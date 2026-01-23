
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Literal, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/books", tags=["books"])

BASE_DIR = Path(__file__).resolve().parent
BOOKS_DIR = BASE_DIR / "books"

# Prefer PS7 for runner; fall back if missing.
PWSH_DEFAULT = r"C:\Program Files\PowerShell\7\pwsh.exe"
RUNNER_DEFAULT = str(BASE_DIR / "run_book_v2.ps1")

MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "2"))
GLOBAL_SEM = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = asyncio.Lock()


def _pick_pwsh() -> str:
    if os.path.exists(PWSH_DEFAULT):
        return PWSH_DEFAULT
    p = shutil.which("pwsh.exe")
    if p:
        return p
    p = shutil.which("powershell.exe")
    return p or "powershell.exe"


def _safe_book_id(book: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", book or ""):
        raise HTTPException(status_code=400, detail="Invalid book id (letters/digits/_-).")
    return book


def _to_ps_bool_str(x: Union[int, bool, str]) -> str:
    if isinstance(x, bool):
        return "True" if x else "False"
    if isinstance(x, int):
        return "True" if x != 0 else "False"
    s = str(x).strip().lower()
    return "True" if s in {"1", "true", "yes", "y", "on"} else "False"


def _jobs_dir(book: str) -> Path:
    d = BOOKS_DIR / book / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job_file(book: str, job_id: str) -> Path:
    return _jobs_dir(book) / f"{job_id}.json"


def _lock_file(book: str) -> Path:
    d = BOOKS_DIR / book
    d.mkdir(parents=True, exist_ok=True)
    return d / "_active.lock"


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_lock(book: str) -> Optional[str]:
    lf = _lock_file(book)
    if not lf.exists():
        return None
    try:
        s = lf.read_text(encoding="utf-8", errors="replace").strip()
        return s or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _try_create_lock(book: str, job_id: str) -> bool:
    lf = _lock_file(book)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(lf), flags)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(job_id)
        return True
    except FileExistsError:
        return False


def _release_lock(book: str, job_id: str) -> None:
    lf = _lock_file(book)
    try:
        if not lf.exists():
            return
        current = lf.read_text(encoding="utf-8", errors="replace").strip()
        if current == job_id:
            lf.unlink(missing_ok=True)
    except Exception:
        pass


class RunReq(BaseModel):
    book: str
    delta: int = Field(3000, ge=100, le=200000)
    model: str = "gpt-4.1-mini"
    max_output_tokens: int = Field(1200, ge=200, le=8000)
    prompt_file: str
    open_qc: Union[int, bool, str] = 0
    open_current: Union[int, bool, str] = 0


class RunResp(BaseModel):
    job_id: str
    status: Literal["QUEUED", "RUNNING", "DONE", "FAILED"]
    book_dir: str
    prompt_file: str
    ps_cmd: list[str]
    ps_rc: Optional[int] = None
    ps_out: Optional[str] = None
    ps_err: Optional[str] = None
    job_file: str
    created_at: str


async def _run_ps(job_id: str) -> None:
    async with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        book = job["book"]

    try:
        async with GLOBAL_SEM:
            async with JOBS_LOCK:
                job = JOBS.get(job_id, job)
                job["status"] = "RUNNING"
                JOBS[job_id] = job
                _atomic_write_json(Path(job["job_file"]), job)

            args = job["ps_cmd"]

            def _do_run():
                return subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )

            p = await asyncio.to_thread(_do_run)
            rc, out, err = p.returncode, p.stdout, p.stderr

            async with JOBS_LOCK:
                job = JOBS.get(job_id, {})
                job["ps_rc"] = rc
                job["ps_out"] = (out or "")[-20000:] if out else ""
                job["ps_err"] = (err or "")[-20000:] if err else ""
                job["status"] = "DONE" if rc == 0 else "FAILED"
                JOBS[job_id] = job
                _atomic_write_json(Path(job["job_file"]), job)
    finally:
        _release_lock(book, job_id)


@router.post("/agent/run", response_model=RunResp)
async def agent_run(req: RunReq):
    book = _safe_book_id(req.book)

    pf = Path(req.prompt_file)
    if not pf.exists():
        raise HTTPException(status_code=400, detail=f"prompt_file not found: {req.prompt_file}")

    runner = RUNNER_DEFAULT
    if not os.path.exists(runner):
        raise HTTPException(status_code=500, detail=f"Runner not found: {runner}")

    active = _read_lock(book)
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "BOOK_BUSY",
                "message": "This book already has an active job (lock file).",
                "job_id": active,
                "status": "QUEUED_OR_RUNNING",
            },
        )

    job_id = uuid.uuid4().hex[:12]
    if not _try_create_lock(book, job_id):
        active2 = _read_lock(book) or "UNKNOWN"
        raise HTTPException(
            status_code=409,
            detail={
                "code": "BOOK_BUSY",
                "message": "This book already has an active job (lock file).",
                "job_id": active2,
                "status": "QUEUED_OR_RUNNING",
            },
        )

    pwsh = _pick_pwsh()
    book_dir = BOOKS_DIR / book
    book_dir.mkdir(parents=True, exist_ok=True)
    jf = _job_file(book, job_id)

    ps_cmd = [
        pwsh, "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", runner,
        "-Book", book,
        "-Delta", str(req.delta),
        "-Model", req.model,
        "-MaxOutputTokens", str(req.max_output_tokens),
        "-PromptFile", str(pf),
        "-OpenQC", _to_ps_bool_str(req.open_qc),
        "-OpenCurrent", _to_ps_bool_str(req.open_current),
    ]

    created_at = datetime.now(timezone.utc).isoformat()

    job = {
        "job_id": job_id,
        "book": book,
        "status": "QUEUED",
        "book_dir": str(book_dir),
        "prompt_file": str(pf),
        "ps_cmd": ps_cmd,
        "ps_rc": None,
        "ps_out": None,
        "ps_err": None,
        "job_file": str(jf),
        "created_at": created_at,
    }

    async with JOBS_LOCK:
        JOBS[job_id] = job
        _atomic_write_json(jf, job)

    asyncio.create_task(_run_ps(job_id))
    return job

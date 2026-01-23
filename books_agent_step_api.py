from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/books/agent", tags=["books-agent"])

ROOT = Path(__file__).resolve().parent
BOOKS_ROOT = ROOT / "books"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, obj: dict) -> None:
    _atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def ensure_book_scaffold(book: str) -> Path:
    book_dir = BOOKS_ROOT / book
    (book_dir / "draft").mkdir(parents=True, exist_ok=True)
    (book_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (book_dir / "jobs").mkdir(parents=True, exist_ok=True)
    (book_dir / "jobs_done").mkdir(parents=True, exist_ok=True)

    for p in [
        book_dir / "draft" / "master.txt",
        book_dir / "draft" / "buffer.txt",
    ]:
        if not p.exists():
            _atomic_write_text(p, "")

    for p in [
        book_dir / "state.json",
        book_dir / "book_bible.json",
    ]:
        if not p.exists():
            _atomic_write_json(p, {})

    return book_dir


class StepReq(BaseModel):
    book: str = Field(..., description="book folder name under books/")
    base_prompt: str = Field("prompts/base_writer_pl.txt", description="relative prompt path from project root")
    intent: str = Field("write_next_scene")
    notes: str = Field("agent_step")
    mode: Literal["buffer", "autonomous"] = Field("buffer", description="buffer=write to buffer.txt, autonomous=write to master.txt")


class StepResp(BaseModel):
    job_id: str
    prompt_file: str
    status: str


@router.post("/step", response_model=StepResp)
def step(req: StepReq):
    # auto-init book folder (żeby nie było 404 "book folder missing")
    book_dir = ensure_book_scaffold(req.book)

    prompts_dir = book_dir / "prompts"
    prompt_name = f"prompt_{_utc_stamp()}_{uuid.uuid4().hex[:8]}.txt"
    prompt_path = prompts_dir / prompt_name

    base_prompt_path = ROOT / req.base_prompt
    if base_prompt_path.exists() and base_prompt_path.is_file():
        prompt_text = base_prompt_path.read_text(encoding="utf-8-sig", errors="replace")
    else:
        prompt_text = "[ROLE: WRITER]\nWrite the next chunk.\n"

    _atomic_write_text(prompt_path, prompt_text)

    jobs_dir = book_dir / "jobs"
    job_id = uuid.uuid4().hex

    rel_prompt = str(prompt_path.relative_to(ROOT)).replace("\\", "/")

    job = {
        "job_id": job_id,
        "book": req.book,
        "intent": req.intent,
        "notes": req.notes,
        "mode": req.mode,
        "base_prompt": req.base_prompt,
        "prompt_file": rel_prompt,
        "status": "QUEUED",
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }

    job_path = jobs_dir / f"{job_id}.json"
    _atomic_write_json(job_path, job)

    return StepResp(job_id=job_id, prompt_file=rel_prompt, status="QUEUED")

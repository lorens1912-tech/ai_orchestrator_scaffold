
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/books", tags=["books"])

BASE_DIR = Path(__file__).resolve().parent
BOOKS_DIR = BASE_DIR / "books"


def _safe_book_id(book: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", book or ""):
        raise HTTPException(status_code=400, detail="Invalid book id (letters/digits/_-).")
    return book


def _read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8", errors="replace"))


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    for book_dir in BOOKS_DIR.glob("*"):
        jf = book_dir / "jobs" / f"{job_id}.json"
        if jf.exists():
            return _read_json(jf)
    raise HTTPException(status_code=404, detail="job not found")


@router.get("/book/{book}/lock")
def get_book_lock(book: str) -> Dict[str, Any]:
    book = _safe_book_id(book)
    lf = BOOKS_DIR / book / "_active.lock"
    if not lf.exists():
        return {"book": book, "locked": False, "lock_path": str(lf), "job_id": None}
    job_id = lf.read_text(encoding="utf-8", errors="replace").strip() or "UNKNOWN"
    return {"book": book, "locked": True, "lock_path": str(lf), "job_id": job_id}


@router.post("/book/{book}/lock/clear")
def clear_book_lock(book: str) -> Dict[str, Any]:
    book = _safe_book_id(book)
    lf = BOOKS_DIR / book / "_active.lock"
    existed = lf.exists()
    if existed:
        lf.unlink(missing_ok=True)
    return {"book": book, "cleared": True, "existed": existed, "lock_path": str(lf)}


@router.get("/book/{book}/jobs/latest")
def latest_job_for_book(book: str) -> Dict[str, Any]:
    book = _safe_book_id(book)
    jobs_dir = BOOKS_DIR / book / "jobs"
    if not jobs_dir.exists():
        raise HTTPException(status_code=404, detail="no jobs dir for this book")

    files = sorted(jobs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise HTTPException(status_code=404, detail="no jobs for this book")

    jf = files[0]
    return {"book": book, "job_file": str(jf), "job": _read_json(jf)}

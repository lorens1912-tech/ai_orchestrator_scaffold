from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

print(f"[RUNS_MANAGE_API] LOADED: {__file__}")

router = APIRouter(prefix="/books", tags=["runs"])

_BOOK_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")
_RUN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,128}$")


def _root_dir() -> Path:
    return Path(__file__).resolve().parent


def _books_dir() -> Path:
    return _root_dir() / "books"


def _validate_book(book: str) -> None:
    if not _BOOK_RE.fullmatch(book or ""):
        raise HTTPException(status_code=400, detail="Invalid book id (allowed: a-z A-Z 0-9 _ - ; max 64).")


def _validate_run_id(run_id: str) -> None:
    if not _RUN_RE.fullmatch(run_id or ""):
        raise HTTPException(status_code=400, detail="Invalid run_id.")


@router.post("/book/{book}/runs/{run_id}/delete")
def runs_delete(book: str, run_id: str) -> Dict[str, Any]:
    """
    UI: usuń wpis timeline.
    Disk:
      deletes books/<book>/runs/<run_id>/
    """
    _validate_book(book)
    _validate_run_id(run_id)

    run_dir = _books_dir() / book / "runs" / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found.")
    if not run_dir.is_dir():
        raise HTTPException(status_code=400, detail="Run path is not a directory.")

    try:
        shutil.rmtree(run_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete run: {e}")

    return {"ok": True, "book": book, "run_id": run_id, "deleted": True}

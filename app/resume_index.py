from __future__ import annotations
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
BOOKS_DIR = ROOT / "books"

def _latest_path(book_id: str) -> Path:
    safe = (book_id or "default").strip()
    return BOOKS_DIR / safe / "_latest_run_id.txt"

def get_latest_run_id(book_id: str) -> Optional[str]:
    p = _latest_path(book_id)
    if not p.exists():
        return None
    try:
        rid = p.read_text(encoding="utf-8").strip()
        return rid or None
    except Exception:
        return None

def set_latest_run_id(book_id: str, run_id: str) -> None:
    p = _latest_path(book_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text((run_id or "").strip() + "\n", encoding="utf-8")
    tmp.replace(p)

from __future__ import annotations
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

ROOT = Path(__file__).resolve().parents[1]
GLOBAL_TRUTH = ROOT / "PROJECT_TRUTH.md"
BOOKS_DIR = ROOT / "books"

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""

def _book_truth_path(book_id: str) -> Path:
    return BOOKS_DIR / book_id / "PROJECT_TRUTH.md"

def get_truth(book_id: Optional[str] = None) -> Dict[str, Any]:
    # book override if exists
    if book_id:
        bp = _book_truth_path(book_id)
        if bp.exists():
            text = _read_text(bp)
            return {
                "scope": "book",
                "book_id": book_id,
                "path": str(bp),
                "sha256": _sha256(text),
                "text": text,
                "loaded_at": datetime.utcnow().isoformat()
            }

    text = _read_text(GLOBAL_TRUTH)
    return {
        "scope": "global",
        "book_id": book_id,
        "path": str(GLOBAL_TRUTH),
        "sha256": _sha256(text),
        "text": text,
        "loaded_at": datetime.utcnow().isoformat()
    }

def build_truth_pack(book_id: Optional[str] = None) -> Dict[str, Any]:
    t = get_truth(book_id)
    # minimal, stable keys for injection
    return {
        "scope": t["scope"],
        "book_id": t["book_id"],
        "sha256": t["sha256"],
        "text": t["text"]
    }

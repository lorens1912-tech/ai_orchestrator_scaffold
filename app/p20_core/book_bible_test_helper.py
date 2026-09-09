from __future__ import annotations

import json
from pathlib import Path

from app.p20_core.book_bible_contract import build_valid_book_bible_payload


def ensure_test_book_bible(book_id: str) -> Path:
    root = Path("books") / book_id
    root.mkdir(parents=True, exist_ok=True)

    path = root / "book_bible.json"
    payload = build_valid_book_bible_payload(book_id)

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

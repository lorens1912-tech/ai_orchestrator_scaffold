
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from llm_client import generate_text


BASE_DIR = Path(__file__).resolve().parents[1]  # ...\ai_orchestrator_scaffold
BOOKS_DIR = BASE_DIR / "books"


def _ensure_book_dir(book: str) -> Path:
    d = BOOKS_DIR / book
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_chapter(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    payload:
      - book: str (required)
      - prompt: str (required)
      - model: str (optional)
      - max_output_tokens: int (optional)
      - target: "master" | "inbox" (optional, default master)
      - mode: "append" | "replace" (optional, default append)
    """
    book = (payload.get("book") or "").strip()
    prompt = payload.get("prompt")

    if not book:
        return {"status": "ERROR", "text": "Brak 'book' w payload", "meta": {}}
    if not prompt or not str(prompt).strip():
        return {"status": "ERROR", "text": "Brak 'prompt' w payload", "meta": {}}

    model = payload.get("model", "gpt-4.1-mini")
    max_output_tokens = int(payload.get("max_output_tokens", 1200))

    target = payload.get("target", "master")
    mode = payload.get("mode", "append")

    book_dir = _ensure_book_dir(book)
    dest = book_dir / ("master.txt" if target == "master" else "inbox.txt")

    text = generate_text(str(prompt), model=model, max_output_tokens=max_output_tokens).strip()

    if mode == "replace":
        dest.write_text(text + "\n", encoding="utf-8", errors="replace")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("a", encoding="utf-8", errors="replace", newline="\n") as f:
            f.write("\n\n" + text)

    return {"status": "OK", "text": text, "meta": {"stage": "WRITING", "book": book, "path": str(dest)}}

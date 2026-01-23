from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

print(f"[RUNS_DETAILS_API] LOADED: {__file__}")

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


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read JSON: {path.name}: {e}")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {path.name}: {e}")


@router.get("/book/{book}/runs/{run_id}")
def runs_get_details(book: str, run_id: str) -> Dict[str, Any]:
    """
    UI: klik w timeline -> szczegóły uruchomienia.

    Disk:
      books/<book>/runs/<run_id>/meta.json
      books/<book>/runs/<run_id>/input.json
      books/<book>/runs/<run_id>/output.json
    """
    _validate_book(book)
    _validate_run_id(run_id)

    run_dir = _books_dir() / book / "runs" / run_id
    meta_path = run_dir / "meta.json"
    input_path = run_dir / "input.json"
    output_path = run_dir / "output.json"

    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Run not found.")

    meta = _read_json(meta_path)
    inputs = _read_json(input_path) if input_path.exists() else {}
    outputs = _read_json(output_path) if output_path.exists() else {}

    # opcjonalnie: jeśli meta.paths zawiera jakieś pliki tekstowe, UI może chcieć je podejrzeć
    paths = meta.get("paths") if isinstance(meta.get("paths"), dict) else {}
    preview: Dict[str, str] = {}

    # Previews tylko dla małych tekstów (md/txt) — bez binarek, bez kombinacji
    for k, p in list(paths.items()):
        if not isinstance(p, str):
            continue
        if not (p.lower().endswith(".md") or p.lower().endswith(".txt")):
            continue
        try:
            fp = Path(p)
            if fp.exists():
                txt = _read_text(fp)
                preview[k] = txt[:5000]  # limit
        except Exception:
            continue

    return {
        "ok": True,
        "book": book,
        "run_id": run_id,
        "meta": meta,
        "inputs": inputs,
        "outputs": outputs,
        "preview": preview,
    }


from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

print(f"[RUNS_POST_API] LOADED: {__file__}")

router = APIRouter(prefix="/books", tags=["runs"])

_BOOK_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")


def _root_dir() -> Path:
    return Path(__file__).resolve().parent


def _books_dir() -> Path:
    return _root_dir() / "books"


def _validate_book(book: str) -> None:
    if not _BOOK_RE.fullmatch(book or ""):
        raise HTTPException(status_code=400, detail="Invalid book id (allowed: a-z A-Z 0-9 _ - ; max 64).")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}_{uuid.uuid4().hex}"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, obj: Any) -> None:
    _atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def _runs_dir(book: str) -> Path:
    d = _books_dir() / book / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _paths(run_dir: Path) -> Dict[str, str]:
    return {
        "dir": str(run_dir),
        "meta": str(run_dir / "meta.json"),
        "input": str(run_dir / "input.json"),
        "output": str(run_dir / "output.json"),
    }


class RunCreateBody(BaseModel):
    role: str = Field(..., description="PISARZ/ARCHITEKT/KRYTYK/KOREKTA/TŁUMACZ/WYDAWCA")
    title: str = Field("RUN", description="Tytuł runa do timeline")
    model: Optional[str] = Field(None, description="Model (opcjonalnie)")
    status: str = Field("SUCCESS", description="SUCCESS / ERROR / DONE / QUEUED / RUNNING")
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = Field(None)
    paths: Dict[str, str] = Field(default_factory=dict, description="Dodatkowe ścieżki do artefaktów (opcjonalnie)")


@router.post("/book/{book}/runs", operation_id="runs_create_post_api")
def runs_create_post(book: str, body: RunCreateBody) -> Dict[str, Any]:
    _validate_book(book)

    run_id = _new_run_id()
    run_dir = _runs_dir(book) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    ts = _utc_now_iso()
    p = _paths(run_dir)
    if body.paths:
        p.update({k: str(v) for k, v in body.paths.items()})

    meta = {
        "run_id": run_id,
        "book": book,
        "role": body.role,
        "title": body.title,
        "model": body.model,
        "status": body.status,
        "ts_start": ts,
        "ts_end": ts,
        "error": body.error,
        "paths": p,
    }

    _atomic_write_json(run_dir / "meta.json", meta)
    _atomic_write_json(run_dir / "input.json", body.inputs or {})
    _atomic_write_json(run_dir / "output.json", body.outputs or {})

    return {"ok": True, "book": book, "run_id": run_id, "meta": meta}

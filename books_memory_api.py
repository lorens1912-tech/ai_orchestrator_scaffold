
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, List, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/books", tags=["books-memory"])

_DEFAULT_BOOKS_ROOT = (Path(__file__).resolve().parent / "books").resolve()
BOOKS_ROOT = Path(os.getenv("BOOKS_ROOT", str(_DEFAULT_BOOKS_ROOT))).resolve()

BOOK_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")


def _utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _require_valid_book(book: str) -> None:
    if not BOOK_ID_RE.match(book or ""):
        raise HTTPException(status_code=400, detail={"code": "INVALID_BOOK_ID"})


def _book_dir(book: str) -> Path:
    _require_valid_book(book)
    p = (BOOKS_ROOT / book).resolve()
    if str(p).lower().startswith(str(BOOKS_ROOT).lower()) is False:
        raise HTTPException(status_code=400, detail={"code": "INVALID_PATH"})
    return p


def _safe_resolve_under(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if str(target).lower().startswith(str(root).lower()) is False:
        raise HTTPException(status_code=400, detail={"code": "INVALID_PATH"})
    return target


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", errors="strict")
    tmp.replace(path)


def _atomic_write_json(path: Path, data: Any) -> None:
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _read_text(path: Path, tail_lines: Optional[int] = None) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if tail_lines is None:
        return text
    if tail_lines < 0 or tail_lines > 50_000:
        raise HTTPException(status_code=400, detail={"code": "INVALID_TAIL_LINES"})
    if tail_lines == 0:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-tail_lines:]) + ("\n" if text.endswith("\n") else "")


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail={"code": "JSON_CORRUPT", "path": str(path), "message": str(e)},
        )


class Meta(BaseModel):
    path: str
    exists: bool
    size_bytes: int = 0
    modified_utc: Optional[str] = None


class JsonResp(BaseModel):
    meta: Meta
    data: Any = None


class TextResp(BaseModel):
    meta: Meta
    text: str = ""


class WriteJson(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)


class WriteText(BaseModel):
    text: str


def _meta(path: Path) -> Meta:
    if not path.exists():
        return Meta(path=str(path), exists=False)
    st = path.stat()
    return Meta(
        path=str(path),
        exists=True,
        size_bytes=st.st_size,
        modified_utc=_utc_iso(st.st_mtime),
    )


def _mem_path(book: str, name: str) -> Path:
    bdir = _book_dir(book)
    return _safe_resolve_under(bdir, f"memory/{name}")


# =========================
#  CANON / DECISIONS / BANS / SUMMARY
# =========================

@router.get("/book/{book}/memory/canon", response_model=JsonResp)
def get_canon(book: str):
    p = _mem_path(book, "canon.json")
    return JsonResp(meta=_meta(p), data=_read_json(p))


@router.post("/book/{book}/memory/canon", response_model=JsonResp)
def set_canon(book: str, payload: WriteJson):
    p = _mem_path(book, "canon.json")
    _atomic_write_json(p, payload.data)
    return JsonResp(meta=_meta(p), data=_read_json(p))


@router.get("/book/{book}/memory/decisions", response_model=JsonResp)
def get_decisions(book: str):
    p = _mem_path(book, "decisions.json")
    return JsonResp(meta=_meta(p), data=_read_json(p))


@router.post("/book/{book}/memory/decisions", response_model=JsonResp)
def set_decisions(book: str, payload: WriteJson):
    p = _mem_path(book, "decisions.json")
    _atomic_write_json(p, payload.data)
    return JsonResp(meta=_meta(p), data=_read_json(p))


@router.get("/book/{book}/memory/bans", response_model=JsonResp)
def get_bans(book: str):
    p = _mem_path(book, "bans.json")
    return JsonResp(meta=_meta(p), data=_read_json(p))


@router.post("/book/{book}/memory/bans", response_model=JsonResp)
def set_bans(book: str, payload: WriteJson):
    p = _mem_path(book, "bans.json")
    _atomic_write_json(p, payload.data)
    return JsonResp(meta=_meta(p), data=_read_json(p))


@router.get("/book/{book}/memory/summary", response_model=TextResp)
def get_summary(book: str, tail_lines: int = Query(default=800)):
    p = _mem_path(book, "summary.md")
    return TextResp(meta=_meta(p), text=_read_text(p, tail_lines=tail_lines))


@router.post("/book/{book}/memory/summary", response_model=TextResp)
def set_summary(book: str, payload: WriteText):
    p = _mem_path(book, "summary.md")
    _atomic_write_text(p, payload.text)
    return TextResp(meta=_meta(p), text=_read_text(p, tail_lines=None))


# =========================
#  NOTES (NADZÓR) -> memory/notes.json
# =========================

LocatorKind = Literal["PHRASE", "PARAGRAPH", "SCENE", "CHARACTER"]
Severity = Literal["HARD", "SOFT"]
Scope = Literal["GLOBAL", "LOCAL"]


class NoteLocator(BaseModel):
    kind: LocatorKind
    value: str
    occurrence: int = 1
    para_index: Optional[int] = None


class NoteAdd(BaseModel):
    type: str
    severity: Severity = "SOFT"
    scope: Scope = "GLOBAL"
    locator: Optional[NoteLocator] = None
    text: str
    tags: List[str] = Field(default_factory=list)


class Note(BaseModel):
    id: str
    type: str
    severity: Severity
    scope: Scope
    locator: Optional[NoteLocator] = None
    text: str
    tags: List[str] = Field(default_factory=list)
    created_utc: str
    updated_utc: str


class NotesReplace(BaseModel):
    notes: List[Note] = Field(default_factory=list)


def _notes_path(book: str) -> Path:
    return _mem_path(book, "notes.json")


def _read_notes(book: str) -> List[dict]:
    p = _notes_path(book)
    data = _read_json(p)
    if data is None:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    raise HTTPException(status_code=500, detail={"code": "NOTES_CORRUPT", "path": str(p)})


def _write_notes(book: str, notes: List[dict]) -> None:
    p = _notes_path(book)
    _atomic_write_json(p, notes)


@router.get("/book/{book}/memory/notes", response_model=JsonResp)
def get_notes(book: str):
    p = _notes_path(book)
    return JsonResp(meta=_meta(p), data=_read_notes(book))


@router.post("/book/{book}/memory/notes", response_model=JsonResp)
def replace_notes(book: str, payload: NotesReplace):
    p = _notes_path(book)
    notes = [n.model_dump() for n in payload.notes]
    _write_notes(book, notes)
    return JsonResp(meta=_meta(p), data=_read_notes(book))


@router.post("/book/{book}/memory/notes/add", response_model=JsonResp)
def add_note(book: str, payload: NoteAdd):
    p = _notes_path(book)
    now = _utc_iso(datetime.now(timezone.utc).timestamp())

    notes = _read_notes(book)
    note = Note(
        id=str(uuid.uuid4()),
        type=payload.type,
        severity=payload.severity,
        scope=payload.scope,
        locator=payload.locator,
        text=payload.text,
        tags=payload.tags,
        created_utc=now,
        updated_utc=now,
    ).model_dump()

    notes.append(note)
    _write_notes(book, notes)
    return JsonResp(meta=_meta(p), data=_read_notes(book))


@router.post("/book/{book}/memory/notes/{note_id}/delete", response_model=JsonResp)
def delete_note(book: str, note_id: str):
    p = _notes_path(book)
    notes = _read_notes(book)
    new_notes = [n for n in notes if str(n.get("id")) != note_id]
    _write_notes(book, new_notes)
    return JsonResp(meta=_meta(p), data=_read_notes(book))


# =========================
#  BULK MEMORY (pod aplikację: jeden przycisk ZAPISZ)
#  GET/POST /books/book/{book}/memory/bulk
# =========================

class BulkMemoryResp(BaseModel):
    canon: JsonResp
    decisions: JsonResp
    bans: JsonResp
    summary: TextResp
    notes: JsonResp


class BulkMemoryWrite(BaseModel):
    canon: Optional[Dict[str, Any]] = None
    decisions: Optional[Dict[str, Any]] = None
    bans: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    # notes opcjonalne: jeśli nie podasz, nie ruszamy notes.json
    notes: Optional[List[Note]] = None


def _bulk_read(book: str) -> BulkMemoryResp:
    p_canon = _mem_path(book, "canon.json")
    p_dec = _mem_path(book, "decisions.json")
    p_bans = _mem_path(book, "bans.json")
    p_sum = _mem_path(book, "summary.md")
    p_notes = _mem_path(book, "notes.json")

    canon = JsonResp(meta=_meta(p_canon), data=_read_json(p_canon))
    decisions = JsonResp(meta=_meta(p_dec), data=_read_json(p_dec))
    bans = JsonResp(meta=_meta(p_bans), data=_read_json(p_bans))
    summary = TextResp(meta=_meta(p_sum), text=_read_text(p_sum, tail_lines=None))
    notes = JsonResp(meta=_meta(p_notes), data=_read_notes(book))

    return BulkMemoryResp(
        canon=canon,
        decisions=decisions,
        bans=bans,
        summary=summary,
        notes=notes,
    )


@router.get("/book/{book}/memory/bulk", response_model=BulkMemoryResp)
def get_memory_bulk(book: str):
    return _bulk_read(book)


@router.post("/book/{book}/memory/bulk", response_model=BulkMemoryResp)
def set_memory_bulk(book: str, payload: BulkMemoryWrite):
    # zapisujemy tylko to, co przyszło (żeby UI mogło robić partial update)
    if payload.canon is not None:
        _atomic_write_json(_mem_path(book, "canon.json"), payload.canon)

    if payload.decisions is not None:
        _atomic_write_json(_mem_path(book, "decisions.json"), payload.decisions)

    if payload.bans is not None:
        _atomic_write_json(_mem_path(book, "bans.json"), payload.bans)

    if payload.summary is not None:
        _atomic_write_text(_mem_path(book, "summary.md"), payload.summary)

    if payload.notes is not None:
        notes_list = [n.model_dump() for n in payload.notes]
        _write_notes(book, notes_list)

    return _bulk_read(book)

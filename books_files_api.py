
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/books", tags=["books-files"])

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


def _read_text_file(path: Path, tail_lines: Optional[int] = None, tail_bytes: Optional[int] = None) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if tail_bytes is not None:
        if tail_bytes < 0 or tail_bytes > 5_000_000:
            raise HTTPException(status_code=400, detail={"code": "INVALID_TAIL_BYTES"})
        if tail_bytes == 0:
            return ""
        return text[-tail_bytes:]
    if tail_lines is not None:
        if tail_lines < 0 or tail_lines > 50_000:
            raise HTTPException(status_code=400, detail={"code": "INVALID_TAIL_LINES"})
        if tail_lines == 0:
            return ""
        lines = text.splitlines()
        return "\n".join(lines[-tail_lines:]) + ("\n" if text.endswith("\n") else "")
    return text


class FileReadResponse(BaseModel):
    path: str
    exists: bool
    size_bytes: int = 0
    modified_utc: Optional[str] = None
    content: str = ""


class InboxFileItem(BaseModel):
    name: str
    rel_path: str
    size_bytes: int
    modified_utc: str


class InboxListResponse(BaseModel):
    book: str
    inbox_dir: str
    files: List[InboxFileItem] = Field(default_factory=list)


@router.get("/book/{book}/master", response_model=FileReadResponse)
def get_master(book: str, tail_lines: Optional[int] = Query(default=None), tail_bytes: Optional[int] = Query(default=None)):
    bdir = _book_dir(book)
    master_path = _safe_resolve_under(bdir, "draft/master.txt")

    if not master_path.exists():
        return FileReadResponse(path=str(master_path), exists=False, content="")

    st = master_path.stat()
    content = _read_text_file(master_path, tail_lines=tail_lines, tail_bytes=tail_bytes)
    return FileReadResponse(
        path=str(master_path),
        exists=True,
        size_bytes=st.st_size,
        modified_utc=_utc_iso(st.st_mtime),
        content=content,
    )


@router.get("/book/{book}/inbox", response_model=InboxListResponse)
def list_inbox(book: str):
    bdir = _book_dir(book)
    inbox_dir = _safe_resolve_under(bdir, "inbox")
    inbox_dir.mkdir(parents=True, exist_ok=True)

    items: List[InboxFileItem] = []
    for p in sorted(inbox_dir.glob("**/*")):
        if not p.is_file():
            continue
        st = p.stat()
        rel = p.relative_to(bdir).as_posix()
        items.append(
            InboxFileItem(
                name=p.name,
                rel_path=rel,
                size_bytes=st.st_size,
                modified_utc=_utc_iso(st.st_mtime),
            )
        )

    return InboxListResponse(book=book, inbox_dir=str(inbox_dir), files=items)


@router.get("/book/{book}/inbox/{name}", response_model=FileReadResponse)
def read_inbox_file(book: str, name: str, tail_lines: Optional[int] = Query(default=None), tail_bytes: Optional[int] = Query(default=None)):
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail={"code": "INVALID_NAME"})

    bdir = _book_dir(book)
    inbox_path = _safe_resolve_under(bdir, f"inbox/{name}")

    if not inbox_path.exists():
        return FileReadResponse(path=str(inbox_path), exists=False, content="")

    st = inbox_path.stat()
    content = _read_text_file(inbox_path, tail_lines=tail_lines, tail_bytes=tail_bytes)
    return FileReadResponse(
        path=str(inbox_path),
        exists=True,
        size_bytes=st.st_size,
        modified_utc=_utc_iso(st.st_mtime),
        content=content,
    )


@router.get("/book/{book}/notes", response_model=FileReadResponse)
def get_notes(book: str, tail_lines: Optional[int] = Query(default=None), tail_bytes: Optional[int] = Query(default=None)):
    bdir = _book_dir(book)
    notes_path = _safe_resolve_under(bdir, "memory/summary.md")

    if not notes_path.exists():
        return FileReadResponse(path=str(notes_path), exists=False, content="")

    st = notes_path.stat()
    content = _read_text_file(notes_path, tail_lines=tail_lines, tail_bytes=tail_bytes)
    return FileReadResponse(
        path=str(notes_path),
        exists=True,
        size_bytes=st.st_size,
        modified_utc=_utc_iso(st.st_mtime),
        content=content,
    )


# =========================
# SEARCH w master.txt (pod aplikację)
# POST /books/book/{book}/master/search
# =========================

class MasterSearchRequest(BaseModel):
    q: str = Field(..., description="Fraza do wyszukania (min 1 znak).")
    limit: int = Field(default=10, ge=1, le=200, description="Maksymalna liczba wyników.")
    case_sensitive: bool = Field(default=False, description="Czy rozróżniać wielkość liter.")
    in_paragraphs: bool = Field(default=True, description="Szukaj w akapitach (rozdzielonych pustą linią).")


class MasterSearchHit(BaseModel):
    para_index: int = Field(..., ge=1, description="Numer akapitu (1-based).")
    match_index: int = Field(..., ge=0, description="Pozycja znaku w akapicie (0-based).")
    excerpt: str = Field(..., description="Krótki fragment z kontekstem.")


class MasterSearchResponse(BaseModel):
    path: str
    exists: bool
    total_hits: int = 0
    hits: List[MasterSearchHit] = Field(default_factory=list)


def _split_paragraphs(text: str) -> List[str]:
    parts = re.split(r"(?:\r?\n){2,}", text)
    return [p for p in parts if p.strip()]


@router.post("/book/{book}/master/search", response_model=MasterSearchResponse)
def master_search(book: str, payload: MasterSearchRequest):
    bdir = _book_dir(book)
    master_path = _safe_resolve_under(bdir, "draft/master.txt")

    if not master_path.exists():
        return MasterSearchResponse(path=str(master_path), exists=False, total_hits=0, hits=[])

    raw = master_path.read_text(encoding="utf-8", errors="replace")
    q = (payload.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail={"code": "EMPTY_QUERY"})

    haystack = raw
    needle = q

    if not payload.case_sensitive:
        haystack = haystack.lower()
        needle = needle.lower()

    hits: List[MasterSearchHit] = []
    total_hits = 0

    if payload.in_paragraphs:
        paras = _split_paragraphs(raw)
        paras_cmp = _split_paragraphs(haystack)

        for i, (p_raw, p_cmp) in enumerate(zip(paras, paras_cmp), start=1):
            start = 0
            while True:
                idx = p_cmp.find(needle, start)
                if idx < 0:
                    break
                total_hits += 1

                a = max(0, idx - 120)
                b = min(len(p_raw), idx + len(q) + 120)
                excerpt = p_raw[a:b].replace("\r", "")

                if len(hits) < payload.limit:
                    hits.append(MasterSearchHit(para_index=i, match_index=idx, excerpt=excerpt))

                start = idx + max(1, len(needle))

                if len(hits) >= payload.limit and total_hits >= payload.limit:
                    break
            if len(hits) >= payload.limit and total_hits >= payload.limit:
                break
    else:
        start = 0
        while True:
            idx = haystack.find(needle, start)
            if idx < 0:
                break
            total_hits += 1

            a = max(0, idx - 120)
            b = min(len(raw), idx + len(q) + 120)
            excerpt = raw[a:b].replace("\r", "")

            if len(hits) < payload.limit:
                hits.append(MasterSearchHit(para_index=1, match_index=idx, excerpt=excerpt))

            start = idx + max(1, len(needle))

            if len(hits) >= payload.limit and total_hits >= payload.limit:
                break

    return MasterSearchResponse(path=str(master_path), exists=True, total_hits=total_hits, hits=hits)

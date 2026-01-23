
# books_runs_query_api.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

# DZIAŁAJĄCY endpoint pod UI:
# /books/book/{book}/runs_query?limit=...&offset=...&role=...&status=...&q=...
router = APIRouter(prefix="/books/book/{book}/runs_query", tags=["runs"])

ROOT_DIR = Path(__file__).resolve().parent
BOOKS_DIR = (ROOT_DIR / "books").resolve()


class RunItem(BaseModel):
    run_id: str
    role: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None
    model: Optional[str] = None
    created_at: Optional[str] = None
    preview: str = ""
    # UWAGA: paths tylko RELATIVE (bez C:\...)
    paths: Dict[str, Any] = {}
    primary_md: str = ""


class RunsQueryResponse(BaseModel):
    ok: bool
    book: str
    total: int
    limit: int
    offset: int
    items: List[RunItem]


def _book_root(book: str) -> Path:
    if not book or "/" in book or "\\" in book or ".." in book:
        raise HTTPException(status_code=422, detail="Invalid book id")
    return (BOOKS_DIR / book).resolve()


def _safe_resolve_under(root: Path, rel_path: str) -> Path:
    if not rel_path or rel_path.startswith(("/", "\\")):
        raise HTTPException(status_code=422, detail="Invalid rel_path")
    candidate = (root / Path(rel_path)).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise HTTPException(status_code=403, detail="Path escapes book root")
    return candidate


def _read_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _extract_preview(output_json: Dict[str, Any]) -> str:
    for key in ("preview", "summary", "message", "status_message"):
        v = output_json.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _normalize_paths(book: str, paths: Dict[str, Any]) -> Dict[str, Any]:
    """
    Meta.paths może mieć absolutne ścieżki Windows (C:\...).
    UI ma dostać WYŁĄCZNIE RELATIVE względem books/<book>/.
    """
    if not isinstance(paths, dict):
        return {}

    out: Dict[str, Any] = {}
    needle1 = f"\\books\\{book}\\"
    needle2 = f"/books/{book}/"

    for k, v in paths.items():
        if not isinstance(v, str) or not v.strip():
            continue

        s = v.strip()

        # 1) jeżeli już wygląda na relatywną: (bez drive letter i bez / na początku)
        if not (len(s) >= 3 and s[1:3] in [":\\", ":/"]) and not s.startswith(("/", "\\")):
            out[k] = s.replace("\\", "/")
            continue

        # 2) Windows absolute -> wytnij od \books\<book>\
        s_win = s.replace("/", "\\")
        idx = s_win.lower().find(needle1.lower())
        if idx >= 0:
            rel = s_win[idx + len(needle1) :].replace("\\", "/")
            out[k] = rel
            continue

        # 3) POSIX absolute -> wytnij od /books/<book>/
        idx2 = s.lower().find(needle2.lower())
        if idx2 >= 0:
            rel = s[idx2 + len(needle2) :].replace("\\", "/")
            out[k] = rel
            continue

        # 4) nie umiemy bezpiecznie zrelatywizować -> nie wysyłamy do UI
        continue

    return out


def _pick_primary_md(paths: Dict[str, Any]) -> str:
    # preferuj klucze typowo md, ale obsłuż dowolne
    for key in ("md", "report_md", "path_md", "latest_md"):
        v = paths.get(key)
        if isinstance(v, str) and v.lower().endswith(".md"):
            return v
    for v in paths.values():
        if isinstance(v, str) and v.lower().endswith(".md"):
            return v
    return ""


@router.get("", response_model=RunsQueryResponse)
def query_runs(
    book: str,
    role: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Search in role/title/preview"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    book_root = _book_root(book)
    if not book_root.exists():
        raise HTTPException(status_code=404, detail="Book not found")

    runs_root = _safe_resolve_under(book_root, "runs")
    if not runs_root.exists():
        return RunsQueryResponse(ok=True, book=book, total=0, limit=limit, offset=offset, items=[])

    run_dirs = [d for d in runs_root.iterdir() if d.is_dir()]

    def _sort_key(d: Path):
        name = d.name
        try:
            mtime = d.stat().st_mtime
        except Exception:
            mtime = 0.0
        return (name, mtime)

    run_dirs.sort(key=_sort_key, reverse=True)

    q_l = (q or "").strip().lower()

    items: List[RunItem] = []
    for d in run_dirs:
        run_id = d.name
        meta = _read_json(d / "meta.json")
        outj = _read_json(d / "output.json")

        paths_raw = meta.get("paths")
        paths_rel = _normalize_paths(book, paths_raw) if isinstance(paths_raw, dict) else {}

        item = RunItem(
            run_id=run_id,
            role=meta.get("role"),
            title=meta.get("title"),
            status=meta.get("status"),
            model=meta.get("model"),
            created_at=meta.get("created_at") or meta.get("ts") or meta.get("timestamp"),
            preview=_extract_preview(outj),
            paths=paths_rel,
            primary_md=_pick_primary_md(paths_rel),
        )

        if role and (item.role or "").lower() != role.lower():
            continue
        if status and (item.status or "").lower() != status.lower():
            continue
        if q_l:
            hay = f"{item.role or ''} {item.title or ''} {item.preview or ''}".lower()
            if q_l not in hay:
                continue

        items.append(item)

    total = len(items)
    sliced = items[offset : offset + limit]

    return RunsQueryResponse(ok=True, book=book, total=total, limit=limit, offset=offset, items=sliced)

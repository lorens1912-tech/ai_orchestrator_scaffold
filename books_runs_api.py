from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from pathlib import Path
from typing import Any, Dict, List, Optional

from books_core import safe_book_root, safe_resolve_under, read_text_safe

router = APIRouter(prefix="/books/book/{book}", tags=["books.runs"])

class RunItem(BaseModel):
    run_id: str
    role: str
    title: str
    status: str
    created_at: str
    preview: str
    paths: Dict[str, str]

class RunsListResp(BaseModel):
    ok: bool
    items: List[RunItem]
    total: int

class RunDetailResp(BaseModel):
    ok: bool
    meta: Dict[str, Any]
    input: Any
    output: Any

def _load_json(path: Path) -> Any:
    import json
    return json.loads(read_text_safe(path))

def _preview_from_output(output_json: Any, max_len: int = 240) -> str:
    if isinstance(output_json, dict):
        for k in ("preview", "summary", "note", "result"):
            v = output_json.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()[:max_len]
    return ""

def _iter_runs(root: Path) -> List[Dict[str, Any]]:
    runs_dir = root / "runs"
    if not runs_dir.exists():
        return []
    out: List[Dict[str, Any]] = []
    for run_folder in runs_dir.iterdir():
        if not run_folder.is_dir():
            continue
        meta_path = run_folder / "meta.json"
        input_path = run_folder / "input.json"
        output_path = run_folder / "output.json"
        if not meta_path.exists():
            continue
        try:
            meta = _load_json(meta_path)
        except Exception:
            continue

        output_obj = None
        try:
            if output_path.exists():
                output_obj = _load_json(output_path)
        except Exception:
            output_obj = None

        out.append({
            "run_id": meta.get("run_id", run_folder.name),
            "role": meta.get("role", ""),
            "title": meta.get("title", ""),
            "status": meta.get("status", ""),
            "created_at": meta.get("created_at", ""),
            "paths": meta.get("paths", {
                "meta": str(meta_path.relative_to(root)),
                "input": str(input_path.relative_to(root)),
                "output": str(output_path.relative_to(root)),
            }),
            "preview": _preview_from_output(output_obj) if output_obj is not None else "",
        })
    out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return out

@router.get("/runs_query", response_model=RunsListResp)
def runs_query(
    book: str,
    q: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    root = safe_book_root(book)
    items = _iter_runs(root)

    if q:
        qq = q.lower()
        items = [x for x in items if (qq in (x.get("title","").lower() + " " + x.get("status","").lower() + " " + x.get("role","").lower()))]
    if status:
        items = [x for x in items if x.get("status") == status]
    if role:
        items = [x for x in items if x.get("role") == role]

    total = len(items)
    sliced = items[offset: offset + limit]
    return {"ok": True, "items": sliced, "total": total}

@router.get("/runs", response_model=RunsListResp)
def runs_list(book: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    root = safe_book_root(book)
    items = _iter_runs(root)
    total = len(items)
    sliced = items[offset: offset + limit]
    return {"ok": True, "items": sliced, "total": total}

@router.get("/runs/{run_id}", response_model=RunDetailResp)
def run_detail(book: str, run_id: str):
    root = safe_book_root(book)
    run_dir = safe_resolve_under(root, f"runs/{run_id}")
    if not run_dir.exists() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="run not found")

    meta_path = run_dir / "meta.json"
    input_path = run_dir / "input.json"
    output_path = run_dir / "output.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="meta.json missing")

    meta = _load_json(meta_path)

    input_obj: Any = {}
    output_obj: Any = {}
    try:
        if input_path.exists():
            input_obj = _load_json(input_path)
    except Exception:
        input_obj = {"ok": True, "stub": True, "note": "input parse failed"}

    try:
        if output_path.exists():
            output_obj = _load_json(output_path)
    except Exception:
        output_obj = {"ok": True, "stub": True, "note": "output parse failed"}

    return {"ok": True, "meta": meta, "input": input_obj, "output": output_obj}

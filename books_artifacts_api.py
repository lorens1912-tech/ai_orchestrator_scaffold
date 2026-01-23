from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Any, Dict

from books_core import safe_book_root, safe_resolve_under, read_text_safe

router = APIRouter(prefix="/books/book/{book}/artifacts", tags=["books.artifacts"])

class ArtifactReadResp(BaseModel):
    ok: bool
    path: str
    content: str

class ArtifactLatestResp(BaseModel):
    ok: bool
    kind: str
    paths: Dict[str, str]
    md: str
    json_data: Any
    raw: str

@router.get("/read", response_model=ArtifactReadResp)
def read_artifact(book: str, path: str = Query(..., description="Relative path under book root")):
    root = safe_book_root(book)
    target = safe_resolve_under(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    try:
        content = read_text_safe(target)
    except Exception as e:
        raise HTTPException(status_code=415, detail=f"cannot read as text: {e!r}")
    return {"ok": True, "path": path, "content": content}

@router.get("/latest", response_model=ArtifactLatestResp)
def latest_artifact(book: str, kind: str = Query(...)):
    root = safe_book_root(book)
    analysis = root / "analysis"

    md_path = analysis / f"{kind}_latest.md"
    json_path = analysis / f"{kind}_latest.json"
    raw_path = analysis / f"{kind}_latest.raw"

    md = read_text_safe(md_path) if md_path.exists() else f"(stub) No latest md for kind='{kind}'"
    raw = read_text_safe(raw_path) if raw_path.exists() else md

    json_obj: Any = {"ok": True, "stub": True, "kind": kind}
    if json_path.exists():
        try:
            import json as _json
            json_obj = _json.loads(read_text_safe(json_path))
        except Exception:
            json_obj = {"ok": True, "stub": True, "kind": kind, "note": "json parse failed -> stub"}

    paths = {
        "md": str((analysis / f"{kind}_latest.md").relative_to(root)),
        "json": str((analysis / f"{kind}_latest.json").relative_to(root)),
        "raw": str((analysis / f"{kind}_latest.raw").relative_to(root)),
    }
    return {"ok": True, "kind": kind, "paths": paths, "md": md, "json_data": json_obj, "raw": raw}

from __future__ import annotations

from typing import Any, Dict
from fastapi import APIRouter
from pydantic import BaseModel

from app.canon_store import load_canon, patch_canon

router = APIRouter(prefix="/books", tags=["canon"])


class CanonPatch(BaseModel):
    upsert: Dict[str, Any] = {}
    remove: Dict[str, Any] = {}


@router.get("/{book_id}/canon")
def get_canon(book_id: str) -> Dict[str, Any]:
    return load_canon(book_id)


@router.patch("/{book_id}/canon")
def patch_canon_endpoint(book_id: str, body: CanonPatch) -> Dict[str, Any]:
    return patch_canon(book_id, body.model_dump() or {})

from __future__ import annotations
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.project_truth_store import get_truth

router = APIRouter(prefix="/project_truth", tags=["project_truth"])

class TruthResponse(BaseModel):
    scope: str
    book_id: str | None = None
    path: str
    sha256: str
    text: str
    loaded_at: str

@router.get("", response_model=TruthResponse)
def project_truth(book_id: str | None = Query(default=None)):
    return get_truth(book_id)

from __future__ import annotations

from fastapi import APIRouter, Body, Query
from typing import Any, Dict, Optional

from app.canon_store import load_canon, patch_canon
from app.canon_check import canon_check

router = APIRouter()

# ---------------------------
# GET /canon variants
# ---------------------------

@router.get("/canon")
@router.get("/canon/get")
def canon_get_q(book_id: str = Query(default="default")) -> Dict[str, Any]:
    canon = load_canon(run_dir=None, book_id=book_id)
    return {"ok": True, "book_id": book_id, "canon": canon}


@router.get("/canon/{book_id}")
def canon_get_p(book_id: str) -> Dict[str, Any]:
    canon = load_canon(run_dir=None, book_id=book_id)
    return {"ok": True, "book_id": book_id, "canon": canon}

# ---------------------------
# PATCH /canon variants
# ---------------------------

@router.patch("/canon")
@router.post("/canon/patch")
def canon_patch_q(
    book_id: str = Query(default="default"),
    patch: Dict[str, Any] = Body(default_factory=dict),
) -> Dict[str, Any]:
    canon = patch_canon(run_dir=None, patch=patch, book_id=book_id)
    return {"ok": True, "book_id": book_id, "canon": canon}


@router.patch("/canon/{book_id}")
def canon_patch_p(
    book_id: str,
    patch: Dict[str, Any] = Body(default_factory=dict),
) -> Dict[str, Any]:
    canon = patch_canon(run_dir=None, patch=patch, book_id=book_id)
    return {"ok": True, "book_id": book_id, "canon": canon}

# ---------------------------
# CHECK (dla test_111 ledger mismatch)
# ---------------------------

@router.post("/canon/check")
@router.post("/canon/check_flags")
def canon_check_ep(
    payload: Dict[str, Any] = Body(default_factory=dict),
    book_id: str = Query(default="default"),
) -> Dict[str, Any]:
    # book_id może przyjść w query albo w body
    bid = str(payload.get("book_id") or book_id or "default")
    text = str(payload.get("text") or "")
    scene_ref = str(payload.get("scene_ref") or "")

    canon = load_canon(run_dir=None, book_id=bid)
    res = canon_check(text, canon, scene_ref=scene_ref)

    # testy zwykle oczekują {ok, issues, scene_ref}
    return {"ok": True, "book_id": bid, "result": res}

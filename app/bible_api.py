from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .run_lock import acquire_book_lock
from .run_store import atomic_write_json

router = APIRouter(prefix="/books", tags=["books"])

def _safe_book_id(book_id: str) -> str:
    b = (book_id or "").strip()
    if not b:
        return "default"
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", b)

def _root() -> Path:
    return Path(__file__).resolve().parents[1]

def _bible_path(book_id: str) -> Path:
    root = _root()
    bid = _safe_book_id(book_id)
    p = root / "books" / bid / "book_bible.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def _default_bible(book_id: str) -> Dict[str, Any]:
    return {
        "book_id": _safe_book_id(book_id),
        "title": "",
        "canon": {"characters": [], "locations": [], "timeline": [], "rules_of_world": []},
        "continuity_rules": {"flag_unknown_entities": True, "force_unknown_entities": False},
        "meta": {"version": 1},
    }

def _read_or_init(book_id: str) -> Dict[str, Any]:
    p = _bible_path(book_id)
    if not p.exists():
        b = _default_bible(book_id)
        atomic_write_json(p, b)
        return b
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cannot read bible: {e}")

class Character(BaseModel):
    name: str = Field(..., min_length=1)
    aliases: List[str] = Field(default_factory=list)

class Bible(BaseModel):
    book_id: str = Field(..., min_length=1)
    title: str = ""
    canon: Dict[str, Any] = Field(default_factory=dict)
    continuity_rules: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)

class PatchCharacters(BaseModel):
    add: List[Character] = Field(default_factory=list)
    remove_names: List[str] = Field(default_factory=list)

@router.get("/{book_id}/bible")
def get_bible(book_id: str):
    bid = _safe_book_id(book_id)
    with acquire_book_lock(bid):
        return _read_or_init(bid)

@router.put("/{book_id}/bible")
def put_bible(book_id: str, bible: Bible):
    bid = _safe_book_id(book_id)
    data = bible.model_dump()
    data["book_id"] = bid
    with acquire_book_lock(bid):
        atomic_write_json(_bible_path(bid), data)
    return {"ok": True, "book_id": bid}

@router.patch("/{book_id}/bible/characters")
def patch_characters(book_id: str, patch: PatchCharacters):
    bid = _safe_book_id(book_id)
    with acquire_book_lock(bid):
        b = _read_or_init(bid)
        canon = b.get("canon") if isinstance(b.get("canon"), dict) else {}
        chars = canon.get("characters") or []
        if not isinstance(chars, list):
            chars = []

        # normalize existing to dict
        norm: List[Dict[str, Any]] = []
        for c in chars:
            if isinstance(c, str):
                norm.append({"name": c, "aliases": []})
            elif isinstance(c, dict):
                nm = str(c.get("name") or "").strip()
                if nm:
                    norm.append({"name": nm, "aliases": list(c.get("aliases") or [])})
        # remove
        remove_set = {x.strip() for x in patch.remove_names if x.strip()}
        norm = [c for c in norm if c.get("name") not in remove_set]

        # add/merge
        by_name = {c["name"]: c for c in norm if c.get("name")}
        for c in patch.add:
            nm = c.name.strip()
            if nm in by_name:
                # merge aliases
                exist = set(by_name[nm].get("aliases") or [])
                for a in c.aliases:
                    a2 = a.strip()
                    if a2:
                        exist.add(a2)
                by_name[nm]["aliases"] = sorted(exist)
            else:
                by_name[nm] = {"name": nm, "aliases": sorted({a.strip() for a in c.aliases if a.strip()})}

        canon["characters"] = list(by_name.values())
        b["canon"] = canon

        atomic_write_json(_bible_path(bid), b)

    return {"ok": True, "book_id": bid, "characters_count": len(b["canon"]["characters"])}

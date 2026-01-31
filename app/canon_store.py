from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
CANON_VERSION = 1


def _iso() -> str:
    return datetime.utcnow().isoformat()


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def canon_dir(book_id: str) -> Path:
    bid = str(book_id or "default").strip() or "default"
    return ROOT / "books" / bid / "canon"


def _paths(book_id: str) -> Dict[str, Path]:
    d = canon_dir(book_id)
    return {
        "ledger": d / "ledger.json",
        "timeline": d / "timeline.json",
        "characters": d / "characters.json",
        "glossary": d / "glossary.json",
    }


def _ensure_section_shape(name: str, x: Any) -> Any:
    if name in ("ledger", "timeline", "characters"):
        return x if isinstance(x, list) else []
    if name == "glossary":
        return x if isinstance(x, dict) else {}
    return x


def load_canon(book_id: str) -> Dict[str, Any]:
    ps = _paths(book_id)

    ledger = _ensure_section_shape("ledger", _load_json(ps["ledger"], []))
    timeline = _ensure_section_shape("timeline", _load_json(ps["timeline"], []))
    characters = _ensure_section_shape("characters", _load_json(ps["characters"], []))
    glossary = _ensure_section_shape("glossary", _load_json(ps["glossary"], {}))

    return {
        "canon_version": CANON_VERSION,
        "book_id": str(book_id or "default"),
        "updated_at": _iso(),
        "ledger": ledger,
        "timeline": timeline,
        "characters": characters,
        "glossary": glossary,
    }


def _index_by_id(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for it in items:
        if isinstance(it, dict) and it.get("id"):
            out[str(it["id"])] = it
    return out


def _upsert_list(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ex = _index_by_id(existing)
    for it in incoming:
        if not isinstance(it, dict):
            continue
        iid = it.get("id")
        if not iid:
            continue
        ex[str(iid)] = {**ex.get(str(iid), {}), **it}
    return [ex[k] for k in sorted(ex.keys())]


def _remove_ids(existing: List[Dict[str, Any]], remove_ids: List[str]) -> List[Dict[str, Any]]:
    rm = {str(x) for x in (remove_ids or []) if str(x).strip()}
    out: List[Dict[str, Any]] = []
    for it in existing:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("id") or "")
        if iid and iid in rm:
            continue
        out.append(it)
    return out


def patch_canon(book_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    patch schema:
      {
        "upsert": {
          "ledger": [ {id,...}, ... ],
          "timeline": [ {id,...}, ... ],
          "characters": [ {id,...}, ... ],
          "glossary": { "TERM": "def", ... }
        },
        "remove": {
          "ledger_ids": ["..."],
          "timeline_ids": ["..."],
          "character_ids": ["..."],
          "glossary_terms": ["TERM"]
        }
      }
    """
    canon = load_canon(book_id)
    up = patch.get("upsert") if isinstance(patch, dict) else {}
    rm = patch.get("remove") if isinstance(patch, dict) else {}

    if isinstance(up, dict):
        if isinstance(up.get("ledger"), list):
            canon["ledger"] = _upsert_list(canon["ledger"], up["ledger"])
        if isinstance(up.get("timeline"), list):
            canon["timeline"] = _upsert_list(canon["timeline"], up["timeline"])
        if isinstance(up.get("characters"), list):
            canon["characters"] = _upsert_list(canon["characters"], up["characters"])
        if isinstance(up.get("glossary"), dict):
            canon["glossary"] = {**canon["glossary"], **up["glossary"]}

    if isinstance(rm, dict):
        if isinstance(rm.get("ledger_ids"), list):
            canon["ledger"] = _remove_ids(canon["ledger"], rm["ledger_ids"])
        if isinstance(rm.get("timeline_ids"), list):
            canon["timeline"] = _remove_ids(canon["timeline"], rm["timeline_ids"])
        if isinstance(rm.get("character_ids"), list):
            canon["characters"] = _remove_ids(canon["characters"], rm["character_ids"])
        if isinstance(rm.get("glossary_terms"), list):
            for t in rm["glossary_terms"]:
                canon["glossary"].pop(str(t), None)

    ps = _paths(book_id)
    _atomic_write_json(ps["ledger"], canon["ledger"])
    _atomic_write_json(ps["timeline"], canon["timeline"])
    _atomic_write_json(ps["characters"], canon["characters"])
    _atomic_write_json(ps["glossary"], canon["glossary"])

    canon["updated_at"] = _iso()
    return canon

from __future__ import annotations
from typing import Any, Dict, List, Set

def _to_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]

def normalize_reason(reason: str) -> str:
    r = (reason or "").strip().upper()
    if "MIN_WORDS" in r:
        return "MIN_WORDS"
    if "EMPTY" in r or "BRAK TREŚCI" in r or "BRAK TRESCI" in r:
        return "EMPTY"
    if "PLACEHOLDER" in r:
        return "PLACEHOLDER"
    if "LIST" in r or "PUNKT" in r:
        return "LISTS"
    if "META" in r:
        return "META"
    return "OTHER"

def classify_quality_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    p = payload or {}
    decision = str(p.get("DECISION", "UNKNOWN")).upper()

    reasons_raw = _to_list(p.get("REASONS", []))
    reason_codes = [normalize_reason(str(x)) for x in reasons_raw]

    stats = p.get("STATS", {}) or {}
    words = int((stats or {}).get("words", 0))
    chars = int((stats or {}).get("chars", 0))

    flags = p.get("FLAGS", {}) or {}
    has_meta = bool(flags.get("has_meta", False))
    has_placeholders = bool(flags.get("has_placeholders", False))
    has_lists = bool(flags.get("has_lists", False))
    too_short = bool(flags.get("too_short", False))

    tags: Set[str] = set()
    tags.add(f"DECISION.{decision}")
    for rc in reason_codes:
        tags.add(f"REASON.{rc}")
    if has_meta:
        tags.add("FLAG.HAS_META")
    if has_placeholders:
        tags.add("FLAG.HAS_PLACEHOLDERS")
    if has_lists:
        tags.add("FLAG.HAS_LISTS")
    if too_short:
        tags.add("FLAG.TOO_SHORT")

    return {
        "decision": decision,
        "reason_codes": reason_codes,
        "words": words,
        "chars": chars,
        "tags": sorted(tags),
    }

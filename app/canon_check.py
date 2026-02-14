from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_NUM = re.compile(r"(\d+(?:[.,]\d+)?)")
_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_UNTIL_YEAR_PL = re.compile(r"\bdo\s+(19\d{2}|20\d{2})\s+roku\b", re.IGNORECASE)
_UNTIL_YEAR_EN = re.compile(r"\buntil\s+(19\d{2}|20\d{2})\b", re.IGNORECASE)

# very rough narration heuristics
_FIRST_PERSON_PL = re.compile(r"\b(ja|mnie|mi|mną|mój|moja|moje|byłem|byłam|jestem|mam|robiłem|widziałem)\b", re.IGNORECASE)
_THIRD_PERSON_PL = re.compile(r"\b(on|ona|ono|jego|jej|jemu|nią|mu|był|była|jest|ma|robił|widział)\b", re.IGNORECASE)


def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        s = str(x).strip().replace(",", ".")
        return float(s)
    except Exception:
        return None


def _find_amount_near(text: str, token: str, window: int = 40) -> Optional[float]:
    t = text or ""
    idx = t.lower().find(token.lower())
    if idx < 0:
        return None
    frag = t[idx : idx + max(10, window)]
    m = _NUM.search(frag)
    if not m:
        return None
    return _to_float(m.group(1))


def _extract_until_year(text: str) -> Optional[int]:
    t = text or ""
    m = _UNTIL_YEAR_PL.search(t) or _UNTIL_YEAR_EN.search(t)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    # fallback: any year
    m2 = _YEAR.search(t)
    if m2:
        try:
            return int(m2.group(1))
        except Exception:
            return None
    return None


def _narration_guess(text: str) -> Optional[str]:
    t = text or ""
    fp = len(_FIRST_PERSON_PL.findall(t))
    tp = len(_THIRD_PERSON_PL.findall(t))
    if fp == 0 and tp == 0:
        return None
    # simple dominance
    if fp >= tp + 2:
        return "first_person"
    if tp >= fp + 2:
        return "third_person"
    return None


def _canon_expected_until_year(canon: Dict[str, Any]) -> Optional[int]:
    """
    P0: if canon.timeline contains a statement with 'do YYYY roku' or contains a year, use it.
    """
    timeline = canon.get("timeline") or []
    if not isinstance(timeline, list):
        return None

    best: Optional[int] = None
    for f in timeline:
        if not isinstance(f, dict):
            continue
        st = str(f.get("statement") or "")
        y = _extract_until_year(st)
        if y:
            best = y
    return best


def _canon_expected_narration(canon: Dict[str, Any]) -> Optional[str]:
    """
    P0: look for locked decision mentioning narration: 'pierwszoosobowa'/'trzecioosobowa'
    """
    decisions = canon.get("decisions") or []
    if not isinstance(decisions, list):
        return None
    for d in decisions:
        if not isinstance(d, dict):
            continue
        if not d.get("locked"):
            continue
        dec = str(d.get("decision") or "").lower()
        if "pierwszoosob" in dec or "first person" in dec:
            return "first_person"
        if "trzecioosob" in dec or "third person" in dec:
            return "third_person"
    return None


def canon_check(text: str, canon: Dict[str, Any], scene_ref: str = "") -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    t = text or ""

    # --- existing LEDGER checks (kept) ---
    ledger = canon.get("ledger") or []
    if not isinstance(ledger, list):
        ledger = []

    for tx in ledger:
        if not isinstance(tx, dict):
            continue
        txid = str(tx.get("id") or "").strip()
        if not txid:
            continue
        if txid.lower() not in t.lower():
            continue

        expected = _to_float(tx.get("amount"))
        found = _find_amount_near(t, txid, window=60)
        if expected is None or found is None:
            continue
        if abs(expected - found) > 1e-9:
            issues.append(
                {
                    "type": "ledger_amount_mismatch",
                    "tx_id": txid,
                    "expected": expected,
                    "found": found,
                    "scene_ref": scene_ref or tx.get("scene_ref") or "",
                }
            )

    known_ids = {str(x.get("id")) for x in ledger if isinstance(x, dict) and x.get("id")}
    mentioned = set(re.findall(r"\btx_[a-zA-Z0-9]+\b", t))
    for mid in sorted(mentioned):
        if mid not in known_ids:
            issues.append({"type": "ledger_unknown_tx", "tx_id": mid, "scene_ref": scene_ref})

    # --- NEW P0 checks for timeline / decisions ---
    exp_year = _canon_expected_until_year(canon)
    got_year = _extract_until_year(t)
    if exp_year is not None and got_year is not None and exp_year != got_year:
        issues.append(
            {
                "type": "timeline_year_mismatch",
                "expected_year": exp_year,
                "found_year": got_year,
                "scene_ref": scene_ref,
            }
        )

    exp_narr = _canon_expected_narration(canon)
    got_narr = _narration_guess(t)
    if exp_narr and got_narr and exp_narr != got_narr:
        issues.append(
            {
                "type": "decision_violation",
                "decision": "narration",
                "BLOCK_PIPELINE": (str("narration").upper() == "FAIL"),
                "BLOCK_PIPELINE": (str("narration").upper() == "FAIL"),
                "expected": exp_narr,
                "found": got_narr,
                "scene_ref": scene_ref,
            }
        )

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "scene_ref": scene_ref,
    }

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


_NUM = re.compile(r"(\d+(?:[.,]\d+)?)")


def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        s = str(x).strip().replace(",", ".")
        return float(s)
    except Exception:
        return None


def _find_amount_near(text: str, token: str, window: int = 40) -> Optional[float]:
    """
    Heurystyka deterministyczna:
    jeśli w tekście jest token (np. tx_001), szukamy pierwszej liczby w promieniu window znaków po tokenie.
    """
    t = text or ""
    idx = t.lower().find(token.lower())
    if idx < 0:
        return None
    frag = t[idx : idx + max(10, window)]
    m = _NUM.search(frag)
    if not m:
        return None
    return _to_float(m.group(1))


def canon_check(text: str, canon: Dict[str, Any], scene_ref: str = "") -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    t = text or ""

    ledger = canon.get("ledger") or []
    if not isinstance(ledger, list):
        ledger = []

    # 1) mismatch amounts for mentioned tx ids
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

        # jeśli nie ma liczby koło txid, nie zgadujemy
        if expected is None or found is None:
            continue

        # tolerancja minimalna na float
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

    # 2) unknown tx ids mentioned like tx_XXX that are not in ledger
    known_ids = {str(x.get("id")) for x in ledger if isinstance(x, dict) and x.get("id")}
    mentioned = set(re.findall(r"\btx_[a-zA-Z0-9]+\b", t))
    for mid in sorted(mentioned):
        if mid not in known_ids:
            issues.append({"type": "ledger_unknown_tx", "tx_id": mid, "scene_ref": scene_ref})

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "scene_ref": scene_ref,
    }

from __future__ import annotations

from typing import Any, Dict, List

_VALID_DECISIONS = {"ACCEPT", "REVISE", "REJECT"}

def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: List[str] = []
        for x in value:
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    if isinstance(value, (tuple, set)):
        out: List[str] = []
        for x in value:
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    s = str(value).strip()
    return [s] if s else []

def _normalize_decision(value: Any) -> str:
    if value is None:
        return "REJECT"
    d = str(value).strip().upper()

    # legacy mapping
    if d in {"PASS", "OK", "SUCCESS"}:
        return "ACCEPT"
    if d in {"FAIL", "FAILED", "ERROR"}:
        return "REJECT"

    if d in _VALID_DECISIONS:
        return d

    return "REJECT"

def normalize_quality(payload: Any) -> Dict[str, Any]:
    """
    Gwarantuje kontrakt QUALITY payload:
    - DECISION: ACCEPT|REVISE|REJECT
    - REASONS: list[str] (zawsze obecne)
    """
    if isinstance(payload, dict):
        out: Dict[str, Any] = dict(payload)
    else:
        out = {"text": "" if payload is None else str(payload)}

    raw_decision = out.get("DECISION")
    if raw_decision is None:
        raw_decision = out.get("decision")
    if raw_decision is None:
        raw_decision = out.get("status")

    out["DECISION"] = _normalize_decision(raw_decision)

    raw_reasons = out.get("REASONS")
    if raw_reasons is None:
        raw_reasons = out.get("reasons")
    out["REASONS"] = _as_list(raw_reasons)

    return out

def enforce_terminal_rules(payload: Any) -> Dict[str, Any]:
    """
    Domyka kontrakt terminalny QUALITY.
    Zostawiamy payload kompatybilny wstecznie i zawsze kompletny.
    """
    out = normalize_quality(payload)

    # Dodatkowa stabilizacja: REJECT może mieć pustą listę,
    # ale nadal musi być listą (to już jest zapewnione).
    if not isinstance(out.get("REASONS"), list):
        out["REASONS"] = _as_list(out.get("REASONS"))

    return out

from __future__ import annotations
from typing import Any, Dict, List, Tuple

_ALLOWED_STATUS = {"ok", "error"}

def build_response(status: str, data: Any = None, errors: List[str] | None = None) -> Dict[str, Any]:
    st = (status or "").strip().lower()
    if st not in _ALLOWED_STATUS:
        raise ValueError("status must be 'ok' or 'error'")

    if errors is None:
        errors = []
    if not isinstance(errors, list):
        raise TypeError("errors must be list[str]")

    normalized_errors: List[str] = [str(e) for e in errors if str(e).strip() != ""]
    return {
        "status": st,
        "data": data,
        "errors": normalized_errors,
    }

def validate_response(resp: Dict[str, Any]) -> Tuple[bool, List[str]]:
    issues: List[str] = []

    if not isinstance(resp, dict):
        return False, ["response must be a dict"]

    for k in ("status", "data", "errors"):
        if k not in resp:
            issues.append(f"missing key: {k}")

    st = resp.get("status")
    if st not in _ALLOWED_STATUS:
        issues.append("invalid status")

    errs = resp.get("errors")
    if not isinstance(errs, list):
        issues.append("errors must be a list")
    elif not all(isinstance(x, str) for x in errs):
        issues.append("errors entries must be strings")

    return len(issues) == 0, issues

from __future__ import annotations
from typing import Any, Dict, List, Optional

ALLOWED_STATUS = {"ok", "error"}

def _norm_error_item(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        code = item.get("code", "E_GENERIC")
        message = item.get("message")
        if message is None:
            message = str(item)
        return {"code": str(code), "message": str(message)}
    return {"code": "E_GENERIC", "message": str(item)}

def _normalize_errors(errors: Optional[Any]) -> List[Dict[str, Any]]:
    if errors is None:
        return []
    if isinstance(errors, list):
        return [_norm_error_item(x) for x in errors]
    return [_norm_error_item(errors)]

def build_response(
    status: str = "ok",
    data: Optional[Dict[str, Any]] = None,
    errors: Optional[Any] = None
) -> Dict[str, Any]:
    if data is None:
        data = {}
    norm_errors = _normalize_errors(errors)

    # wymuszenie spójności kontraktu: niepuste errors => status=error
    if norm_errors:
        status = "error"

    return {"status": status, "data": data, "errors": norm_errors}

def validate_response(resp: Any) -> bool:
    if not isinstance(resp, dict):
        return False
    if set(resp.keys()) != {"status", "data", "errors"}:
        return False
    if resp["status"] not in ALLOWED_STATUS:
        return False
    if not isinstance(resp["data"], dict):
        return False
    if not isinstance(resp["errors"], list):
        return False
    for e in resp["errors"]:
        if not isinstance(e, dict):
            return False
        if "code" not in e or "message" not in e:
            return False
    return True

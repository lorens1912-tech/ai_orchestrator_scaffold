from __future__ import annotations
from typing import Any, Dict, List, Optional

ALLOWED_STATUS = {"ok", "error"}

def build_response(
    status: str = "ok",
    data: Optional[Dict[str, Any]] = None,
    errors: Optional[List[Any]] = None
) -> Dict[str, Any]:
    if data is None:
        data = {}
    if errors is None:
        errors = []
    return {"status": status, "data": data, "errors": errors}

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
    return True

from __future__ import annotations
from typing import Any, Dict, List, Optional, Union

ALLOWED_STATUS = {"ok", "error"}

def build_response(
    status: str = "ok",
    data: Optional[Dict[str, Any]] = None,
    errors: Optional[Union[Any, List[Any]]] = None
) -> Dict[str, Any]:
    """
    Buduje standardową odpowiedź API.
    - Jeśli errors nie jest pusty → status automatycznie staje się 'error'
    - errors zawsze jest listą (konwersja pojedynczego błędu na listę)
    """
    if data is None:
        data = {}

    if errors is None:
        errors = []
    elif not isinstance(errors, list):
        errors = [errors]

    # Kluczowa logika: błędy wymuszają status 'error'
    final_status = "error" if errors else status

    return {
        "status": final_status,
        "data": data,
        "errors": errors
    }

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

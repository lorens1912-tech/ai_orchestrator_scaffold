from __future__ import annotations
from typing import Any, Dict, Tuple

LANE_MODES = {"WRITE", "EDIT", "CRITIC"}

def should_use_pro_writer_lane(mode: Any, preset: Any, payload: Any) -> bool:
    m = str(mode or "").upper()
    if m not in LANE_MODES:
        return False
    if isinstance(payload, dict) and payload.get("disable_pro_writer_lane") is True:
        return False
    return True

def _call_lane(fn, mode: Any, preset: Any, payload: Any) -> Any:
    errors = []
    for call in (
        lambda: fn(mode=mode, preset=preset, payload=payload),
        lambda: fn(mode=mode, payload=payload),
        lambda: fn(mode, preset, payload),
        lambda: fn(mode, payload),
        lambda: fn(payload),
    ):
        try:
            return call()
        except TypeError as e:
            errors.append(str(e))
    raise TypeError("lane_call_signature_mismatch: " + " | ".join(errors[:3]))

def try_pro_writer_lane(mode: Any, preset: Any, payload: Any) -> Tuple[bool, Dict[str, Any], Dict[str, Any]]:
    if not should_use_pro_writer_lane(mode, preset, payload):
        return False, {}, {"reason": "not_eligible"}

    try:
        import app.pro_writer_lane as lane
    except Exception as e:
        return False, {}, {"reason": "lane_import_error", "detail": str(e)}

    for fn_name in (
        "route_writer_lane",
        "resolve_writer_lane",
        "run_writer_lane_contract",
        "writer_lane_contract",
        "build_writer_lane_contract",
    ):
        fn = getattr(lane, fn_name, None)
        if not callable(fn):
            continue
        try:
            out = _call_lane(fn, mode, preset, payload)
        except Exception as e:
            return False, {}, {"reason": "lane_call_error", "fn": fn_name, "detail": str(e)}

        if isinstance(out, dict):
            handled = bool(out.get("handled", False))
            response = out.get("response", {})
            if handled and isinstance(response, dict):
                return True, response, {"fn": fn_name, "reason": "handled"}
            return False, {}, {"fn": fn_name, "reason": "not_handled_dict"}

    return False, {}, {"reason": "no_callable_found"}

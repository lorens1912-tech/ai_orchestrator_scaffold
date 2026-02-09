from __future__ import annotations
from typing import Any, Dict, Tuple
from app.policy_feedback import adjust_policy_from_feedback
from app.policy_resolver import resolve_policy_for_scope

def adjust_policy_targeted(
    current_policy: Dict[str, Any] | None,
    feedback: Dict[str, Any] | None,
    preset: str | None = None,
    mode: str | None = None,
    flags: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    resolved_policy, telemetry = resolve_policy_for_scope(
        current_policy=current_policy,
        preset=preset,
        mode=mode,
        flags=flags,
    )

    if not telemetry.get("enabled", True):
        audit = {
            "band": "skip",
            "pressure": 0.0,
            "reason": "disabled_by_flags",
            "signals": {},
            "policy_source": telemetry.get("policy_source"),
        }
        return resolved_policy, {"audit": audit, "telemetry": telemetry}

    adjusted_policy, audit = adjust_policy_from_feedback(
        current_policy=resolved_policy,
        feedback=feedback or {},
    )
    audit = dict(audit or {})
    audit["policy_source"] = telemetry.get("policy_source")
    return adjusted_policy, {"audit": audit, "telemetry": telemetry}

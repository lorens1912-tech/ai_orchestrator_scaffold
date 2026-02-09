from __future__ import annotations
from typing import Any, Dict
from fastapi import APIRouter, Body
from app.policy_feedback import adjust_policy_from_feedback

router = APIRouter()

@router.post("/policy/adjust")
def policy_adjust(body: Dict[str, Any] = Body(...)):
    current_policy = body.get("current_policy") or {}
    feedback = body.get("feedback") or {}
    adjusted_policy, audit = adjust_policy_from_feedback(
        current_policy=current_policy,
        feedback=feedback,
    )
    return {"status": "ok", "adjusted_policy": adjusted_policy, "audit": audit}

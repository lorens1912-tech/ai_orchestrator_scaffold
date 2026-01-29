from __future__ import annotations
from app.resume_index import get_latest_run_id, set_latest_run_id

import time
from uuid import uuid4
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .orchestrator_stub import resolve_modes, execute_stub, write_manifest, load_manifest
from .book_store import ensure_book_structure, update_book_latest

# --- execute_stub wrapper: mapuje TEAM_OVERRIDE_NOT_ALLOWED -> HTTP 422 ---
def _execute_stub_http_safe(**kwargs):
    from fastapi import HTTPException
    from app.orchestrator_stub import execute_stub as _raw_execute_stub
    try:
        return _raw_execute_stub(**kwargs)
    except ValueError as e:
        if "TEAM_OVERRIDE_NOT_ALLOWED" in str(e):
            raise HTTPException(status_code=422, detail=str(e))
        raise
router = APIRouter()

class AgentStepRequest(BaseModel):
    # run control
    run_id: Optional[str] = None
    resume: bool = False

    # project routing (optional)
    book_id: Optional[str] = None

    # one of:
    modes: Optional[List[str]] = None
    steps: Optional[List[Dict[str, Any]]] = None
    preset: Optional[str] = None

    # common payload
    payload: Optional[Dict[str, Any]] = None

    # global override (applies only to author modes by policy in orchestrator_stub)
    team_override: Optional[str] = None

@router.post("/agent/step")
def agent_step(req: AgentStepRequest) -> Dict[str, Any]:
    try:
        # === RESUME_V4_BEGIN ===
        run_id = None
        if req.resume:
            run_id = get_latest_run_id(req.book_id)
        if not run_id:
            run_id = None
        set_latest_run_id(req.book_id, run_id)
        # === RESUME_V4_END ===
        if req.resume:
            run_id = get_latest_run_id(req.book_id)
        if not run_id:
            run_id =  req.run_id or f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        set_latest_run_id(req.book_id, run_id)

        # ensure book scaffolding if book_id provided
        if req.book_id:
            ensure_book_structure(req.book_id)

        payload_dict = req.model_dump(exclude_none=True)

        steps = resolve_modes(run_id, payload_dict)

        state = _execute_stub_http_safe(run_id, steps, resume=req.resume)

        if load_manifest(run_id) is None:
            write_manifest(run_id, state)

        latest = None
        if req.book_id:
            latest = update_book_latest(req.book_id, run_id, state)

        return {"run_id": run_id, "state": state, "book_latest": latest}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")


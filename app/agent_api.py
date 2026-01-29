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

from __future__ import annotations
from typing import Any, Dict
from fastapi import APIRouter, HTTPException

from .orchestrator_stub import resolve_modes, execute_stub

router = APIRouter()


@router.post("/agent/step")
def agent_step(body: Dict[str, Any]):
    try:
        payload = body.get("payload", {})

        # NORMALIZACJA WEJŚCIA
        if "preset" in body:
            plan = resolve_modes(preset=body["preset"], payload=payload)
        elif "modes" in body:
            plan = resolve_modes(modes=body["modes"], payload=payload)
        elif "mode" in body:
            plan = resolve_modes(modes=[body["mode"]], payload=payload)
        else:
            raise HTTPException(status_code=400, detail="No mode or preset specified")

        result = execute_stub(plan)
        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

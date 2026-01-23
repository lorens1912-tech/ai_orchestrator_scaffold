from __future__ import annotations

from fastapi import APIRouter, HTTPException
from .agent_models import AgentStepRequest, AgentStepResponse
from .orchestrator_stub import resolve_modes, execute_stub, write_manifest, load_manifest
from .run_store import new_run_id
from .config_registry import ConfigError

router = APIRouter(prefix="/agent", tags=["agent"])

@router.post("/step", response_model=AgentStepResponse)
def agent_step(req: AgentStepRequest):
    try:
        if req.resume:
            if not req.run_id:
                raise ConfigError("resume=true requires run_id")
            mf = load_manifest(req.run_id)
            run_id = req.run_id
            book_id = mf.get("book_id")
            resolved = mf.get("resolved_modes", [])
            if not isinstance(book_id, str) or not isinstance(resolved, list):
                raise ConfigError("Invalid run manifest content")
            artifacts = execute_stub(run_id, book_id, resolved, req.payload)
            return AgentStepResponse(
                ok=True,
                run_id=run_id,
                book_id=book_id,
                mode=None,
                preset=None,
                resolved_modes=resolved,
                artifacts=artifacts,
                warnings=[],
                errors=[]
            )

        resolved, mode_used, preset_used = resolve_modes(req.mode, req.preset)
        run_id = new_run_id("run")
        write_manifest(run_id, req.book_id, resolved, req.payload)
        artifacts = execute_stub(run_id, req.book_id, resolved, req.payload)

        return AgentStepResponse(
            ok=True,
            run_id=run_id,
            book_id=req.book_id,
            mode=mode_used,
            preset=preset_used,
            resolved_modes=resolved,
            artifacts=artifacts,
            warnings=[],
            errors=[]
        )
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")

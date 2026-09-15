from fastapi import FastAPI, HTTPException
from typing import Dict, Any

from app.p20_core.runtime import run_agent_step, health_payload, config_validate_payload
from app.p20_core.contracts import AgentStepRequest
from app.p20_core.book_bible_contract import BookBibleContractError
from app.p20_core.canon_rebuild import canon_rebuild_endpoint
from app.p20_core.context_runtime import ProjectExecutionIdentityError
from app.config_registry import load_presets
from app.canon_check import canon_check
from app.canon_store import load_canon
from app.debug_model_router import router as debug_model_router
from app.bible_api import router as bible_router
from app.operator_api import router as operator_router

app = FastAPI()
app.include_router(debug_model_router)
app.include_router(bible_router)
app.include_router(operator_router)


def _agent_input_error_status(exc: ValueError) -> int | None:
    detail = str(exc)
    if (
        detail.startswith("Unknown mode:")
        or detail.startswith("Unknown preset:")
        or detail.startswith("Unknown team_id:")
    ):
        return 400
    if detail.startswith("TEAM_OVERRIDE_NOT_ALLOWED:"):
        return 422
    return None


@app.get("/health")
def health():
    return health_payload()


@app.get("/config/validate")
def config_validate():
    return config_validate_payload()


@app.get("/config/presets")
def config_presets() -> Dict[str, Any]:
    raw = load_presets()
    presets = raw.get("presets") if isinstance(raw, dict) else raw
    if isinstance(raw, dict):
        preset_ids = list(raw.get("preset_ids") or [])
        presets_count = int(raw.get("presets_count") or len(preset_ids))
    else:
        preset_ids = [str(item.get("id")) for item in presets if isinstance(item, dict) and item.get("id")]
        presets_count = len(preset_ids)
    return {
        "ok": True,
        "source": "config_registry",
        "presets": presets,
        "preset_ids": preset_ids,
        "presets_count": presets_count,
    }


@app.post("/canon/rebuild")
def canon_rebuild(body: Dict[str, Any]) -> Dict[str, Any]:
    return canon_rebuild_endpoint(body)


@app.post("/canon/check_flags")
def canon_check_flags(body: Dict[str, Any]) -> Dict[str, Any]:
    book_id = str(body.get("book_id") or "default")
    text = str(body.get("text") or "")
    scene_ref = str(body.get("scene_ref") or "")
    canon = body.get("canon")
    if not isinstance(canon, dict):
        canon = load_canon(run_dir=None, book_id=book_id)
    result = canon_check(text=text, canon=canon, scene_ref=scene_ref)
    return {"ok": True, "book_id": book_id, "result": result}


@app.post("/agent/step")
async def agent_step(req: AgentStepRequest) -> Dict[str, Any]:
    try:
        return await run_agent_step(req)
    except BookBibleContractError as e:
        return {
            "ok": False,
            "decision": "REJECT",
            "error": str(e),
        }
    except ProjectExecutionIdentityError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ValueError as e:
        status_code = _agent_input_error_status(e)
        if status_code is not None:
            raise HTTPException(status_code=status_code, detail=str(e)) from e
        raise

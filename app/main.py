from fastapi import FastAPI, HTTPException
from typing import Dict, Any

from app.p20_core.runtime import run_agent_step, health_payload, config_validate_payload
from app.p20_core.contracts import AgentStepRequest
from app.p20_core.book_bible_contract import BookBibleContractError
from app.p20_core.canon_rebuild import canon_rebuild_endpoint

app = FastAPI()


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


@app.post("/canon/rebuild")
def canon_rebuild(body: Dict[str, Any]) -> Dict[str, Any]:
    return canon_rebuild_endpoint(body)


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
    except ValueError as e:
        status_code = _agent_input_error_status(e)
        if status_code is not None:
            raise HTTPException(status_code=status_code, detail=str(e)) from e
        raise

from fastapi import FastAPI
from typing import Dict, Any

from app.p20_core.runtime import run_agent_step, health_payload, config_validate_payload
from app.p20_core.contracts import AgentStepRequest
from app.p20_core.book_bible_contract import BookBibleContractError
from app.p20_core.canon_rebuild import canon_rebuild_endpoint

app = FastAPI()


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

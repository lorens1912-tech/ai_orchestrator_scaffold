from fastapi import FastAPI
from typing import Dict, Any

from app.p20_core.runtime import run_agent_step, health_payload, config_validate_payload
from app.p20_core.contracts import AgentStepRequest
from app.p20_core.book_bible_contract import load_book_bible_or_raise
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
    modes_check = list(req.modes or [])
    if not modes_check and getattr(req, "mode", None):
        modes_check = [req.mode]

    if "WRITE" in modes_check:
        payload = dict(req.payload or {})
        resolved_book_id = req.book_id or payload.get("book_id")

        try:
            binding = load_book_bible_or_raise(resolved_book_id)
        except Exception as e:
            return {
                "ok": False,
                "decision": "REJECT",
                "error": str(e),
            }

        payload["_book_bible"] = {
            "path": binding["path"],
            "sha256": binding["sha256"],
            "contract_version": binding["contract_version"],
        }

        req = req.copy(update={"book_id": resolved_book_id, "payload": payload})

    return await run_agent_step(req)

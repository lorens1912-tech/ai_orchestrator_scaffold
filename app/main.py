from fastapi import FastAPI
from .config_api import router as config_router
from .agent_api import router as agent_router
from .runs_api import router as runs_router

app = FastAPI()
app.include_router(config_router)
app.include_router(agent_router)
app.include_router(runs_router)

@app.get("/health")
def health():
    return {"ok": True}

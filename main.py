
from fastapi import FastAPI

from books_memory_api import router as books_memory_router
from books_writer_api import router as books_writer_router
from books_architect_api import router as books_architect_router
from books_runs_api import router as books_runs_router
from books_critic_v2_api import router as books_critic_v2_router
from books_runs_details_api import router as books_runs_details_router
from books_runs_export_api import router as books_runs_export_router
from books_runs_manage_api import router as books_runs_manage_router
from books_runs_post_api import router as books_runs_post_router
from books_workflow_api import router as books_workflow_router

app = FastAPI(title="AI Orchestrator Scaffold")

app.include_router(books_memory_router)
app.include_router(books_writer_router)
app.include_router(books_architect_router)
app.include_router(books_runs_router)
app.include_router(books_critic_v2_router)
app.include_router(books_runs_details_router)
app.include_router(books_runs_export_router)
app.include_router(books_runs_manage_router)
app.include_router(books_runs_post_router)
app.include_router(books_workflow_router)

# /books/agent/worker/*
try:
    from books_agent_worker_api import router as books_agent_worker_router
    app.include_router(books_agent_worker_router)
except Exception as e:
    print("[WARN] books_agent_worker_api not loaded:", repr(e))

# /books/agent/step (serwer ma wstać nawet jeśli ten moduł ma problem)
try:
    from books_agent_step_api import router as books_agent_step_router
    app.include_router(books_agent_step_router)
except Exception as e:
    print("[WARN] books_agent_step_api not loaded:", repr(e))

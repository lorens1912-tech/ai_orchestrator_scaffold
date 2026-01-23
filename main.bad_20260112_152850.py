
$ROOT="C:\AI\ai_orchestrator_scaffold"

# backup uszkodzonego pliku
$bak = "$ROOT\main.bad_{0}.py" -f (Get-Date -Format "yyyyMMdd_HHmmss")
Copy-Item "$ROOT\main.py" $bak -Force

# nadpisz main.py wersją stabilną (bez żadnych printów/f-stringów)
@'
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

from books_agent_step_api import router as books_agent_step_router

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
app.include_router(books_agent_step_router)
'@ | Set-Content -Encoding UTF8 "$ROOT\main.py"

# test import (ma wypisać MAIN_OK)
python -c "import main; print('MAIN_OK')"

from __future__ import annotations

from fastapi import APIRouter

from books_artifacts_api import router as artifacts_router
from books_runs_api import router as runs_router

from books_proof_api import router as proof_router
from books_critic_api import router as critic_router
from books_humanity_api import router as humanity_router
from books_writer_api import router as writer_router
from books_architect_api import router as architect_router
from books_humanity_llm_api import router as humanity_llm_router
from books_agent_api import router as agent_router
from books_agent_jobs_api import router as agent_jobs_router
from books_draft_api import router as draft_router

router = APIRouter()
router.include_router(artifacts_router)
router.include_router(runs_router)
router.include_router(proof_router)
router.include_router(critic_router)
router.include_router(humanity_router)
router.include_router(writer_router)
router.include_router(architect_router)
router.include_router(humanity_llm_router)
router.include_router(agent_router)
router.include_router(agent_jobs_router)
router.include_router(draft_router)


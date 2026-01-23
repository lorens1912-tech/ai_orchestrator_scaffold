from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathlib import Path
import json, datetime
from .state_schema import BookState
from .llm_client import run_completion

router = APIRouter()
DATA_ROOT     = Path(__file__).resolve().parent.parent / "books"
STOP_SEQUENCE = "<|END_OF_SCENE|>"
DEFAULT_MODEL = "gpt-4.1-mini"

# ---------- MODELS ----------
class WorkerOnceReq(BaseModel):
    book: str
    job_id: str

class WorkerOnceResp(BaseModel):
    ok: bool
    book: str
    processed: bool
    job_id: str
    status: str
    master_path: str
    buffer_path: str
    wrote_to: str
    job_done_path: str
    model: str
    added_chars: int            # <- DODANE
    usage: dict | None = None
    error: str | None = None

# ---------- HELPERS ----------
def _truncate_tokens(text: str, max_tokens: int) -> str:
    return text[: max_tokens * 4]  # 1 token ≈ 4 znaki

# ---------- ENDPOINT ----------
@router.post("/books/agent/worker/once", response_model=WorkerOnceResp)
def worker_once(req: WorkerOnceReq):
    book_dir    = DATA_ROOT / req.book
    draft_dir   = book_dir / "draft"
    buffer_path = draft_dir / "buffer.txt"
    master_path = draft_dir / "master.txt"
    buffer_path.parent.mkdir(parents=True, exist_ok=True)
    if not buffer_path.exists():
        buffer_path.write_text("", encoding="utf-8")

    state_path = book_dir / "state.json"
    if not state_path.exists():
        raise HTTPException(500, "missing state.json")
    state = BookState.model_validate_json(state_path.read_text())

    prompt_base    = master_path.read_text(encoding="utf-8")
    prompt_trimmed = _truncate_tokens(prompt_base, state.writer.max_tokens)
    prompt         = f"{prompt_trimmed}\n{STOP_SEQUENCE}\n"

    try:
        completion = run_completion(
            model      = DEFAULT_MODEL,
            prompt     = prompt,
            max_tokens = state.writer.max_tokens
        )
        text_out = completion["content"]
        usage    = completion.get("usage")
    except Exception as e:
        return WorkerOnceResp(
            ok=False, book=req.book, processed=False, job_id=req.job_id,
            status="FAILED", master_path=str(master_path), buffer_path=str(buffer_path),
            wrote_to="", job_done_path="", model=DEFAULT_MODEL, added_chars=0,
            usage=None, error=str(e)
        )

    buffer_path.write_text(text_out, encoding="utf-8")

    job_done_dir = book_dir / "jobs_done"
    job_done_dir.mkdir(parents=True, exist_ok=True)
    done_path = job_done_dir / f"{req.job_id}.json"
    done_path.write_text(json.dumps({
        "job_id": req.job_id,
        "model":  DEFAULT_MODEL,
        "ts":     datetime.datetime.utcnow().isoformat(),
        "added_chars": len(text_out)
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return WorkerOnceResp(
        ok=True, book=req.book, processed=True, job_id=req.job_id, status="SUCCESS",
        master_path=str(master_path), buffer_path=str(buffer_path), wrote_to="buffer",
        job_done_path=str(done_path), model=DEFAULT_MODEL,
        added_chars=len(text_out), usage=usage, error=None
    )

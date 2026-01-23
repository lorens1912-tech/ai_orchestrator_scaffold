
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

print(f"[WORKFLOW_API] LOADED: {__file__}")

router = APIRouter(prefix="/books", tags=["workflow"])

_BOOK_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")


def _root_dir() -> Path:
    return Path(__file__).resolve().parent


def _books_dir() -> Path:
    return _root_dir() / "books"


def _validate_book(book: str) -> None:
    if not _BOOK_RE.fullmatch(book or ""):
        raise HTTPException(status_code=400, detail="Invalid book id (allowed: a-z A-Z 0-9 _ - ; max 64).")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str = "") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}{ts}_{uuid.uuid4().hex}" if prefix else f"{ts}_{uuid.uuid4().hex}"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, obj: Any) -> None:
    _atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def _append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _write_run_one_shot(
    *,
    book: str,
    title: str,
    status: str,
    inputs: Dict[str, Any],
    outputs: Dict[str, Any],
    error: Optional[str] = None,
    extra_paths: Optional[Dict[str, str]] = None,
) -> str:
    runs_dir = _books_dir() / book / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    run_id = _new_id()
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    ts = _utc_now_iso()

    paths: Dict[str, str] = {
        "dir": str(run_dir),
        "meta": str(run_dir / "meta.json"),
        "input": str(run_dir / "input.json"),
        "output": str(run_dir / "output.json"),
    }
    if extra_paths:
        paths.update({k: str(v) for k, v in extra_paths.items()})

    meta = {
        "run_id": run_id,
        "book": book,
        "role": "WORKFLOW",
        "title": title,
        "model": "WORKFLOW",
        "status": status,
        "ts_start": ts,
        "ts_end": ts,
        "error": error,
        "paths": paths,
    }

    _atomic_write_json(run_dir / "meta.json", meta)
    _atomic_write_json(run_dir / "input.json", inputs or {})
    _atomic_write_json(run_dir / "output.json", outputs or {})

    return run_id


class NextChunkBody(BaseModel):
    architect_model: Optional[str] = Field("MODEL_NEUTRAL", description="Model dla ARCHITEKTA (opcjonalnie)")
    writer_model: Optional[str] = Field("MODEL_NEUTRAL", description="Model dla PISARZA (opcjonalnie)")
    brief_note: Optional[str] = Field("neutral", description="Neutralna notatka do briefu")
    chunk_text: Optional[str] = Field("NEUTRAL_WORKFLOW_CHUNK", description="Neutralny tekst dopisywany do master.txt")
    append_newline: bool = Field(True, description="Dopisać \\n na końcu chunk_text")


@router.post("/book/{book}/workflow/next_chunk")
def workflow_next_chunk(book: str, body: NextChunkBody) -> Dict[str, Any]:
    _validate_book(book)

    # --- Paths ---
    analysis_dir = _books_dir() / book / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    draft_dir = _books_dir() / book / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)

    master_path = (draft_dir / "master.txt").resolve()

    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    brief_path = (analysis_dir / f"workflow_brief_{ts_tag}.md").resolve()
    brief_latest_path = (analysis_dir / "workflow_brief_latest.md").resolve()

    # --- Step 1: ARCHITEKT (brief) — neutral ---
    brief = (
        "# WORKFLOW BRIEF\n\n"
        f"- book: {book}\n"
        f"- ts: {ts_tag}\n"
        f"- architect_model: {body.architect_model}\n"
        f"- note: {body.brief_note}\n\n"
        "## CEL\n"
        "- Kontynuuj tekst w master.txt w sposób spójny stylistycznie.\n"
        "- Bez wprowadzania danych wrażliwych / prawdziwych adresów / realnych osób.\n"
        "- Neutralny placeholder w testach.\n"
    )
    _atomic_write_text(brief_path, brief)
    _atomic_write_text(brief_latest_path, brief)

    # --- Step 2: PISARZ (append chunk) — neutral ---
    chunk = body.chunk_text or "NEUTRAL_WORKFLOW_CHUNK"
    if body.append_newline and not chunk.endswith("\n"):
        chunk += "\n"
    _append_text(master_path, chunk)

    outputs = {
        "ok": True,
        "book": book,
        "brief_path": str(brief_path),
        "brief_latest_path": str(brief_latest_path),
        "master_path": str(master_path),
        "written_chars": len(chunk),
    }

    inputs = {
        "architect_model": body.architect_model,
        "writer_model": body.writer_model,
        "brief_note": body.brief_note,
        "chunk_preview": (chunk[:200] if chunk else ""),
        "append_newline": body.append_newline,
    }

    run_id = _write_run_one_shot(
        book=book,
        title="WORKFLOW_NEXT_CHUNK",
        status="SUCCESS",
        inputs=inputs,
        outputs=outputs,
        extra_paths={
            "workflow_brief": str(brief_path),
            "workflow_brief_latest": str(brief_latest_path),
            "master_txt": str(master_path),
        },
    )

    return {"ok": True, "book": book, "run_id": run_id, **outputs}

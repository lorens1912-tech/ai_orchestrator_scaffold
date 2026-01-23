from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from books_core import (
    safe_book_root,
    safe_resolve_under,
    ensure_dir,
    make_run_id,
    atomic_write_text,
    write_run,
    write_latest,
    read_text_safe,
)

router = APIRouter(prefix="/books/writer", tags=["books.writer"])


class WriterGenerateReq(BaseModel):
    book: str
    text: str = Field(..., description="Text to append to draft/master.txt")
    ensure_newline: bool = Field(True, description="Ensure newline separation before/after append")
    preview_chars: int = Field(900, ge=200, le=4000)


class WriterGenerateResp(BaseModel):
    ok: bool
    book: str
    run_id: str
    status: str
    role: str
    title: str
    preview: str
    paths: Dict[str, str]
    writes: Dict[str, Any]


@router.post("/generate", response_model=WriterGenerateResp)
def writer_generate(req: WriterGenerateReq):
    run_id = make_run_id("writer")
    role = "WRITER"
    title = "WRITER_APPEND"

    try:
        book_root = safe_book_root(req.book)
        ensure_dir(book_root)

        draft_rel = "draft/master.txt"
        draft_path = safe_resolve_under(book_root, draft_rel)
        ensure_dir(draft_path.parent)

        incoming = req.text or ""
        incoming = incoming.replace("\r\n", "\n").replace("\r", "\n")

        # Read existing (best-effort)
        existing = ""
        try:
            if draft_path.exists():
                existing = read_text_safe(draft_path)
        except Exception:
            existing = ""

        to_append = incoming
        if req.ensure_newline:
            prefix = "" if (not existing or existing.endswith("\n")) else "\n"
            suffix = "" if to_append.endswith("\n") else "\n"
            to_append = prefix + to_append + suffix

        new_text = existing + to_append

        w_draft = atomic_write_text(draft_path, new_text)

        fallback = not bool(w_draft.get("ok"))

        # latest writer report (md + json + raw)
        preview = to_append[-req.preview_chars:] if len(to_append) > req.preview_chars else to_append
        report_md = (
            "# Writer append\n"
            f"- book: `{req.book}`\n"
            f"- run_id: `{run_id}`\n"
            f"- draft: `{draft_rel}`\n"
            f"- appended_chars: **{len(to_append)}**\n"
            f"- write_mode: `{w_draft.get('mode')}`\n"
            "\n"
            "## Preview (appended tail)\n"
            "```text\n"
            f"{preview.rstrip()}\n"
            "```\n"
        )

        report_json = {
            "ok": True,
            "tool": "writer",
            "run_id": run_id,
            "book": req.book,
            "draft": draft_rel,
            "appended_chars": len(to_append),
            "write": w_draft,
            "fallback": fallback,
        }

        w_latest = write_latest(book_root, "writer", report_md, json_obj=report_json, raw_text=incoming)

        # run log
        run_in = {"book": req.book, "draft": draft_rel, "ensure_newline": req.ensure_newline, "text_chars": len(incoming)}
        run_out = {
            "ok": True,
            "tool": "writer",
            "fallback": fallback,
            "draft_rel": draft_rel,
            "appended_chars": len(to_append),
            "preview": preview,
            "latest": w_latest.get("paths", {}),
        }
        run_written = write_run(
            book_root=book_root,
            run_id=run_id,
            tool="writer_generate",
            title=title,
            status="SUCCESS_FALLBACK" if fallback else "SUCCESS",
            role=role,
            input_obj=run_in,
            output_obj=run_out,
        )

        return {
            "ok": True,
            "book": req.book,
            "run_id": run_id,
            "status": run_written["meta"]["status"],
            "role": role,
            "title": title,
            "preview": preview,
            "paths": {
                "run_meta": run_written["paths"]["meta"],
                "run_input": run_written["paths"]["input"],
                "run_output": run_written["paths"]["output"],
                "draft": draft_rel,
                **{f"latest_{k}": v for k, v in w_latest.get("paths", {}).items()},
            },
            "writes": {"draft": w_draft, "latest": w_latest, "run": run_written.get("writes", {})},
        }
    except Exception as e:
        return {
            "ok": True,
            "book": req.book,
            "run_id": run_id,
            "status": "SUCCESS_FALLBACK",
            "role": role,
            "title": title,
            "preview": f"(fallback) Exception: {e!r}",
            "paths": {},
            "writes": {"error": repr(e)},
        }

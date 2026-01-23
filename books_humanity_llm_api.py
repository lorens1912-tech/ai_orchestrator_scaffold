from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from books_core import (
    safe_book_root,
    safe_resolve_under,
    ensure_dir,
    make_run_id,
    write_run,
    write_latest,
    atomic_write_text,
    atomic_write_json,
    read_text_safe,
)

router = APIRouter(prefix="/books/humanity", tags=["books.humanity.llm"])


class StylistReq(BaseModel):
    book: str
    path: str = Field("draft/master.txt")
    instruction: str = Field("Uhumanizuj styl: mniej ogólników, więcej konkretu. Nie zmieniaj sensu.")
    max_edits: int = Field(10, ge=1, le=50)


class StylistResp(BaseModel):
    ok: bool
    book: str
    run_id: str
    status: str
    role: str
    title: str
    preview: str
    paths: Dict[str, str]


def _fallback_report(text: str, instruction: str) -> Dict[str, Any]:
    # Bezpieczny fallback: ZERO LLM, zero blokowania requestu.
    # Nie nadpisujemy tekstu (no-op rewrite), tylko dajemy wskazówki.
    head = (text or "").strip()
    head = head[:1200] + ("..." if len(head) > 1200 else "")

    tips = [
        "Usuń powtarzane otwarcia akapitów (np. te same 6–8 słów).",
        "W każdym akapicie: 1 detal zmysłowy + 1 konkret (obiekt/liczba/nazwa).",
        "Zamiast 'ktoś/coś' — nazwij blokadę: monitoring, drzwi, karta dostępu, timer.",
        "Skróć zdania tam, gdzie robi się 'mowa o granicach' – thriller lubi tempo.",
        "Utrzymuj 1 nową informację na scenę (nie 3 naraz).",
    ]

    md = []
    md.append("## Summary")
    md.append("(fallback) LLM not configured or failed; returning guidance only.")
    md.append("")
    md.append("## Instruction")
    md.append(instruction.strip())
    md.append("")
    md.append("## Top guidance")
    md += [f"- {t}" for t in tips]
    md.append("")
    md.append("## Rewrite (no-op)")
    md.append("```text")
    md.append(head)
    md.append("```")
    report_md = "\n".join(md).strip() + "\n"

    report_json = {
        "ok": True,
        "summary": "(fallback) LLM not configured or failed; returning guidance only.",
        "instruction": instruction,
        "tips": tips,
        "rewrite": head,  # no-op
    }
    return {"md": report_md, "json": report_json, "raw": head}


@router.post("/stylist", response_model=StylistResp)
def humanity_stylist(req: StylistReq):
    run_id = make_run_id("stylist")
    role = "HUMANITY"
    title = "HUMANITY_STYLIST"

    book_root = safe_book_root(req.book)
    ensure_dir(book_root)

    try:
        src_path = safe_resolve_under(book_root, req.path)
        text = read_text_safe(src_path) if src_path.exists() else ""

        # Jeżeli LLM nie jest skonfigurowany — NATYCHMIAST fallback (zero ERROR)
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY") or ""
        if not api_key.strip():
            rep = _fallback_report(text, req.instruction)
            report_md = rep["md"]
            report_json = rep["json"]
            raw_text = rep["raw"]

            report_md_rel = f"analysis/humanity_stylist_report_{run_id}.md"
            report_json_rel = f"analysis/humanity_stylist_report_{run_id}.json"
            atomic_write_text(safe_resolve_under(book_root, report_md_rel), report_md)
            atomic_write_json(safe_resolve_under(book_root, report_json_rel), report_json)

            w_latest = write_latest(book_root, "stylist", report_md, json_obj=report_json, raw_text=raw_text)
            run_written = write_run(
                book_root=book_root,
                run_id=run_id,
                tool="humanity_stylist_llm",
                title=title,
                status="SUCCESS_FALLBACK",
                role=role,
                input_obj=req.model_dump(),
                output_obj={"ok": True, "fallback": True, "report_md": report_md_rel, "report_json": report_json_rel, "latest": w_latest.get("paths", {})},
            )

            return {
                "ok": True,
                "book": req.book,
                "run_id": run_id,
                "status": "SUCCESS_FALLBACK",
                "role": role,
                "title": title,
                "preview": report_md[:600],
                "paths": {
                    "run_meta": run_written["paths"]["meta"],
                    "run_input": run_written["paths"]["input"],
                    "run_output": run_written["paths"]["output"],
                    "report_md": report_md_rel,
                    "report_json": report_json_rel,
                    **{f"latest_{k}": v for k, v in w_latest.get("paths", {}).items()},
                },
            }

        # Jeśli kiedyś podłączysz LLM — tu będzie ścieżka “real LLM”.
        # Na razie też robimy fallback, żeby nigdy nie blokować.
        rep = _fallback_report(text, req.instruction)
        report_md = rep["md"]
        report_json = rep["json"]
        raw_text = rep["raw"]

        report_md_rel = f"analysis/humanity_stylist_report_{run_id}.md"
        report_json_rel = f"analysis/humanity_stylist_report_{run_id}.json"
        atomic_write_text(safe_resolve_under(book_root, report_md_rel), report_md)
        atomic_write_json(safe_resolve_under(book_root, report_json_rel), report_json)

        w_latest = write_latest(book_root, "stylist", report_md, json_obj=report_json, raw_text=raw_text)
        run_written = write_run(
            book_root=book_root,
            run_id=run_id,
            tool="humanity_stylist_llm",
            title=title,
            status="SUCCESS_FALLBACK",
            role=role,
            input_obj=req.model_dump(),
            output_obj={"ok": True, "fallback": True, "report_md": report_md_rel, "report_json": report_json_rel, "latest": w_latest.get("paths", {})},
        )

        return {
            "ok": True,
            "book": req.book,
            "run_id": run_id,
            "status": "SUCCESS_FALLBACK",
            "role": role,
            "title": title,
            "preview": report_md[:600],
            "paths": {
                "run_meta": run_written["paths"]["meta"],
                "run_input": run_written["paths"]["input"],
                "run_output": run_written["paths"]["output"],
                "report_md": report_md_rel,
                "report_json": report_json_rel,
                **{f"latest_{k}": v for k, v in w_latest.get("paths", {}).items()},
            },
        }

    except Exception as e:
        # Nigdy ERROR — zawsze SUCCESS_FALLBACK
        report_md = f"## Summary\n(fallback) Exception: {e!r}\n"
        w_latest = write_latest(book_root, "stylist", report_md, json_obj={"ok": True, "fallback": True, "error": repr(e)}, raw_text=report_md)
        run_written = write_run(
            book_root=book_root,
            run_id=run_id,
            tool="humanity_stylist_llm",
            title=title,
            status="SUCCESS_FALLBACK",
            role=role,
            input_obj=req.model_dump(),
            output_obj={"ok": True, "fallback": True, "error": repr(e), "latest": w_latest.get("paths", {})},
        )
        return {
            "ok": True,
            "book": req.book,
            "run_id": run_id,
            "status": "SUCCESS_FALLBACK",
            "role": role,
            "title": title,
            "preview": report_md,
            "paths": {"run_meta": run_written["paths"]["meta"], "run_input": run_written["paths"]["input"], "run_output": run_written["paths"]["output"], **{f"latest_{k}": v for k, v in w_latest.get("paths", {}).items()}},
        }

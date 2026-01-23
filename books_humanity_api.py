from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

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

router = APIRouter(prefix="/books/humanity", tags=["books.humanity"])


class HumanityCheckReq(BaseModel):
    book: str
    path: Optional[str] = Field("draft/master.txt")
    text: Optional[str] = None


class HumanityCheckResp(BaseModel):
    ok: bool
    book: str
    run_id: str
    status: str
    role: str
    title: str
    preview: str
    paths: Dict[str, str]
    writes: Dict[str, Any]


_AIISH_PHRASES = [
    "warto zauważyć",
    "podsumowując",
    "należy pamiętać",
    "w dzisiejszych czasach",
    "bez wątpienia",
    "co więcej",
    "warto dodać",
]


def _load_text(book_root, req: HumanityCheckReq) -> Tuple[str, str]:
    if req.text and req.text.strip():
        return req.text, "(inline)"
    rel = req.path or "draft/master.txt"
    p = safe_resolve_under(book_root, rel)
    if p.exists() and p.is_file():
        return read_text_safe(p), rel
    return "", rel


def _find_flags(text: str) -> List[str]:
    t = text.lower()
    flags: List[str] = []
    for ph in _AIISH_PHRASES:
        if ph in t:
            flags.append(f"Możliwy schematyczny zwrot: **{ph}**")
    if re.search(r"\n{4,}", text):
        flags.append("Dziwne przerwy: 4+ pustych linii z rzędu.")
    if text.count("—") + text.count("–") == 0 and len(text) > 1500:
        flags.append("Brak pauz/dialogów (—/–). Jeśli to proza sceniczna, może być zbyt monolityczne.")
    if re.search(r"\b(który|która|które)\b", t) and len(text) > 2000:
        flags.append("Sprawdź nadmiar zdań względnych (który/która/które) – mogą wydłużać rytm.")
    return flags[:25]


@router.post("/check", response_model=HumanityCheckResp)
def humanity_check(req: HumanityCheckReq):
    run_id = make_run_id("humanity")
    role = "HUMANITY"
    title = "HUMANITY_CHECK"

    try:
        book_root = safe_book_root(req.book)
        ensure_dir(book_root)

        text, source = _load_text(book_root, req)
        fallback = not bool(text.strip())

        flags = _find_flags(text) if text else []
        md = ["# Humanity report", f"- book: `{req.book}`", f"- run_id: `{run_id}`", f"- source: `{source}`", ""]
        if fallback:
            md += ["(fallback) No input text found. Provide `text` or ensure `draft/master.txt` exists.", ""]

        md += ["## Flags"]
        md += [f"- {f}" for f in flags] if flags else ["- none"]

        md += [
            "",
            "## Micro-tuning (bez LLM)",
            "- Zamień 1–2 ogólne zdania na konkret sensoryczny (dźwięk, zapach, faktura).",
            "- Daj czytelnikowi wybór interpretacji: mniej tłumaczenia, więcej obserwacji.",
            "- Jeśli tempo siada: skróć 3 najdłuższe zdania w scenie o ~30%.",
        ]
        report_md = "\n".join(md).strip() + "\n"

        report_md_rel = f"analysis/humanity_report_{run_id}.md"
        report_md_path = safe_resolve_under(book_root, report_md_rel)
        w_rmd = atomic_write_text(report_md_path, report_md)

        # latest (JSON stub auto)
        w_latest = write_latest(book_root, "humanity", report_md, json_obj=None, raw_text=report_md)

        run_out = {
            "ok": True,
            "tool": "humanity",
            "fallback": fallback,
            "report": {"md": report_md_rel},
            "latest": w_latest.get("paths", {}),
            "flags_count": len(flags),
        }
        run_in = {"book": req.book, "path": req.path, "source": source, "text_provided": bool(req.text)}
        run_written = write_run(
            book_root=book_root,
            run_id=run_id,
            tool="humanity_check",
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
            "preview": report_md[:500],
            "paths": {
                "run_meta": run_written["paths"]["meta"],
                "run_input": run_written["paths"]["input"],
                "run_output": run_written["paths"]["output"],
                "report_md": report_md_rel,
                **{f"latest_{k}": v for k, v in w_latest.get("paths", {}).items()},
            },
            "writes": {
                "report_md": w_rmd,
                "latest": w_latest,
                "run": run_written.get("writes", {}),
            },
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

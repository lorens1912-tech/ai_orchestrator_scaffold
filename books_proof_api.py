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
    atomic_write_json,
    write_run,
    write_latest,
    read_text_safe,
)

router = APIRouter(prefix="/books/proof", tags=["books.proof"])


class ProofCheckReq(BaseModel):
    book: str = Field(..., description="Book id")
    path: Optional[str] = Field("draft/master.txt", description="Relative path under book root")
    text: Optional[str] = Field(None, description="Optional direct text; overrides path if provided")
    max_issues: int = Field(80, ge=1, le=500)


class ProofCheckResp(BaseModel):
    ok: bool
    book: str
    run_id: str
    status: str
    role: str
    title: str
    preview: str
    paths: Dict[str, str]
    writes: Dict[str, Any]


_WORD_RE = re.compile(r"\b[\wĄĆĘŁŃÓŚŹŻąćęłńóśźż]+\b", re.UNICODE)
_REPEAT_WORD_RE = re.compile(r"\b([\wĄĆĘŁŃÓŚŹŻąćęłńóśźż]+)\s+\1\b", re.IGNORECASE | re.UNICODE)


def _load_text(book_root, req: ProofCheckReq) -> Tuple[str, str]:
    if req.text and req.text.strip():
        return req.text, "(inline)"
    rel = req.path or "draft/master.txt"
    p = safe_resolve_under(book_root, rel)
    if p.exists() and p.is_file():
        return read_text_safe(p), rel
    return "", rel


def _proof_issues(text: str, max_issues: int) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    lines = text.splitlines() or [""]

    def add(tp: str, line_no: int, snippet: str, msg: str):
        if len(issues) >= max_issues:
            return
        issues.append({"type": tp, "line": line_no, "snippet": snippet[:180], "message": msg})

    for i, line in enumerate(lines, start=1):
        if line.rstrip("\n\r\t ") != line:
            add("TRAILING_WS", i, line, "Trailing whitespace")

        if "  " in line:
            add("DOUBLE_SPACE", i, line, "Double space found")

        if re.search(r"\s+[.,;:!?]", line):
            add("SPACE_BEFORE_PUNCT", i, line, "Space before punctuation")

        if re.search(r"[.,;:!?]{2,}", line):
            add("MULTI_PUNCT", i, line, "Multiple punctuation marks in a row")

        m = _REPEAT_WORD_RE.search(line)
        if m:
            add("REPEAT_WORD", i, line, f"Repeated word: '{m.group(1)}'")

    # Global checks
    if "\t" in text:
        add("TAB_CHAR", 0, "\\t", "Tab characters present (prefer spaces)")

    if "\u00A0" in text:
        add("NBSP", 0, "NBSP", "Non-breaking spaces present")

    return issues


@router.post("/check", response_model=ProofCheckResp)
def proof_check(req: ProofCheckReq):
    run_id = make_run_id("proof")
    role = "PROOF"
    title = "PROOF_CHECK"

    try:
        book_root = safe_book_root(req.book)
        ensure_dir(book_root)

        text, source = _load_text(book_root, req)
        fallback = not bool(text.strip())

        issues = _proof_issues(text, req.max_issues) if text else []
        words = _WORD_RE.findall(text) if text else []
        stats = {
            "chars": len(text),
            "lines": len(text.splitlines()) if text else 0,
            "words": len(words),
            "issues": len(issues),
            "source": source,
        }

        report_json = {"ok": True, "tool": "proof", "stats": stats, "issues": issues}
        md_lines = [
            f"# Proof report",
            f"- book: `{req.book}`",
            f"- run_id: `{run_id}`",
            f"- source: `{source}`",
            f"- chars/words/lines: **{stats['chars']} / {stats['words']} / {stats['lines']}**",
            f"- issues: **{stats['issues']}**",
            "",
        ]
        if fallback:
            md_lines += ["(fallback) No input text found. Provide `text` or ensure `draft/master.txt` exists.", ""]

        if issues:
            md_lines.append("## Issues (first N)")
            for it in issues[: min(len(issues), 40)]:
                md_lines.append(f"- L{it['line']}: **{it['type']}** — {it['message']}  \n  `{it['snippet']}`")
        else:
            md_lines.append("## Issues")
            md_lines.append("- none")

        report_md = "\n".join(md_lines).strip() + "\n"

        # per-run report files
        report_md_rel = f"analysis/proof_report_{run_id}.md"
        report_json_rel = f"analysis/proof_report_{run_id}.json"
        report_md_path = safe_resolve_under(book_root, report_md_rel)
        report_json_path = safe_resolve_under(book_root, report_json_rel)

        w_rmd = atomic_write_text(report_md_path, report_md)
        w_rjson = atomic_write_json(report_json_path, report_json)

        # latest
        w_latest = write_latest(book_root, "proof", report_md, json_obj=report_json, raw_text=report_md)

        # run log
        run_out = {
            "ok": True,
            "tool": "proof",
            "fallback": fallback,
            "report": {"md": report_md_rel, "json": report_json_rel},
            "latest": w_latest.get("paths", {}),
            "stats": stats,
        }
        run_in = {"book": req.book, "path": req.path, "source": source, "max_issues": req.max_issues, "text_provided": bool(req.text)}
        run_written = write_run(
            book_root=book_root,
            run_id=run_id,
            tool="proof_check",
            title=title,
            status="SUCCESS_FALLBACK" if fallback else "SUCCESS",
            role=role,
            input_obj=run_in,
            output_obj=run_out,
        )

        preview = report_md[:500]
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
                "report_md": report_md_rel,
                "report_json": report_json_rel,
                **{f"latest_{k}": v for k, v in w_latest.get("paths", {}).items()},
            },
            "writes": {
                "report_md": w_rmd,
                "report_json": w_rjson,
                "latest": w_latest,
                "run": run_written.get("writes", {}),
            },
        }
    except Exception as e:
        # hard fallback: still 200
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

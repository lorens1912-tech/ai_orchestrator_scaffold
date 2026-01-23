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

router = APIRouter(prefix="/books/critic", tags=["books.critic"])


class CriticCheckReq(BaseModel):
    book: str
    path: Optional[str] = Field("draft/master.txt")
    text: Optional[str] = None
    max_notes: int = Field(30, ge=1, le=200)


class CriticCheckResp(BaseModel):
    ok: bool
    book: str
    run_id: str
    status: str
    role: str
    title: str
    preview: str
    paths: Dict[str, str]
    writes: Dict[str, Any]


_SENT_RE = re.compile(r"[^.!?…]+[.!?…]", re.UNICODE)
_WORD_RE = re.compile(r"\b[\wĄĆĘŁŃÓŚŹŻąćęłńóśźż]+\b", re.UNICODE)


def _load_text(book_root, req: CriticCheckReq) -> Tuple[str, str]:
    if req.text and req.text.strip():
        return req.text, "(inline)"
    rel = req.path or "draft/master.txt"
    p = safe_resolve_under(book_root, rel)
    if p.exists() and p.is_file():
        return read_text_safe(p), rel
    return "", rel


def _metrics(text: str) -> Dict[str, Any]:
    lines = text.splitlines()
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    sents = _SENT_RE.findall(text) if text else []
    words = _WORD_RE.findall(text) if text else []

    avg_sent_len = (len(words) / len(sents)) if sents else 0.0
    avg_para_len = (len(words) / len(paras)) if paras else 0.0

    exclam = text.count("!") if text else 0
    quest = text.count("?") if text else 0
    dash_dialogue = sum(1 for ln in lines if ln.lstrip().startswith(("–", "-")))

    return {
        "chars": len(text),
        "lines": len(lines),
        "paragraphs": len(paras),
        "sentences": len(sents),
        "words": len(words),
        "avg_sentence_words": round(avg_sent_len, 2),
        "avg_paragraph_words": round(avg_para_len, 2),
        "exclamations": exclam,
        "questions": quest,
        "dialogue_lines": dash_dialogue,
    }


def _notes(m: Dict[str, Any], max_notes: int) -> List[str]:
    notes: List[str] = []

    if m["avg_sentence_words"] and m["avg_sentence_words"] > 22:
        notes.append("Zdania są długie (avg > 22 słowa). Rozważ cięcia / rytm.")

    if m["paragraphs"] and m["avg_paragraph_words"] > 140:
        notes.append("Akapity są ciężkie (średnio > 140 słów). Rozbij dla tempa i oddechu.")

    if m["exclamations"] > max(8, m["sentences"] // 8):
        notes.append("Dużo wykrzykników. Zostaw je na momenty kulminacyjne.")

    if m["dialogue_lines"] == 0 and m["words"] > 400:
        notes.append("Brak dialogów w dłuższym fragmencie. Jeśli to scena akcji/relacji, dialog może pomóc.")

    if m["questions"] == 0 and m["words"] > 600:
        notes.append("Zero pytań. Czasem jedno zdanie-hak (pytanie) buduje napięcie.")

    # Generic craft nudges
    notes += [
        "Sprawdź, czy każda scena ma: cel, konflikt, zmianę (choćby mikro).",
        "Wyrzuć 1–2 zdania ogólne na akapit, zastąp detalem zmysłowym (konkret!).",
        "Jeśli to thriller: dopilnuj 'deadline' albo stawki w 1–2 zdaniach na początku sceny.",
    ]

    return notes[:max_notes]


@router.post("/check", response_model=CriticCheckResp)
def critic_check(req: CriticCheckReq):
    run_id = make_run_id("critic")
    role = "CRITIC"
    title = "CRITIC_CHECK"

    try:
        book_root = safe_book_root(req.book)
        ensure_dir(book_root)

        text, source = _load_text(book_root, req)
        fallback = not bool(text.strip())

        m = _metrics(text) if text else {"chars": 0, "words": 0}
        notes = _notes(m if isinstance(m, dict) else {}, req.max_notes) if text else []

        report_json = {"ok": True, "tool": "critic", "source": source, "metrics": m, "notes": notes}
        md = ["# Critic report", f"- book: `{req.book}`", f"- run_id: `{run_id}`", f"- source: `{source}`", ""]
        if fallback:
            md += ["(fallback) No input text found. Provide `text` or ensure `draft/master.txt` exists.", ""]

        md += ["## Metrics", *[f"- **{k}**: {v}" for k, v in m.items()], "", "## Notes"]
        if notes:
            md += [f"- {n}" for n in notes]
        else:
            md += ["- (no notes)"]

        report_md = "\n".join(md).strip() + "\n"

        report_md_rel = f"analysis/critic_report_{run_id}.md"
        report_json_rel = f"analysis/critic_report_{run_id}.json"
        report_md_path = safe_resolve_under(book_root, report_md_rel)
        report_json_path = safe_resolve_under(book_root, report_json_rel)

        w_rmd = atomic_write_text(report_md_path, report_md)
        w_rjson = atomic_write_json(report_json_path, report_json)

        w_latest = write_latest(book_root, "critic", report_md, json_obj=report_json, raw_text=report_md)

        run_out = {
            "ok": True,
            "tool": "critic",
            "fallback": fallback,
            "report": {"md": report_md_rel, "json": report_json_rel},
            "latest": w_latest.get("paths", {}),
            "metrics": m,
        }
        run_in = {"book": req.book, "path": req.path, "source": source, "max_notes": req.max_notes, "text_provided": bool(req.text)}
        run_written = write_run(
            book_root=book_root,
            run_id=run_id,
            tool="critic_check",
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

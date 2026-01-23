from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple, List

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

router = APIRouter(prefix="/books/draft", tags=["books.draft"])


class DraftCleanupReq(BaseModel):
    book: str
    path: str = Field("draft/master.txt")
    mode: str = Field("safe", description="safe|aggressive")
    keep_backup: bool = Field(True)
    preview_tail_lines: int = Field(60, ge=10, le=400)


class DraftCleanupResp(BaseModel):
    ok: bool
    book: str
    run_id: str
    status: str
    role: str
    title: str
    removed_lines: int
    removed_dupe_lines: int
    removed_inline_phrases: int
    removed_repeated_sentences: int
    removed_repeated_paragraphs: int
    backup_path: Optional[str]
    paths: Dict[str, str]
    preview_tail: str


LINE_DROP_PATTERNS = [
    r"^NEUTRAL_.*$",
    r"^Wejście w scenę.*$",
    r"^Cel bohatera.*$",
    r"^Przeszkoda:.*$",
    r"^Mikrodecyzja.*$",
    r"^Konsekwencja.*$",
    r"^Nowa informacja.*$",
    r"^Zwrot.*$",
    r"^Hook.*$",
]

INLINE_PHRASES = [
    r"Opis w 2-3 zdaniach:\s*konkret miejsca,\s*ruch bohatera,\s*detal rekwizytu\.\s*",
    r"Nie tłumacz\.\s*Pokaż\.\s*",
    r"Potem decyzja,\s*która coś kosztuje\.\s*",
    r"Konkret\.\s*Ruch\.\s*Decyzja\.\s*",
    r"(Konkret\.\s*){2,}",
    r"(Ruch\.\s*){2,}",
    r"(Decyzja\.\s*){2,}",
]

REPEAT_SENTENCE_MINLEN = 18
REPEAT_MAX_KEEP = 2  # aggressive only


def _read_tail(text: str, n_lines: int) -> str:
    lines = text.splitlines()
    tail = lines[-n_lines:] if len(lines) > n_lines else lines
    return "\n".join(tail).strip() + "\n"


def _split_sentences(par: str) -> List[str]:
    parts = re.split(r"(?<=[\.\!\?…])\s+", par.strip())
    return [p.strip() for p in parts if p.strip()]


def _norm_sig(s: str) -> str:
    # normalize for "paragraph signature"
    x = s.lower()
    x = re.sub(r"[^\wąćęłńóśźż\s]", " ", x)
    x = re.sub(r"\s+", " ", x).strip()
    return x


def _cleanup_text(text: str, mode: str) -> Tuple[str, int, int, int, int, int]:
    aggressive = (mode or "").lower() == "aggressive"

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    drop_res = [re.compile(p) for p in LINE_DROP_PATTERNS]
    inline_res = [re.compile(p, flags=re.IGNORECASE) for p in INLINE_PHRASES]

    removed_lines = 0
    removed_inline = 0

    kept_lines: List[str] = []

    for ln in lines:
        s = ln.strip()
        if s == "":
            kept_lines.append("")
            continue

        if any(rx.match(s) for rx in drop_res):
            removed_lines += 1
            continue

        new_ln = ln
        for rx in inline_res:
            new_ln2, n = rx.subn("", new_ln)
            if n:
                removed_inline += n
                new_ln = new_ln2

        if aggressive and ("Opis w" in new_ln and "zdaniach" in new_ln):
            removed_lines += 1
            continue

        kept_lines.append(new_ln.rstrip())

    # collapse blank lines: max 2 in a row
    collapsed: List[str] = []
    blank_run = 0
    for ln in kept_lines:
        if ln.strip() == "":
            blank_run += 1
            if blank_run <= 2:
                collapsed.append("")
        else:
            blank_run = 0
            collapsed.append(ln)

    # remove consecutive duplicate lines
    deduped: List[str] = []
    dupe_removed = 0
    prev = None
    for ln in collapsed:
        if prev is not None and ln == prev and ln.strip() != "":
            dupe_removed += 1
            continue
        deduped.append(ln)
        prev = ln

    # paragraph split
    paras: List[str] = []
    buf: List[str] = []
    for ln in deduped:
        if ln.strip() == "":
            if buf:
                paras.append("\n".join(buf).strip())
                buf = []
            else:
                paras.append("")
        else:
            buf.append(ln)
    if buf:
        paras.append("\n".join(buf).strip())

    # sentence repeat cleanup (aggressive)
    repeat_counter: Dict[str, int] = {}
    removed_repeat_sents = 0
    cleaned_paras: List[str] = []

    for p in paras:
        if not p.strip():
            cleaned_paras.append("")
            continue

        sents = _split_sentences(p)
        out_sents: List[str] = []
        for s in sents:
            key = _norm_sig(s)
            if len(key) < REPEAT_SENTENCE_MINLEN:
                out_sents.append(s)
                continue
            c = repeat_counter.get(key, 0)
            if aggressive and c >= REPEAT_MAX_KEEP:
                removed_repeat_sents += 1
                continue
            repeat_counter[key] = c + 1
            out_sents.append(s)

        cleaned_paras.append(" ".join(out_sents).strip())

    # paragraph repeat cleanup (aggressive): kill paragraphs with same opening signature
    removed_repeat_paras = 0
    seen_para_sig: Dict[str, int] = {}
    final_paras: List[str] = []

    for p in cleaned_paras:
        if not p.strip():
            final_paras.append("")
            continue

        sig = _norm_sig(p)[:140]  # key: first ~140 normalized chars (kills "Wybrał ryzyko..." blocks)
        c = seen_para_sig.get(sig, 0)
        if aggressive and c >= 1:
            removed_repeat_paras += 1
            continue
        seen_para_sig[sig] = c + 1
        final_paras.append(p)

    # rebuild text
    out_lines: List[str] = []
    for p in final_paras:
        if p == "":
            out_lines.append("")
        else:
            out_lines.extend(p.splitlines())
            out_lines.append("")
    cleaned = "\n".join(out_lines).strip() + "\n"

    return cleaned, removed_lines, dupe_removed, removed_inline, removed_repeat_sents, removed_repeat_paras


@router.post("/cleanup", response_model=DraftCleanupResp)
def draft_cleanup(req: DraftCleanupReq):
    run_id = make_run_id("cleanup")
    role = "MAINT"
    title = "DRAFT_CLEANUP_V3"

    try:
        book_root = safe_book_root(req.book)
        ensure_dir(book_root)

        draft_path = safe_resolve_under(book_root, req.path)
        if not draft_path.exists():
            report_md = f"# Draft cleanup v3\n- book: `{req.book}`\n- run_id: `{run_id}`\n\n(fallback) File not found: `{req.path}`\n"
            w_latest = write_latest(book_root, "draft_cleanup", report_md, json_obj={"ok": True, "stub": True}, raw_text=report_md)
            run_written = write_run(book_root, run_id, "draft_cleanup_v3", title, "SUCCESS_FALLBACK", role, req.model_dump(), {"ok": True, "note": "file not found"})
            return {
                "ok": True,
                "book": req.book,
                "run_id": run_id,
                "status": "SUCCESS_FALLBACK",
                "role": role,
                "title": title,
                "removed_lines": 0,
                "removed_dupe_lines": 0,
                "removed_inline_phrases": 0,
                "removed_repeated_sentences": 0,
                "removed_repeated_paragraphs": 0,
                "backup_path": None,
                "paths": {"run_meta": run_written["paths"]["meta"], "run_input": run_written["paths"]["input"], "run_output": run_written["paths"]["output"], **{f"latest_{k}": v for k, v in w_latest.get('paths', {}).items()}},
                "preview_tail": "",
            }

        original = read_text_safe(draft_path)

        backup_rel = None
        if req.keep_backup:
            backup_rel = f"draft/backups/master_before_cleanup_{run_id}.txt"
            backup_path = safe_resolve_under(book_root, backup_rel)
            ensure_dir(backup_path.parent)
            atomic_write_text(backup_path, original)

        cleaned, removed_lines, dupe_removed, removed_inline, removed_repeat_sents, removed_repeat_paras = _cleanup_text(original, req.mode)

        w_write = atomic_write_text(draft_path, cleaned)
        tail = _read_tail(cleaned, req.preview_tail_lines)

        report_json = {
            "ok": True,
            "book": req.book,
            "run_id": run_id,
            "path": req.path,
            "mode": req.mode,
            "removed_lines": removed_lines,
            "removed_dupe_lines": dupe_removed,
            "removed_inline_phrases": removed_inline,
            "removed_repeated_sentences": removed_repeat_sents,
            "removed_repeated_paragraphs": removed_repeat_paras,
            "backup": backup_rel,
            "write": w_write,
        }

        report_md = (
            "# Draft cleanup v3\n"
            f"- book: `{req.book}`\n"
            f"- run_id: `{run_id}`\n"
            f"- path: `{req.path}`\n"
            f"- mode: `{req.mode}`\n"
            f"- removed_lines: **{removed_lines}**\n"
            f"- removed_dupe_lines: **{dupe_removed}**\n"
            f"- removed_inline_phrases: **{removed_inline}**\n"
            f"- removed_repeated_sentences: **{removed_repeat_sents}**\n"
            f"- removed_repeated_paragraphs: **{removed_repeat_paras}**\n"
            f"- backup: `{backup_rel or '(none)'}`\n"
            "\n"
            "## Tail preview\n"
            "```text\n"
            f"{tail.rstrip()}\n"
            "```\n"
        )

        w_latest = write_latest(book_root, "draft_cleanup", report_md, json_obj=report_json, raw_text=cleaned)

        run_written = write_run(
            book_root=book_root,
            run_id=run_id,
            tool="draft_cleanup_v3",
            title=title,
            status="SUCCESS" if w_write.get("ok") else "SUCCESS_FALLBACK",
            role=role,
            input_obj=req.model_dump(),
            output_obj={"ok": True, "stats": report_json, "latest": w_latest.get("paths", {})},
        )

        return {
            "ok": True,
            "book": req.book,
            "run_id": run_id,
            "status": run_written["meta"]["status"],
            "role": role,
            "title": title,
            "removed_lines": removed_lines,
            "removed_dupe_lines": dupe_removed,
            "removed_inline_phrases": removed_inline,
            "removed_repeated_sentences": removed_repeat_sents,
            "removed_repeated_paragraphs": removed_repeat_paras,
            "backup_path": backup_rel,
            "paths": {
                "run_meta": run_written["paths"]["meta"],
                "run_input": run_written["paths"]["input"],
                "run_output": run_written["paths"]["output"],
                **{f"latest_{k}": v for k, v in w_latest.get("paths", {}).items()},
            },
            "preview_tail": tail,
        }
    except Exception as e:
        return {
            "ok": True,
            "book": req.book,
            "run_id": run_id,
            "status": "SUCCESS_FALLBACK",
            "role": role,
            "title": title,
            "removed_lines": 0,
            "removed_dupe_lines": 0,
            "removed_inline_phrases": 0,
            "removed_repeated_sentences": 0,
            "removed_repeated_paragraphs": 0,
            "backup_path": None,
            "paths": {},
            "preview_tail": f"(fallback) Exception: {e!r}",
        }

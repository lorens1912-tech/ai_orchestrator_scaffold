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
    atomic_write_json,
    write_run,
    write_latest,
    read_text_safe,
)

router = APIRouter(prefix="/books/architect", tags=["books.architect"])


class ArchitectRunReq(BaseModel):
    book: str
    path: Optional[str] = Field("draft/master.txt")
    text: Optional[str] = None
    goal: Optional[str] = None
    chunk_hint_words: int = Field(800, ge=200, le=5000)


class ArchitectRunResp(BaseModel):
    ok: bool
    book: str
    run_id: str
    status: str
    role: str
    title: str
    preview: str
    paths: Dict[str, str]
    writes: Dict[str, Any]


def _load_text(book_root, req: ArchitectRunReq) -> Tuple[str, str]:
    if req.text and req.text.strip():
        return req.text, "(inline)"
    rel = req.path or "draft/master.txt"
    p = safe_resolve_under(book_root, rel)
    if p.exists() and p.is_file():
        return read_text_safe(p), rel
    return "", rel


def _tail_sentences(text: str, n: int = 12) -> List[str]:
    t = text.strip()
    if not t:
        return []
    # take last ~3000 chars and split into sentences
    chunk = t[-3000:]
    sents = re.split(r"(?<=[\.\!\?…])\s+", chunk)
    sents = [s.strip() for s in sents if s.strip()]
    return sents[-n:]


def _extract_entities(text: str) -> List[str]:
    # Proper nouns (very rough)
    nouns = re.findall(r"\b[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]{2,}\b", text)
    out = []
    for n in nouns:
        if n.lower() in {"nie", "nowy", "dialog", "konkret", "ruch", "decyzja"}:
            continue
        if n not in out:
            out.append(n)
        if len(out) >= 6:
            break
    return out


def _extract_specifics(tail_sents: List[str]) -> Dict[str, str]:
    txt = " ".join(tail_sents)

    # detect concrete objects
    obj_candidates = []
    for pat in [
        r"\b(karta dostępu|pendrive|koperta|klucz|telefon|monitoring|drzwi|kamera|kod|hasło)\b",
        r"\b(pęknięt\w+ szk\w+|uchylone drzwi|nieznany numer)\b",
    ]:
        for m in re.finditer(pat, txt, flags=re.IGNORECASE):
            obj_candidates.append(m.group(0))

    obj = obj_candidates[-1] if obj_candidates else "karta dostępu"

    # detect place cue
    place = "korytarz biurowca"
    if re.search(r"\b(hotel|hol)\b", txt, flags=re.IGNORECASE):
        place = "hol hotelu"
    elif re.search(r"\b(parking)\b", txt, flags=re.IGNORECASE):
        place = "parking podziemny"
    elif re.search(r"\b(kawiarnia)\b", txt, flags=re.IGNORECASE):
        place = "kawiarnia"

    # detect stake (simple)
    stake = "wpadka oznacza konsekwencje: utratę przewagi i ryzyko wpadki"
    if re.search(r"reputac", txt, flags=re.IGNORECASE):
        stake = "stawka: reputacja i bezpieczeństwo"
    if re.search(r"pieni", txt, flags=re.IGNORECASE):
        stake = "stawka: pieniądze i bezpieczeństwo"
    if re.search(r"bezpiecz", txt, flags=re.IGNORECASE):
        stake = "stawka: bezpieczeństwo"

    # detect blocker (try to make it concrete)
    blocker = "system kontroli dostępu nie przepuszcza (wymaga uprawnień)"
    if re.search(r"\b(kamera|monitoring)\b", txt, flags=re.IGNORECASE):
        blocker = "monitoring / kamera łapie ruch — trzeba działać cicho"
    if re.search(r"\b(drzw\w+)\b", txt, flags=re.IGNORECASE) and re.search(r"\b(uchyl|zamk)\b", txt, flags=re.IGNORECASE):
        blocker = "drzwi są uchylone/zamknięte — wejście zostawia ślad"

    return {"object": obj, "place": place, "stake": stake, "blocker": blocker}


def _suggest_next(goal: Optional[str], tail_sents: List[str], entities: List[str]) -> Dict[str, Any]:
    last_sentence = tail_sents[-1] if tail_sents else ""
    specifics = _extract_specifics(tail_sents)

    scene_goal = (goal.strip() if isinstance(goal, str) and goal.strip() else None) or "Ujawnić nową informację i podnieść stawkę."
    conflict = f"Przeszkoda (konkret): {specifics['blocker']}."
    stakes = f"Stawka (konkret): {specifics['stake']}."
    setting = f"Miejsce: {specifics['place']}. Rekwizyt: {specifics['object']}."
    hook = "Zakończ zwrotem: sygnał/wiadomość/odkrycie, które zmienia plan."

    beats = [
        "1) Wejście: ruch + detal zmysłowy.",
        f"2) Cel: {scene_goal}",
        f"3) Przeszkoda: {specifics['blocker']}",
        "4) Mikrodecyzja: ryzyko w 1 ruchu.",
        "5) Konsekwencja: natychmiastowe pogorszenie.",
        "6) Nowa informacja: 1 konkret (nazwa/obiekt/liczba).",
        "7) Zwrot: przewaga przechodzi na drugą stronę.",
        "8) Hook: krótki, twardy.",
    ]

    return {
        "scene_goal": scene_goal,
        "conflict": conflict,
        "stakes": stakes,
        "setting": setting,
        "hook": hook,
        "beats": beats,
        "carry_over": {"last_sentence": last_sentence, "entities": entities, "specifics": specifics},
    }


@router.post("/run", response_model=ArchitectRunResp)
def architect_run(req: ArchitectRunReq):
    run_id = make_run_id("architect")
    role = "ARCHITEKT"
    title = "ARCHITECT_BRIEF"

    try:
        book_root = safe_book_root(req.book)
        ensure_dir(book_root)

        text, source = _load_text(book_root, req)
        fallback = not bool(text.strip())

        tail_sents = _tail_sentences(text, 12)
        entities = _extract_entities(" ".join(tail_sents))

        plan = _suggest_next(req.goal, tail_sents, entities)

        report_json = {
            "ok": True,
            "tool": "architect",
            "book": req.book,
            "run_id": run_id,
            "source": source,
            "fallback": fallback,
            "chunk_hint_words": req.chunk_hint_words,
            "plan": plan,
        }

        md = []
        md.append("# Architect brief (concrete)")
        md.append(f"- book: `{req.book}`")
        md.append(f"- run_id: `{run_id}`")
        md.append(f"- source: `{source}`")
        md.append(f"- target_words_hint: **{req.chunk_hint_words}**")
        md.append("")
        md.append("## Carry-over")
        md.append(f"- last_sentence: `{plan['carry_over']['last_sentence']}`")
        md.append(f"- entities: {', '.join(plan['carry_over']['entities']) if plan['carry_over']['entities'] else '(none)'}")
        sp = plan["carry_over"]["specifics"]
        md.append(f"- specifics: place=`{sp['place']}`, object=`{sp['object']}`, stake=`{sp['stake']}`, blocker=`{sp['blocker']}`")
        md.append("")
        md.append("## Next chunk plan")
        md.append(f"- **Scene goal:** {plan['scene_goal']}")
        md.append(f"- **Conflict:** {plan['conflict']}")
        md.append(f"- **Stakes:** {plan['stakes']}")
        md.append(f"- **Setting:** {plan['setting']}")
        md.append(f"- **Hook:** {plan['hook']}")
        md.append("")
        md.append("## Beats")
        md += [f"- {b}" for b in plan["beats"]]
        md.append("")
        md.append("## Writer instruction")
        md.append("- Zero streszczeń. Pokaż ruch i koszt decyzji. Jeden konkret w środku sceny.")
        report_md = "\n".join(md).strip() + "\n"

        report_md_rel = f"analysis/architect_report_{run_id}.md"
        report_json_rel = f"analysis/architect_report_{run_id}.json"
        w_rmd = atomic_write_text(safe_resolve_under(book_root, report_md_rel), report_md)
        w_rjson = atomic_write_json(safe_resolve_under(book_root, report_json_rel), report_json)

        w_latest = write_latest(book_root, "architect", report_md, json_obj=report_json, raw_text=report_md)

        run_written = write_run(
            book_root=book_root,
            run_id=run_id,
            tool="architect_run",
            title=title,
            status="SUCCESS_FALLBACK" if fallback else "SUCCESS",
            role=role,
            input_obj=req.model_dump(),
            output_obj={"ok": True, "fallback": fallback, "report": {"md": report_md_rel, "json": report_json_rel}, "latest": w_latest.get("paths", {})},
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
            "writes": {"report_md": w_rmd, "report_json": w_rjson, "latest": w_latest, "run": run_written.get("writes", {})},
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

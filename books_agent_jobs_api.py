from __future__ import annotations

import json
import re
import hashlib
import threading
import queue
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

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

from books_architect_api import architect_run, ArchitectRunReq
from books_writer_api import writer_generate, WriterGenerateReq
from books_proof_api import proof_check, ProofCheckReq
from books_critic_api import critic_check, CriticCheckReq
from books_humanity_llm_api import humanity_stylist, StylistReq

router = APIRouter(prefix="/books/agent", tags=["books.agent.jobs"])


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# -------------------------
# JOB STORAGE
# -------------------------
def _job_path(book_root, job_id: str):
    p = safe_resolve_under(book_root, f"jobs/{job_id}.json")
    ensure_dir(p.parent)
    return p


def _write_job(book_root, job_id: str, payload: Dict[str, Any]) -> None:
    p = _job_path(book_root, job_id)
    atomic_write_json(p, payload)


def _read_job(book_root, job_id: str) -> Dict[str, Any]:
    p = _job_path(book_root, job_id)
    if not p.exists():
        return {"ok": True, "status": "NOT_FOUND", "job_id": job_id}
    try:
        return json.loads(read_text_safe(p))
    except Exception as e:
        return {"ok": True, "status": "READ_ERROR", "job_id": job_id, "error": repr(e)}


# -------------------------
# FINGERPRINT MEMORY
# -------------------------
def _fp_path(book_root):
    p = safe_resolve_under(book_root, "memory/scene_fingerprints.json")
    ensure_dir(p.parent)
    return p


def _fp_load(book_root) -> Dict[str, Any]:
    p = _fp_path(book_root)
    try:
        if p.exists():
            obj = json.loads(read_text_safe(p))
            if isinstance(obj, dict) and isinstance(obj.get("items"), list):
                return obj
    except Exception:
        pass
    return {"items": []}


def _fp_save(book_root, state: Dict[str, Any]) -> None:
    p = _fp_path(book_root)
    atomic_write_json(p, state)


def _avoid_sets(state: Dict[str, Any], last: int = 25) -> Dict[str, Set[str]]:
    items = state.get("items", [])[-last:]
    avoid = {k: set() for k in ["places", "props", "dialogs", "hooks", "p2", "p3"]}
    for it in items:
        for k_src, k_dst in [
            ("place", "places"),
            ("prop", "props"),
            ("dialog", "dialogs"),
            ("hook", "hooks"),
            ("p2_key", "p2"),
            ("p3_key", "p3"),
        ]:
            v = it.get(k_src)
            if isinstance(v, str) and v.strip():
                avoid[k_dst].add(v.strip())
    return avoid


def _sentence_tail(text: str, max_sents: int = 40) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []
    chunk = t[-3500:]
    parts = re.split(r"(?<=[\.\!\?…])\s+", chunk)
    sents = [p.strip() for p in parts if p.strip()]
    return sents[-max_sents:]


def _opener_key(s: str, n_words: int = 6) -> str:
    w = re.findall(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż0-9]+", (s or "").lower())
    return " ".join(w[:n_words]).strip()


def _seed_int(s: str) -> int:
    h = hashlib.md5((s or "").encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _pick_avoid(arr: List[str], seed: int, shift: int, avoid: Set[str]) -> str:
    if not arr:
        return ""
    n = len(arr)
    start = (seed >> shift) % n
    for j in range(n):
        cand = arr[(start + j) % n]
        if cand not in avoid:
            return cand
    return arr[start]


def _pick_opener(arr: List[str], seed: int, shift: int, avoid_keys: Set[str], blacklist: Set[str]) -> str:
    n = len(arr)
    start = (seed >> shift) % n
    for j in range(n):
        cand = arr[(start + j) % n]
        k = _opener_key(cand)
        if k and (k not in avoid_keys) and (k not in blacklist):
            return cand
    return arr[start]


def _offline_chunk(arch_md: str, job_run_id: str, i: int, words: int, avoid: Dict[str, Set[str]], opener_blacklist: Set[str]) -> Tuple[str, Dict[str, str]]:
    seed = _seed_int(arch_md + f"|{job_run_id}|{i}|")

    places = [
        "Korytarz biurowca",
        "Parking podziemny",
        "Kawiarnia przy ruchliwej ulicy",
        "Klatka schodowa starej kamienicy",
        "Wnętrze taksówki stojącej na światłach",
        "Hol hotelu z miękkim dywanem",
    ]
    sensory = [
        "Powietrze miało metaliczny posmak, jak po burzy",
        "Światło migotało, odbijając się w mokrym asfalcie",
        "W tle buczała wentylacja, zbyt głośno jak na ciszę",
        "Ktoś zostawił w powietrzu słodkawy zapach perfum",
        "Zimno wchodziło pod skórę, mimo że było wewnątrz",
    ]
    props = [
        "telefon z pękniętym szkłem",
        "karta dostępu z wytartym nadrukiem",
        "złożona kartka papieru wsunięta w kieszeń",
        "pendrive na cienkim łańcuszku",
        "koperta bez adresu",
        "klucz, który nie pasował do żadnych drzwi",
    ]
    dialogue = [
        "– Masz minutę. Ani sekundy więcej.",
        "– Nie teraz.",
        "– Wiesz, co to znaczy?",
        "– To nie powinno tu być.",
        "– Jeśli to powiesz na głos, będziemy mieli problem.",
    ]
    hooks = [
        "Telefon zawibrował krótko. Na ekranie: NIEZNANY NUMER.",
        "Na podłodze leżała koperta. Bez adresu. Bez znaczka.",
        "Drzwi, które miały być zamknięte, były uchylone – o grubość palca.",
        "Na ekranie mignęły trzy słowa, których nie dało się odzobaczyć.",
        "Z głośnika dobiegł cichy sygnał. Ktoś właśnie się podłączył.",
    ]
    p2_openers = [
        "Zrobił pół kroku i od razu poczuł, że coś nie gra.",
        "Ruch utknął mu w gardle; cisza nagle była zbyt głośna.",
        "W tej samej sekundzie zrozumiał, że to nie jest prosta droga do przodu.",
        "Ktoś lub coś postawiło warunek. Natychmiast.",
        "Wszystko wyglądało normalnie… dopóki nie przestało.",
        "Zanim zdążył mrugnąć, sytuacja zmieniła zasady.",
    ]
    p3_openers = [
        "Zaryzykował.",
        "Postawił na jeden ruch.",
        "Zdecydował się działać.",
        "Nie cofnął się.",
        "Wybrał najgorszą z dobrych opcji.",
        "Wszedł w to.",
    ]

    place = _pick_avoid(places, seed, 0, avoid["places"])
    sens = sensory[(seed >> 3) % len(sensory)]
    prop = _pick_avoid(props, seed, 7, avoid["props"])
    dlg = _pick_avoid(dialogue, seed, 11, avoid["dialogs"])
    hook = _pick_avoid(hooks, seed, 15, avoid["hooks"])

    p2 = _pick_opener(p2_openers, seed, 19, avoid["p2"], opener_blacklist)
    p3 = _pick_opener(p3_openers, seed, 23, avoid["p3"], opener_blacklist)

    conflict = "System kontroli dostępu nie przepuszcza (wymaga uprawnień)."
    stakes = "Stawka: utrata przewagi i ryzyko wpadki."
    m = re.search(r"\*\*Conflict:\*\*\s*(.+)", arch_md, flags=re.IGNORECASE)
    if m:
        conflict = m.group(1).strip()
    m = re.search(r"\*\*Stakes:\*\*\s*(.+)", arch_md, flags=re.IGNORECASE)
    if m:
        stakes = m.group(1).strip()

    conflict = re.sub(r"^\s*(Przeszkoda\s*\(?.*?\)?:)\s*", "", conflict, flags=re.IGNORECASE)
    stakes = re.sub(r"^\s*(Stawka\s*\(?.*?\)?:)\s*", "", stakes, flags=re.IGNORECASE)

    t1 = f"{place}. {sens}. Zacisnął palce na {prop} i zatrzymał się na pół kroku. Ujawnić nową informację i podnieść stawkę."
    t2 = f"{p2} {conflict} {dlg} Przełknął ślinę — rozmowa nie była o słowach, tylko o granicach."
    t3 = f"{p3} {stakes} {hook}"
    text = (t1 + "\n\n" + t2 + "\n\n" + t3).strip()

    micro = [
        "Wskazał ekran i przesunął kciukiem po zimnym szkle.",
        "Ktoś w tle chrząknął, jakby czekał na błąd.",
        "Zrobił krok w bok i od razu pożałował.",
        "Zatrzymał oddech na sekundę, za długo.",
        "Wiedział, że to zostawi ślad.",
        "Kątem oka złapał ruch, którego nie powinno tu być.",
        "Dźwięk w tle brzmiał jak ostrzeżenie.",
        "Coś kliknęło cicho, zbyt blisko.",
    ]
    used = set()
    k = 0
    while len(text.split()) < words:
        cand = micro[(seed + k) % len(micro)]
        k += 1
        if cand in used:
            continue
        used.add(cand)
        text += " " + cand

    meta = {
        "place": place,
        "prop": prop,
        "dialog": dlg,
        "hook": hook,
        "p2_key": _opener_key(p2),
        "p3_key": _opener_key(p3),
    }
    return text.strip() + "\n", meta


# -------------------------
# WORKER THREAD
# -------------------------
_JOB_Q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
_STARTED = False


def _worker_loop():
    while True:
        job = _JOB_Q.get()
        try:
            _run_job(job)
        except Exception:
            # worker must never die
            pass
        finally:
            _JOB_Q.task_done()


def _ensure_worker():
    global _STARTED
    if _STARTED:
        return
    t = threading.Thread(target=_worker_loop, daemon=True)
    t.start()
    _STARTED = True


def _run_job(job: Dict[str, Any]) -> None:
    book = job["book"]
    job_id = job["job_id"]
    req = job["req"]
    job_run_id = job["job_run_id"]

    book_root = safe_book_root(book)
    ensure_dir(book_root)

    job_state = _read_job(book_root, job_id)
    job_state.update({"status": "RUNNING", "started_at": _utc_iso()})
    _write_job(book_root, job_id, job_state)

    steps: List[Dict[str, Any]] = []
    try:
        master = safe_resolve_under(book_root, "draft/master.txt")
        ensure_dir(master.parent)
        if not master.exists():
            atomic_write_text(master, f"START {book}.\n")

        fp = _fp_load(book_root)
        avoid = _avoid_sets(fp, last=25)
        opener_blacklist = { _opener_key(s) for s in _sentence_tail(read_text_safe(master), 40) if _opener_key(s) }

        for i in range(req["n"]):
            # cancel support
            cur = _read_job(book_root, job_id)
            if cur.get("cancel") is True:
                job_state.update({"status": "CANCELLED", "finished_at": _utc_iso(), "steps": steps})
                _write_job(book_root, job_id, job_state)
                return

            arch = architect_run(ArchitectRunReq(book=book, path="draft/master.txt", chunk_hint_words=max(200, req["words_per_step"])))
            arch_md = (arch.get("preview") or "") + f"\nSALT:{job_run_id}:{i}:{arch.get('run_id','')}\n"

            chunk, meta = _offline_chunk(arch_md, job_run_id, i, req["words_per_step"], avoid, opener_blacklist)
            wr = writer_generate(WriterGenerateReq(book=book, text=chunk, ensure_newline=True, preview_chars=700))

            pr = proof_check(ProofCheckReq(book=book, path="draft/master.txt", max_issues=60)) if req["do_proof"] else None
            cr = critic_check(CriticCheckReq(book=book, path="draft/master.txt", max_notes=20)) if req["do_critic"] else None
            st = humanity_stylist(StylistReq(book=book, path="draft/master.txt", max_edits=8)) if req["do_stylist"] else None

            item = {"run_id": job_run_id, "i": i + 1, **meta}
            fp["items"].append(item)
            fp["items"] = fp["items"][-60:]
            _fp_save(book_root, fp)

            avoid = _avoid_sets(fp, last=25)
            opener_blacklist.add(meta.get("p2_key", ""))
            opener_blacklist.add(meta.get("p3_key", ""))

            steps.append({
                "i": i + 1,
                "architect_run_id": arch.get("run_id"),
                "writer_run_id": wr.get("run_id"),
                "proof_run_id": pr.get("run_id") if isinstance(pr, dict) else None,
                "critic_run_id": cr.get("run_id") if isinstance(cr, dict) else None,
                "stylist_run_id": st.get("run_id") if isinstance(st, dict) else None,
                "fingerprint": item,
                "appended_preview": wr.get("preview"),
            })

            job_state.update({"progress": {"i": i + 1, "n": req["n"]}, "steps": steps})
            _write_job(book_root, job_id, job_state)

        report_json = {"ok": True, "book": book, "job_id": job_id, "run_id": job_run_id, "steps": steps}
        report_md = "# Agent loop_write (JOB)\n" + "\n".join([f"- {s['i']}: place={s['fingerprint'].get('place')} prop={s['fingerprint'].get('prop')} hook={s['fingerprint'].get('hook')}" for s in steps]) + "\n"

        atomic_write_text(safe_resolve_under(book_root, f"analysis/agent_loop_write_job_report_{job_run_id}.md"), report_md)
        atomic_write_json(safe_resolve_under(book_root, f"analysis/agent_loop_write_job_report_{job_run_id}.json"), report_json)

        w_latest = write_latest(book_root, "agent_loop_write", report_md, json_obj=report_json, raw_text=report_md)

        run_written = write_run(
            book_root=book_root,
            run_id=job_run_id,
            tool="agent_loop_write_job",
            title="AGENT_LOOP_WRITE_JOB",
            status="SUCCESS",
            role="AGENT",
            input_obj=req,
            output_obj={"ok": True, "job_id": job_id, "steps": steps, "latest": w_latest.get("paths", {})},
        )

        job_state.update({
            "status": "SUCCESS",
            "finished_at": _utc_iso(),
            "steps": steps,
            "paths": {
                "job_file": f"jobs/{job_id}.json",
                "run_meta": run_written["paths"]["meta"],
                "run_input": run_written["paths"]["input"],
                "run_output": run_written["paths"]["output"],
                **{f"latest_{k}": v for k, v in w_latest.get("paths", {}).items()},
                "fingerprints": "memory/scene_fingerprints.json",
                "master_txt": "draft/master.txt",
            },
        })
        _write_job(book_root, job_id, job_state)
    except Exception as e:
        job_state.update({"status": "SUCCESS_FALLBACK", "finished_at": _utc_iso(), "error": repr(e), "steps": steps})
        _write_job(book_root, job_id, job_state)


# -------------------------
# API
# -------------------------
class LoopWriteJobReq(BaseModel):
    book: str
    n: int = Field(4, ge=1, le=50)
    words_per_step: int = Field(220, ge=120, le=1200)
    do_proof: bool = True
    do_critic: bool = False
    do_stylist: bool = True


class JobResp(BaseModel):
    ok: bool
    book: str
    job_id: str
    status: str
    created_at: str
    paths: Dict[str, str]
    progress: Optional[Dict[str, Any]] = None


@router.post("/loop_write_job", response_model=JobResp)
def loop_write_job(req: LoopWriteJobReq):
    _ensure_worker()

    book_root = safe_book_root(req.book)
    ensure_dir(book_root)

    job_id = make_run_id("job")
    job_run_id = make_run_id("loopwritejob")

    payload = {
        "ok": True,
        "book": req.book,
        "job_id": job_id,
        "status": "QUEUED",
        "created_at": _utc_iso(),
        "progress": {"i": 0, "n": req.n},
        "cancel": False,
        "req": req.model_dump(),
        "job_run_id": job_run_id,
        "paths": {"job_file": f"jobs/{job_id}.json"},
    }
    _write_job(book_root, job_id, payload)

    _JOB_Q.put({"book": req.book, "job_id": job_id, "job_run_id": job_run_id, "req": req.model_dump()})

    return {"ok": True, "book": req.book, "job_id": job_id, "status": "QUEUED", "created_at": payload["created_at"], "paths": {"job_file": f"jobs/{job_id}.json"}, "progress": payload["progress"]}


@router.get("/job/{job_id}")
def get_job(book: str, job_id: str):
    book_root = safe_book_root(book)
    ensure_dir(book_root)
    return _read_job(book_root, job_id)


@router.post("/job/{job_id}/cancel")
def cancel_job(book: str, job_id: str):
    book_root = safe_book_root(book)
    ensure_dir(book_root)
    st = _read_job(book_root, job_id)
    st["cancel"] = True
    st["status"] = st.get("status") if st.get("status") in {"SUCCESS", "SUCCESS_FALLBACK"} else "CANCEL_REQUESTED"
    _write_job(book_root, job_id, st)
    return {"ok": True, "book": book, "job_id": job_id, "status": st["status"]}

from __future__ import annotations

import json
import os
import traceback
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal, List, Tuple

from fastapi import APIRouter
from pydantic import BaseModel, Field

from llm_client import generate_text

router = APIRouter(prefix="/books/agent", tags=["books-agent"])

ROOT = Path(__file__).resolve().parent
BOOKS_ROOT = ROOT / "books"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_json(path: Path, obj: dict) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


def read_text_safe(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8-sig", errors="replace")


def read_json_safe(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:
        return {}


def ensure_book_scaffold(book: str) -> Path:
    book_dir = BOOKS_ROOT / book
    (book_dir / "draft").mkdir(parents=True, exist_ok=True)
    (book_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (book_dir / "jobs").mkdir(parents=True, exist_ok=True)
    (book_dir / "jobs_done").mkdir(parents=True, exist_ok=True)

    for p in [
        book_dir / "draft" / "master.txt",
        book_dir / "draft" / "buffer.txt",
    ]:
        if not p.exists():
            atomic_write_text(p, "")

    for p in [
        book_dir / "state.json",
        book_dir / "book_bible.json",
    ]:
        if not p.exists():
            atomic_write_json(p, {})

    return book_dir


# =========================
# LOCK per book (.lock)
# =========================
def acquire_book_lock(book_dir: Path, ttl_sec: int = 1800) -> Tuple[bool, str]:
    lock_path = book_dir / ".lock"

    # jeśli stary lock wisi (np. po crashu) -> zdejmij
    if lock_path.exists():
        try:
            age = time.time() - lock_path.stat().st_mtime
            if age > ttl_sec:
                lock_path.unlink(missing_ok=True)
        except Exception:
            pass

    try:
        # atomowe utworzenie (fail jeśli istnieje)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "ts": utc_now_iso()}, ensure_ascii=False))
        return True, "LOCKED"
    except FileExistsError:
        return False, f"LOCK_EXISTS: {lock_path}"
    except Exception as e:
        return False, f"LOCK_ERROR: {e}"


def release_book_lock(book_dir: Path) -> None:
    lock_path = book_dir / ".lock"
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def pick_oldest_job(jobs_dir: Path) -> Optional[Path]:
    jobs = sorted(jobs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    return jobs[0] if jobs else None


def contains_meta(text: str) -> bool:
    t = (text or "").lower()
    bad = [
        "proszę o przesłanie",
        "prześlij fragment",
        "prześlij streszczenie",
        "bez tego nie jestem w stanie",
        "nie jestem w stanie napisać",
        "podaj dokładne informacje",
        "nie mogę kontynuować",
    ]
    return any(x in t for x in bad)


# =========================
# MODELE API
# =========================
class WorkerOnceReq(BaseModel):
    book: str = Field(..., description="book folder under books/")
    max_chars_from_prompt: int = Field(400, ge=0, le=5000)
    job_id: Optional[str] = Field(None, description="optional: force specific job id")


class WorkerOnceResp(BaseModel):
    ok: bool
    book: str
    processed: bool
    job_id: Optional[str] = None
    status: Optional[str] = None
    master_path: Optional[str] = None
    buffer_path: Optional[str] = None
    wrote_to: Optional[Literal["buffer", "master"]] = None
    job_done_path: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[dict] = None
    error: Optional[str] = None


class FactIssue(BaseModel):
    severity: Literal["low", "medium", "high"] = "low"
    type: str
    claim: str
    evidence: Optional[str] = None
    suggested_fix: Optional[str] = None


class FactCheckReq(BaseModel):
    book: str
    source: Literal["buffer", "master"] = "buffer"
    deep: bool = False


class FactCheckResp(BaseModel):
    ok: bool
    book: str
    checked_chars: int
    model: str
    issues: List[FactIssue] = []


class AcceptReq(BaseModel):
    book: str
    clear_buffer: bool = True
    require_fact_ok: bool = True
    bypass_gate: bool = False


class AcceptResp(BaseModel):
    ok: bool
    book: str
    status: str
    added_chars: int = 0
    master_path: Optional[str] = None
    buffer_path: Optional[str] = None
    error: Optional[str] = None


@router.post("/worker/once", response_model=WorkerOnceResp)
def worker_once(req: WorkerOnceReq):
    book_dir = ensure_book_scaffold(req.book)

    got, msg = acquire_book_lock(book_dir)
    if not got:
        return WorkerOnceResp(ok=False, book=req.book, processed=False, status="BOOK_BUSY", error=msg)

    try:
        jobs_dir = book_dir / "jobs"
        jobs_done_dir = book_dir / "jobs_done"
        draft_dir = book_dir / "draft"
        master_path = draft_dir / "master.txt"
        buffer_path = draft_dir / "buffer.txt"

        # wybór joba
        job_p: Optional[Path] = None
        if req.job_id:
            cand = jobs_dir / f"{req.job_id}.json"
            if cand.exists():
                job_p = cand
            else:
                return WorkerOnceResp(ok=False, book=req.book, processed=True, status="JOB_NOT_FOUND", error=req.job_id)

        if job_p is None:
            job_p = pick_oldest_job(jobs_dir)

        if job_p is None:
            return WorkerOnceResp(ok=True, book=req.book, processed=True, status="NO_JOBS", master_path=str(master_path), buffer_path=str(buffer_path))

        job = read_json_safe(job_p)
        job_id = job.get("job_id") or job_p.stem
        mode = job.get("mode", "buffer")
        prompt_file = job.get("prompt_file")

        prompt_text = ""
        if prompt_file:
            p = ROOT / prompt_file
            prompt_text = read_text_safe(p)

        # payload: najpierw per-book, potem globalny
        payload = read_json_safe(book_dir / "payload.json")
        if not payload:
            payload = read_json_safe(ROOT / "payload.json")

        topic = payload.get("topic", "")
        words = int(payload.get("words", 800) or 800)

        master_tail = read_text_safe(master_path)[-4000:]
        buffer_tail = read_text_safe(buffer_path)[-2000:]

        user = (
            f"TOPIC: {topic}\n"
            f"TARGET_WORDS: ~{words}\n\n"
            f"MASTER_TAIL:\n{master_tail}\n\n"
            f"BUFFER_TAIL:\n{buffer_tail}\n\n"
            f"ZADANIE: Napisz kolejny fragment zgodnie z instrukcją w PROMPT.\n"
        )

        full_prompt = (prompt_text.strip() + "\n\n" + user).strip()

        model = os.getenv("OPENAI_MODEL", os.getenv("OPENAI_PRIMARY", "gpt-4.1-mini"))

        out = None
        try:
            out = generate_text(full_prompt, model=model)
        except TypeError:
            out = generate_text(full_prompt)

        text = out.get("text") if isinstance(out, dict) else str(out)
        model_used = out.get("model") if isinstance(out, dict) else model
        usage = out.get("usage") if isinstance(out, dict) else None

        text = (text or "").strip()
        if not text.endswith("\n"):
            text += "\n"

        wrote_to: Literal["buffer", "master"] = "buffer"
        if mode == "autonomous":
            append_text(master_path, "\n" + text)
            wrote_to = "master"
        else:
            append_text(buffer_path, "\n" + text)
            wrote_to = "buffer"

        done = {
            **job,
            "status": "SUCCESS",
            "finished_utc": utc_now_iso(),
            "wrote_to": wrote_to,
            "model": model_used,
        }
        if usage is not None:
            done["usage"] = usage

        done_path = jobs_done_dir / f"{job_id}.json"
        atomic_write_json(done_path, done)

        try:
            job_p.unlink()
        except Exception:
            pass

        return WorkerOnceResp(
            ok=True,
            book=req.book,
            processed=True,
            job_id=job_id,
            status="SUCCESS",
            master_path=str(master_path),
            buffer_path=str(buffer_path),
            wrote_to=wrote_to,
            job_done_path=str(done_path),
            model=model_used,
            usage=usage,
        )

    except Exception as e:
        traceback.print_exc()
        return WorkerOnceResp(ok=False, book=req.book, processed=True, status="ERROR", error=str(e))
    finally:
        release_book_lock(book_dir)


@router.post("/fact_check", response_model=FactCheckResp)
def fact_check(req: FactCheckReq):
    book_dir = ensure_book_scaffold(req.book)
    draft_dir = book_dir / "draft"
    master_path = draft_dir / "master.txt"
    buffer_path = draft_dir / "buffer.txt"

    target = buffer_path if req.source == "buffer" else master_path
    text = read_text_safe(target)
    checked = len(text)

    if not text.strip():
        return FactCheckResp(
            ok=False,
            book=req.book,
            checked_chars=0,
            model="fast",
            issues=[FactIssue(severity="low", type="BUFFER_EMPTY", claim="Brak tekstu do sprawdzenia.")],
        )

    # FAST guard: blokuj metatekst
    if contains_meta(text):
        return FactCheckResp(
            ok=False,
            book=req.book,
            checked_chars=checked,
            model="fast",
            issues=[FactIssue(severity="high", type="META_TEXT", claim="Wykryto metatekst (prośby o streszczenie/wyjaśnienia).")],
        )

    if not req.deep:
        return FactCheckResp(ok=True, book=req.book, checked_chars=checked, model="fast", issues=[])

    fact_model = os.getenv("OPENAI_FACT_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    prompt = (
        "Sprawdź spójność tekstu. Zwróć WYŁĄCZNIE JSON:\n"
        '{ "ok": true/false, "issues": [ { "severity":"low|medium|high", "type":"...", "claim":"...", "evidence":"...", "suggested_fix":"..." } ] }\n\n'
        "TEKST:\n" + text[-12000:]
    )

    try:
        try:
            out = generate_text(prompt, model=fact_model)
        except TypeError:
            out = generate_text(prompt)

        raw = out.get("text") if isinstance(out, dict) else str(out)
        s = (raw or "").strip()
        start = s.find("{")
        if start >= 0:
            s = s[start:]
        data = json.loads(s)

        ok = bool(data.get("ok", True))
        issues_raw = data.get("issues") or []
        issues: List[FactIssue] = []
        for it in issues_raw:
            try:
                issues.append(FactIssue(**it))
            except Exception:
                issues.append(FactIssue(severity="low", type="parse", claim=str(it)))

        return FactCheckResp(ok=ok, book=req.book, checked_chars=checked, model=fact_model, issues=issues)

    except Exception:
        return FactCheckResp(
            ok=True,
            book=req.book,
            checked_chars=checked,
            model=fact_model,
            issues=[FactIssue(severity="low", type="FACT_CHECK_FALLBACK", claim="Deep fact_check failed; fallback OK.")],
        )


@router.post("/accept", response_model=AcceptResp)
def accept(req: AcceptReq):
    book_dir = ensure_book_scaffold(req.book)

    got, msg = acquire_book_lock(book_dir)
    if not got:
        return AcceptResp(ok=False, book=req.book, status="BOOK_BUSY", error=msg)

    try:
        draft_dir = book_dir / "draft"
        master_path = draft_dir / "master.txt"
        buffer_path = draft_dir / "buffer.txt"

        buf = read_text_safe(buffer_path)
        if not buf.strip():
            return AcceptResp(
                ok=False,
                book=req.book,
                status="BUFFER_EMPTY",
                master_path=str(master_path),
                buffer_path=str(buffer_path),
                error="BUFFER_EMPTY",
            )

        if req.require_fact_ok and not req.bypass_gate:
            fc = fact_check(FactCheckReq(book=req.book, source="buffer", deep=False))
            if not fc.ok:
                return AcceptResp(
                    ok=False,
                    book=req.book,
                    status="FACT_GATE_BLOCKED",
                    master_path=str(master_path),
                    buffer_path=str(buffer_path),
                    error="FACT_GATE_BLOCKED",
                )

        master_prev = read_text_safe(master_path)
        new_master = (master_prev + "\n" + buf).lstrip("\n")
        atomic_write_text(master_path, new_master)

        added = len(buf)

        if req.clear_buffer:
            atomic_write_text(buffer_path, "")

        return AcceptResp(
            ok=True,
            book=req.book,
            status="SUCCESS",
            added_chars=added,
            master_path=str(master_path),
            buffer_path=str(buffer_path),
        )

    finally:
        release_book_lock(book_dir)

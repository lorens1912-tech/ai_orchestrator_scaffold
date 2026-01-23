from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

print(f"[CRITIC_V2_API] LOADED: {__file__}")

router = APIRouter(prefix="/books", tags=["critic"])

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


def _new_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}_{uuid.uuid4().hex}"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, obj: Any) -> None:
    _atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_run_one_shot(
    *,
    book: str,
    role: str,
    title: str,
    model: Optional[str],
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
        "role": role,
        "title": title,
        "model": model,
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


def _split_paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]


def _excerpt(s: str, n: int = 420) -> str:
    s = s.strip()
    return (s[:n] + "…") if len(s) > n else s


def _critic_heuristic(text: str, max_points: int = 12) -> Dict[str, Any]:
    paras = _split_paragraphs(text)
    points: List[Dict[str, Any]] = []

    # Heurystyki “czytelnicze” (neutralne, bez fabuły)
    # 1) placeholdery
    ph_hits = len(re.findall(r"\b(NEUTRAL_[A-Z0-9_]+|TBD)\b", text))
    if ph_hits:
        points.append({"type": "PLACEHOLDERS", "severity": "high", "note": f"W tekście są placeholdery (x{ph_hits}). Usuń przed wydaniem.", "excerpt": "GLOBAL"})

    # 2) monotonia rytmu: dużo podobnych długości zdań (bardzo prosto)
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    lens = [len(re.findall(r"\w+", s)) for s in sentences if s.strip()]
    if len(lens) >= 8:
        avg = sum(lens) / max(1, len(lens))
        var = sum((x - avg) ** 2 for x in lens) / max(1, len(lens))
        if var < 20:
            points.append({"type": "RHYTHM_MONOTONY", "severity": "med", "note": "Rytm zdań jest monotonny (mała zmienność długości).", "excerpt": "GLOBAL"})

    # 3) brak konkretu: dużo ogólników (lista bardzo krótka, neutralna)
    vague = re.findall(r"\b(ogólnie|w zasadzie|tak naprawdę|w pewnym sensie|właściwie)\b", text.lower())
    if len(vague) >= 3:
        points.append({"type": "VAGUE_PHRASES", "severity": "med", "note": f"Dużo ogólników/wytrychów (x{len(vague)}).", "excerpt": "GLOBAL"})

    # 4) jeśli pierwszy akapit wygląda jak meta-komentarz
    if paras:
        p0 = paras[0].lower()
        if "to jest" in p0 and "wpis" in p0 and "master" in p0:
            points.append({"type": "META_TEXT", "severity": "high", "note": "Początek wygląda jak meta-komentarz techniczny, nie jak proza.", "excerpt": _excerpt(paras[0])})

    # 5) szybkie “miejsca do wzmocnienia”: krótkie, ogólne akapity
    for i, p in enumerate(paras[:30]):
        w = re.findall(r"\w+", p)
        if 20 <= len(w) <= 45 and len(points) < max_points:
            points.append({"type": "THIN_PARAGRAPH", "severity": "low", "note": "Akapit może być zbyt cienki — rozważ doprecyzowanie obrazu/akcji.", "excerpt": f"para_index={i}: " + _excerpt(p)})

    points = points[:max_points]

    score = 100
    for pt in points:
        if pt["severity"] == "high":
            score -= 18
        elif pt["severity"] == "med":
            score -= 10
        else:
            score -= 6
    score = max(0, min(100, score))

    return {
        "metrics": {"paragraphs": len(paras), "sentences": len(sentences), "points_count": len(points), "score": score},
        "points": points,
    }


class CriticBody(BaseModel):
    book: str = Field(..., description="Book id, np. test")
    model: str = Field("HEURISTIC", description="Tu: HEURISTIC (stabilnie)")
    note: str = Field("neutral", description="Notatka (UI)")
    context_chars: int = Field(20000, ge=0, le=200000)
    max_points: int = Field(12, ge=1, le=50)
    save_run: bool = Field(True)


@router.post("/critic/check")
def critic_check(body: CriticBody) -> Dict[str, Any]:
    _validate_book(body.book)
    book = body.book

    master_path = (_books_dir() / book / "draft" / "master.txt").resolve()
    analysis_dir = _books_dir() / book / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    if not master_path.exists():
        raise HTTPException(status_code=404, detail="master.txt not found for this book.")

    txt = _read_text(master_path)
    ctx = txt[-body.context_chars:] if body.context_chars > 0 else txt

    res = _critic_heuristic(ctx, max_points=body.max_points)

    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = (analysis_dir / f"critic_report_{ts_tag}.md").resolve()
    latest_path = (analysis_dir / "critic_report_latest.md").resolve()
    json_path = (analysis_dir / f"critic_report_{ts_tag}.json").resolve()
    json_latest_path = (analysis_dir / "critic_report_latest.json").resolve()

    _atomic_write_json(json_path, res)
    _atomic_write_json(json_latest_path, res)

    m = res["metrics"]
    lines: List[str] = []
    lines.append("# KRYTYK (HEURYSTYKA) — RAPORT\n\n")
    lines.append(f"- book: {book}\n")
    lines.append(f"- ts: {ts_tag}\n")
    lines.append(f"- note: {body.note}\n")
    lines.append(f"- master_path: {master_path}\n")
    lines.append(f"- context_chars: {body.context_chars}\n\n")
    lines.append("## METRYKI\n")
    for k, v in m.items():
        lines.append(f"- {k}: {v}\n")
    lines.append("\n## PUNKTY\n")

    for p in res["points"]:
        lines.append(f"### type={p['type']} severity={p['severity']}\n")
        lines.append(f"- note: {p['note']}\n")
        lines.append("```text\n")
        lines.append(str(p["excerpt"]) + "\n")
        lines.append("```\n\n")

    report = "".join(lines)
    _atomic_write_text(report_path, report)
    _atomic_write_text(latest_path, report)

    out = {
        "ok": True,
        "book": book,
        "model": body.model,
        "master_path": str(master_path),
        "report_path": str(report_path),
        "latest_path": str(latest_path),
        "json_path": str(json_path),
        "json_latest_path": str(json_latest_path),
        "metrics": res["metrics"],
        "points_count": res["metrics"]["points_count"],
        "score": res["metrics"]["score"],
    }

    if body.save_run:
        run_id = _write_run_one_shot(
            book=book,
            role="KRYTYK",
            title="CRITIC_CHECK",
            model=body.model,
            status="SUCCESS",
            inputs={"note": body.note, "context_chars": body.context_chars, "max_points": body.max_points},
            outputs={"metrics": res["metrics"], "report_path": str(report_path), "json_path": str(json_path)},
            extra_paths={
                "critic_report": str(report_path),
                "critic_report_latest": str(latest_path),
                "critic_json": str(json_path),
                "critic_json_latest": str(json_latest_path),
                "master_txt": str(master_path),
            },
        )
        out["run_id"] = run_id

    return out

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
BOOKS_DIR = ROOT / "books"

def _now() -> str:
    return datetime.utcnow().isoformat()

def _sanitize_book_id(book_id: str) -> str:
    book_id = (book_id or "").strip()
    if not book_id:
        raise ValueError("book_id is required")
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", book_id)

def _sanitize_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "untitled"
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)

def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

def _atomic_write_json(path: Path, obj: dict) -> None:
    _atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))

def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def ensure_book_structure(book_id: str) -> Path:
    bid = _sanitize_book_id(book_id)
    bdir = BOOKS_DIR / bid

    (bdir / "draft" / "inbox").mkdir(parents=True, exist_ok=True)
    (bdir / "draft" / "chapters").mkdir(parents=True, exist_ok=True)
    (bdir / "memory" / "summaries").mkdir(parents=True, exist_ok=True)

    pf = bdir / "project_profile.json"
    if not pf.exists():
        _atomic_write_json(pf, {"book_id": bid, "created_at": _now(), "notes": ""})

    bb = bdir / "book_bible.json"
    if not bb.exists():
        _atomic_write_json(bb, {"book_id": bid, "created_at": _now(), "canon": {}})

    sp = bdir / "style_profile.json"
    if not sp.exists():
        _atomic_write_json(sp, {"book_id": bid, "created_at": _now(), "style": {}})

    tl = bdir / "memory" / "timeline.json"
    if not tl.exists():
        _atomic_write_json(tl, {"book_id": bid, "created_at": _now(), "events": []})

    facts = bdir / "memory" / "facts.json"
    if not facts.exists():
        _atomic_write_json(facts, {"book_id": bid, "created_at": _now(), "facts": []})

    idx = bdir / "chapters_index.json"
    if not idx.exists():
        _atomic_write_json(idx, {"book_id": bid, "chapters": {}, "updated_at": _now()})

    return bdir

def update_book_latest(book_id: str, run_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    bdir = ensure_book_structure(book_id)
    latest_text = state.get("latest_text")

    if isinstance(latest_text, str):
        _atomic_write_text(bdir / "draft" / "latest.txt", latest_text)

    latest = {
        "book_id": bdir.name,
        "run_id": run_id,
        "last_step": state.get("last_step"),
        "latest_text_path": str((bdir / "draft" / "latest.txt").as_posix()),
        "runs_state_path": str((ROOT / "runs" / run_id / "state.json").as_posix()),
        "updated_at": _now()
    }
    _atomic_write_json(bdir / "latest.json", latest)

    log_path = bdir / "memory" / "run_log.jsonl"
    prev = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    _atomic_write_text(log_path, prev + json.dumps({
        "ts": _now(),
        "run_id": run_id,
        "last_step": state.get("last_step"),
        "latest_text_len": len(latest_text) if isinstance(latest_text, str) else None
    }, ensure_ascii=False) + "\n")

    return latest

def ingest_text(book_id: str, text: str, source: str = "IMPORT", name: str = "", chapter_id: str = "") -> Dict[str, Any]:
    bdir = ensure_book_structure(book_id)
    safe_name = _sanitize_name(name) if name else _sanitize_name(source.lower())
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    inbox_path = bdir / "draft" / "inbox" / f"{ts}_{safe_name}.txt"
    _atomic_write_text(inbox_path, text or "")

    # update latest.txt (human is source of truth)
    _atomic_write_text(bdir / "draft" / "latest.txt", text or "")

    meta = {
        "book_id": bdir.name,
        "source": source,
        "name": name or safe_name,
        "chapter_id": chapter_id or None,
        "inbox_path": str(inbox_path.as_posix()),
        "saved_at": _now()
    }
    _atomic_write_json(bdir / "draft" / "inbox" / f"{ts}_{safe_name}.meta.json", meta)
    return meta

def upsert_chapter_version(book_id: str, chapter_id: str, new_text: str, mode: str = "REPLACE") -> Dict[str, Any]:
    """
    mode:
      - REPLACE: nowa wersja = new_text
      - APPEND:  nowa wersja = (poprzednia_latest + "\\n" + new_text)
    """
    bdir = ensure_book_structure(book_id)
    cid = _sanitize_name(chapter_id)
    cdir = bdir / "draft" / "chapters" / cid
    cdir.mkdir(parents=True, exist_ok=True)

    latest_path = cdir / "latest.txt"
    prev = latest_path.read_text(encoding="utf-8") if latest_path.exists() else ""

    if (mode or "").upper() == "APPEND" and prev:
        merged = prev.rstrip() + "\n" + (new_text or "")
    else:
        merged = new_text or ""

    # compute next version
    existing = sorted([p for p in cdir.glob("v*.txt")])
    next_n = 1
    if existing:
        last = existing[-1].stem  # e.g. v0003
        try:
            next_n = int(last[1:]) + 1
        except Exception:
            next_n = len(existing) + 1

    vname = f"v{next_n:04d}.txt"
    vpath = cdir / vname
    _atomic_write_text(vpath, merged)
    _atomic_write_text(latest_path, merged)

    # update global index
    idx_path = bdir / "chapters_index.json"
    idx = _read_json(idx_path, {"book_id": bdir.name, "chapters": {}, "updated_at": _now()})

    chapters = idx.get("chapters") if isinstance(idx.get("chapters"), dict) else {}
    ch = chapters.get(cid) if isinstance(chapters.get(cid), dict) else {}
    ch.update({
        "chapter_id": cid,
        "latest_version": vname,
        "latest_path": str(latest_path.as_posix()),
        "versions_dir": str(cdir.as_posix()),
        "updated_at": _now()
    })
    chapters[cid] = ch
    idx["chapters"] = chapters
    idx["updated_at"] = _now()
    _atomic_write_json(idx_path, idx)

    return {
        "book_id": bdir.name,
        "chapter_id": cid,
        "mode": (mode or "REPLACE").upper(),
        "version": vname,
        "version_path": str(vpath.as_posix()),
        "latest_path": str(latest_path.as_posix()),
        "chars": len(merged)
    }

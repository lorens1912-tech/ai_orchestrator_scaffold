import json
import re
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
BOOKS_DIR = ROOT / "books"

def _now() -> str:
    return datetime.utcnow().isoformat()

def _atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

def _entity_guess(text: str, limit: int = 60) -> List[str]:
    # minimalny, deterministyczny extractor: kapitalizowane tokeny (start)
    caps = re.findall(r"\b[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźżA-ZĄĆĘŁŃÓŚŹŻ\-]{2,}\b", text or "")
    uniq = sorted(list({c for c in caps}))
    return uniq[:limit]

def _ensure_files(book_dir: Path) -> None:
    mem = book_dir / "memory"
    mem.mkdir(parents=True, exist_ok=True)

    tl = mem / "timeline.json"
    if not tl.exists():
        _atomic_write_json(tl, {"book_id": book_dir.name, "created_at": _now(), "events": []})

    facts = mem / "facts.json"
    if not facts.exists():
        _atomic_write_json(facts, {"book_id": book_dir.name, "created_at": _now(), "facts": []})

    wk = mem / "who_knows.json"
    if not wk.exists():
        _atomic_write_json(wk, {"book_id": book_dir.name, "created_at": _now(), "entries": []})

def update_memory_from_chapter(book_id: str, chapter_id: str, version: str | None = None, text: str | None = None) -> Dict[str, Any]:
    bdir = BOOKS_DIR / str(book_id)
    if not bdir.exists():
        raise FileNotFoundError(f"Book not found: {bdir}")

    _ensure_files(bdir)

    cid = str(chapter_id)
    v = str(version) if version else "unknown"

    # read latest chapter text if not provided
    if text is None:
        latest = bdir / "draft" / "chapters" / cid / "latest.txt"
        if not latest.exists():
            raise FileNotFoundError(f"Chapter latest not found: {latest}")
        text = latest.read_text(encoding="utf-8")

    digest = _sha(text)
    ents = _entity_guess(text)

    # timeline: 1 event per chapter version (start)
    tl_path = bdir / "memory" / "timeline.json"
    tl = _read_json(tl_path, {"book_id": bdir.name, "created_at": _now(), "events": []})
    events = tl.get("events") if isinstance(tl.get("events"), list) else []
    event_id = f"{cid}:{v}:{digest[:12]}"

    if not any(isinstance(e, dict) and e.get("id") == event_id for e in events):
        events.append({
            "id": event_id,
            "chapter_id": cid,
            "version": v,
            "ts": _now(),
            "sha256": digest,
            "entities": ents,
            "note": "auto_snapshot_v1"
        })
        tl["events"] = events
        tl["updated_at"] = _now()
        _atomic_write_json(tl_path, tl)

    # facts: entity mentions as starter facts
    facts_path = bdir / "memory" / "facts.json"
    fx = _read_json(facts_path, {"book_id": bdir.name, "created_at": _now(), "facts": []})
    flist = fx.get("facts") if isinstance(fx.get("facts"), list) else []

    added = 0
    for ent in ents:
        fid = f"ENT:{ent}:{digest[:12]}"
        if any(isinstance(f, dict) and f.get("id") == fid for f in flist):
            continue
        flist.append({
            "id": fid,
            "type": "ENTITY_MENTION",
            "value": ent,
            "chapter_id": cid,
            "version": v,
            "sha256": digest,
            "ts": _now(),
            "confidence": "LOW",
            "note": "auto_extract_v1"
        })
        added += 1

    if added:
        fx["facts"] = flist
        fx["updated_at"] = _now()
        _atomic_write_json(facts_path, fx)

    # who_knows: placeholder entry (nie rozstrzygamy wiedzy bez LLM)
    wk_path = bdir / "memory" / "who_knows.json"
    wk = _read_json(wk_path, {"book_id": bdir.name, "created_at": _now(), "entries": []})
    wlist = wk.get("entries") if isinstance(wk.get("entries"), list) else []
    wid = f"WK:{cid}:{v}:{digest[:12]}"

    if not any(isinstance(x, dict) and x.get("id") == wid for x in wlist):
        wlist.append({
            "id": wid,
            "chapter_id": cid,
            "version": v,
            "sha256": digest,
            "ts": _now(),
            "characters": ents[:20],
            "knowledge": [],
            "note": "placeholder_v1"
        })
        wk["entries"] = wlist
        wk["updated_at"] = _now()
        _atomic_write_json(wk_path, wk)

    return {
        "book_id": bdir.name,
        "chapter_id": cid,
        "version": v,
        "sha256": digest,
        "entities_count": len(ents),
        "timeline_event_id": event_id,
        "facts_added": added,
        "who_knows_id": wid
    }

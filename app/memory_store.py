import json
import re
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
BOOKS_DIR = ROOT / "books"

def _now() -> str:
    return datetime.utcnow().isoformat()

def _sanitize(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "untitled"
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", s)

def _atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def _stats(text: str) -> dict:
    text = text or ""
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    lines = text.splitlines()
    words = re.findall(r"\b\w+\b", text, flags=re.UNICODE)
    head = text[:600]
    tail = text[-600:] if len(text) > 600 else text

    # bardzo prosty "entity guess": kapitalizowane tokeny (na start)
    caps = re.findall(r"\b[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźżA-ZĄĆĘŁŃÓŚŹŻ\-]{2,}\b", text)
    uniq_caps = sorted(list({c for c in caps}))[:50]

    return {
        "chars": len(text),
        "lines": len(lines),
        "words": len(words),
        "sha256": sha,
        "excerpt_head": head,
        "excerpt_tail": tail,
        "entity_guess": uniq_caps
    }

def create_chapter_snapshot(book_id: str, chapter_id: str, version: Optional[str] = None, text: Optional[str] = None) -> Dict[str, Any]:
    bid = _sanitize(book_id)
    cid = _sanitize(chapter_id)

    bdir = BOOKS_DIR / bid
    chapter_dir = bdir / "draft" / "chapters" / cid
    latest_txt = chapter_dir / "latest.txt"

    if text is None:
        if not latest_txt.exists():
            raise FileNotFoundError(f"Chapter latest not found: {latest_txt}")
        text = latest_txt.read_text(encoding="utf-8")

    if not version:
        idx = _read_json(bdir / "chapters_index.json")
        if isinstance(idx, dict):
            ch = (idx.get("chapters") or {}).get(cid) or {}
            version = ch.get("latest_version")

    version = version or "unknown"
    vstem = version.replace(".txt", "")
    snap_dir = bdir / "memory" / "snapshots" / cid
    snap_path = snap_dir / f"{vstem}.summary.json"
    latest_summary_path = bdir / "memory" / "summaries" / f"{cid}_latest.summary.json"

    snap = {
        "book_id": bid,
        "chapter_id": cid,
        "version": version,
        "created_at": _now(),
        "stats": _stats(text)
    }

    _atomic_write_json(snap_path, snap)
    _atomic_write_json(latest_summary_path, snap)

    return {
        "book_id": bid,
        "chapter_id": cid,
        "version": version,
        "snapshot_path": str(snap_path.as_posix()),
        "latest_summary_path": str(latest_summary_path.as_posix()),
        "chars": snap["stats"]["chars"],
        "sha256": snap["stats"]["sha256"]
    }

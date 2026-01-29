from __future__ import annotations

from pathlib import Path
from typing import Optional
import json
import os
import tempfile
import re

ROOT = Path(__file__).resolve().parents[1]
BOOKS_DIR = ROOT / "books"
RUNS_DIR = ROOT / "runs"

_RUN_ID_RE = re.compile(r"^run_\d{8}_\d{6}_[0-9a-f]{6,}$", re.IGNORECASE)

def _latest_path(book_id: str) -> Path:
    safe = (book_id or "default").strip()
    return BOOKS_DIR / safe / "_latest_run_id.txt"

def _read_book_id_from_run(run_dir: Path) -> Optional[str]:
    """
    Próbuje wyciągnąć book_id z pierwszego kroku runa:
    runs/<run_id>/steps/001_*.json -> input.book_id
    """
    steps = run_dir / "steps"
    if not steps.exists():
        return None
    first = None
    for p in sorted(steps.glob("001_*.json")):
        first = p
        break
    if not first or not first.exists():
        return None
    try:
        data = json.loads(first.read_text(encoding="utf-8"))
    except Exception:
        return None
    inp = data.get("input") if isinstance(data, dict) else None
    if isinstance(inp, dict):
        bid = inp.get("book_id")
        if isinstance(bid, str) and bid.strip():
            return bid.strip()
    return None

def _scan_runs_latest(book_id: str) -> Optional[str]:
    bid = (book_id or "default").strip()
    if not RUNS_DIR.exists():
        return None

    # run_id zawiera timestamp -> sort po nazwie malejąco działa jako "najnowszy pierwszy"
    run_dirs = [p for p in RUNS_DIR.iterdir() if p.is_dir() and p.name.startswith("run_")]
    run_dirs.sort(key=lambda x: x.name, reverse=True)

    for d in run_dirs:
        rb = _read_book_id_from_run(d)
        if rb == bid:
            return d.name
    return None

def get_latest_run_id(book_id: str) -> Optional[str]:
    # 1) preferuj latest file (jeśli istnieje i wskazuje na istniejący run)
    p = _latest_path(book_id)
    if p.exists():
        try:
            rid = p.read_text(encoding="utf-8").strip()
            if rid:
                # sanity: minimalna walidacja formatu + istnienie folderu run
                if _RUN_ID_RE.match(rid) and (RUNS_DIR / rid).exists():
                    return rid
                # jeśli format/istnienie nie pasuje, lecimy fallbackiem
        except Exception:
            pass

    # 2) fallback: skan runs/
    return _scan_runs_latest(book_id)

def set_latest_run_id(book_id: str, run_id: str) -> None:
    """
    Atomic write (Windows/Linux):
    - zapis do temp pliku w katalogu booka
    - flush + os.fsync
    - os.replace -> atomic rename
    """
    rid = (run_id or "").strip()
    if not rid:
        return

    book_dir = (BOOKS_DIR / (book_id or "default").strip())
    book_dir.mkdir(parents=True, exist_ok=True)
    target = book_dir / "_latest_run_id.txt"

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(book_dir), delete=False, prefix="_latest_run_id_", suffix=".tmp"
        ) as f:
            tmp_path = Path(f.name)
            f.write(rid + "\n")
            f.flush()
            os.fsync(f.fileno())

        os.replace(str(tmp_path), str(target))
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

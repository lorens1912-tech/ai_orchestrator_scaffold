from __future__ import annotations
from pathlib import Path
from typing import Optional
import json

ROOT = Path(__file__).resolve().parents[1]
BOOKS_DIR = ROOT / "books"
RUNS_DIR = ROOT / "runs"

def _latest_path(book_id: str) -> Path:
    safe = (book_id or "default").strip()
    return BOOKS_DIR / safe / "_latest_run_id.txt"

def _read_book_id_from_run(run_dir: Path) -> Optional[str]:
    """
    Próbuje wyciągnąć book_id z pierwszego kroku runa:
    runs/<run_id>/steps/001_*.json  ->  input.book_id lub input["book_id"]
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
    # run_id ma format run_YYYYMMDD_HHMMSS_xxxxxx => sort malejąco po nazwie działa
    for d in sorted((p for p in RUNS_DIR.iterdir() if p.is_dir() and p.name.startswith("run_")),
                    key=lambda x: x.name, reverse=True):
        rb = _read_book_id_from_run(d)
        if rb == bid:
            return d.name
    return None

def get_latest_run_id(book_id: str) -> Optional[str]:
    # 1) preferuj plik latest (jeśli istnieje i wygląda sensownie)
    p = _latest_path(book_id)
    if p.exists():
        try:
            rid = p.read_text(encoding="utf-8").strip()
            if rid:
                # sanity: jeśli katalog runa istnieje, zwracamy
                if (RUNS_DIR / rid).exists():
                    return rid
        except Exception:
            pass

    # 2) fallback: skan runs/
    return _scan_runs_latest(book_id)

def set_latest_run_id(book_id: str, run_id: str) -> None:
    p = _latest_path(book_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text((run_id or "").strip() + "\n", encoding="utf-8")
    tmp.replace(p)

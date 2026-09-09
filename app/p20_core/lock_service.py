from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.p20_core.storage_paths import get_books_root, get_runs_root

APP_VERSION = "P20.0-novel-core"
REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = REPO_ROOT / "runs"
BOOKS_ROOT = REPO_ROOT / "books"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_run_dir(run_id: str) -> Path:
    run_dir = get_runs_root() / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def ensure_book_audit_dir(book_id: str) -> Path:
    p = get_books_root() / str(book_id) / "audit"
    p.mkdir(parents=True, exist_ok=True)
    return p


def run_lock_path(run_id: str) -> Path:
    return ensure_run_dir(run_id) / "run.lock.json"


def book_lock_path(book_id: str) -> Path:
    return ensure_book_audit_dir(book_id) / "book.lock.json"


def _read_lock(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return fallback


def get_run_lock(run_id: str) -> Optional[Dict[str, Any]]:
    p = run_lock_path(run_id)
    if not p.exists():
        return None
    return _read_lock(
        p,
        {
            "run_id": run_id,
            "status": "locked",
            "engine": APP_VERSION,
        },
    )


def get_book_lock(book_id: str) -> Optional[Dict[str, Any]]:
    p = book_lock_path(book_id)
    if not p.exists():
        return None
    return _read_lock(
        p,
        {
            "book_id": book_id,
            "status": "locked",
            "engine": APP_VERSION,
        },
    )


def acquire_run_lock(run_id: str, book_id: str) -> Dict[str, Any]:
    p = run_lock_path(run_id)
    payload = {
        "run_id": run_id,
        "book_id": book_id,
        "status": "locked",
        "locked_at": utc_now_iso(),
        "engine": APP_VERSION,
    }

    try:
        with p.open("x", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except FileExistsError:
        existing = get_run_lock(run_id) or payload
        raise RuntimeError(json.dumps(existing, ensure_ascii=False))

    return payload


def acquire_book_lock(book_id: str, run_id: str) -> Dict[str, Any]:
    p = book_lock_path(book_id)
    payload = {
        "book_id": book_id,
        "run_id": run_id,
        "status": "locked",
        "locked_at": utc_now_iso(),
        "engine": APP_VERSION,
    }

    try:
        with p.open("x", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except FileExistsError:
        existing = get_book_lock(book_id) or payload
        raise RuntimeError(json.dumps(existing, ensure_ascii=False))

    return payload


def release_run_lock(run_id: str) -> None:
    p = run_lock_path(run_id)
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass


def release_book_lock(book_id: str) -> None:
    p = book_lock_path(book_id)
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass

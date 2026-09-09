from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_books_root() -> Path:
    from app.p20_core.storage_paths import get_books_root as _get_books_root

    return _get_books_root()


def _normalize_run_dir(run_dir: Path) -> Path:
    p = Path(run_dir)
    if p.suffix.lower() == ".json" and p.parent.name.lower() == "steps":
        return p.parent.parent
    if p.name.lower() == "steps":
        return p.parent
    return p


def canon_default() -> Dict[str, Any]:
    return {
        "timeline": [],
        "world_facts": [],
        "character_facts": {},
        "decisions": [],
        "ledger": [],
    }


def run_canon_path(run_dir: Path) -> Path:
    rd = _normalize_run_dir(run_dir)
    return rd / "canon.json"


def book_canon_path(book_id: str) -> Path:
    bid = (book_id or "default").strip() or "default"
    d = get_books_root() / bid
    d.mkdir(parents=True, exist_ok=True)
    return d / "canon.json"


def _coerce_legacy_book_id_call(
    run_dir: Optional[Path],
    book_id: Optional[str],
) -> tuple[Optional[Path], Optional[str]]:
    if book_id is not None or run_dir is None:
        return run_dir, book_id

    raw = str(run_dir)
    candidate = Path(raw)
    if not candidate.is_absolute() and len(candidate.parts) == 1 and raw.strip():
        return None, raw

    return run_dir, book_id


def load_canon(run_dir: Optional[Path] = None, book_id: Optional[str] = None) -> Dict[str, Any]:
    run_dir, book_id = _coerce_legacy_book_id_call(run_dir, book_id)

    if run_dir is not None:
        p = run_canon_path(Path(run_dir))
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass

    if book_id is not None:
        p2 = book_canon_path(book_id)
        if p2.exists():
            try:
                return json.loads(p2.read_text(encoding="utf-8"))
            except Exception:
                pass

    return canon_default()


def save_canon(run_dir: Optional[Path], canon: Dict[str, Any], book_id: Optional[str] = None) -> None:
    canon = canon or canon_default()

    if run_dir is not None:
        p = run_canon_path(Path(run_dir))
        p.write_text(json.dumps(canon, ensure_ascii=False, indent=2), encoding="utf-8")

    if book_id is not None:
        p2 = book_canon_path(book_id)
        p2.write_text(json.dumps(canon, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge(dst: Any, patch: Any) -> Any:
    # dict: merge recursively; list: REPLACE (żeby testy nie kumulowały danych)
    if isinstance(dst, dict) and isinstance(patch, dict):
        for k, v in patch.items():
            if k in dst:
                dst[k] = _merge(dst[k], v)
            else:
                dst[k] = v
        return dst
    if isinstance(patch, list):
        return patch
    return patch


def patch_canon(run_dir: Optional[Path] = None, patch: Optional[Dict[str, Any]] = None, book_id: Optional[str] = None) -> Dict[str, Any]:
    patch = patch or {}
    canon = load_canon(run_dir=run_dir, book_id=book_id)
    canon = _merge(canon, patch)
    save_canon(run_dir=run_dir, canon=canon, book_id=book_id)
    return canon

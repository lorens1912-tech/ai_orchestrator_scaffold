
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _root_dir() -> Path:
    return Path(__file__).resolve().parent


def _books_dir() -> Path:
    return _root_dir() / "books"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, obj: Any) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    _atomic_write_text(path, text)


def ensure_runs_dir(book: str) -> Path:
    d = _books_dir() / book / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class RunPaths:
    meta: str
    input: str
    output: str


def create_run(
    *,
    book: str,
    role: str,
    model: Optional[str],
    status: str,
    input_obj: Optional[Dict[str, Any]],
    output_obj: Optional[Dict[str, Any]],
    artifact_paths: Optional[Dict[str, str]] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Creates:
      books/<book>/runs/<run_id>/{meta.json,input.json,output.json}
    """
    runs_dir = ensure_runs_dir(book)
    run_id = new_run_id()
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    meta_path = run_dir / "meta.json"
    in_path = run_dir / "input.json"
    out_path = run_dir / "output.json"

    paths: Dict[str, str] = {
        "meta": str(meta_path),
        "input": str(in_path),
        "output": str(out_path),
    }
    if artifact_paths:
        # e.g. {"master_txt": "...", "architect_report_latest": "..."}
        paths.update({k: str(v) for k, v in artifact_paths.items()})

    meta: Dict[str, Any] = {
        "run_id": run_id,
        "book": book,
        "role": role,
        "model": model,
        "status": status,
        "created_at": _utc_now_iso(),
        "paths": paths,
    }
    if extra_meta:
        meta.update(extra_meta)

    _atomic_write_json(meta_path, meta)
    _atomic_write_json(in_path, input_obj if input_obj is not None else {})
    _atomic_write_json(out_path, output_obj if output_obj is not None else {})

    return {"run_id": run_id, "dir": str(run_dir), "meta": meta}


def list_runs(book: str) -> List[Dict[str, Any]]:
    runs_dir = _books_dir() / book / "runs"
    if not runs_dir.exists():
        return []

    items: List[Dict[str, Any]] = []
    for d in runs_dir.iterdir():
        if not d.is_dir():
            continue
        meta_path = d / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        created_at = meta.get("created_at")
        items.append(
            {
                "run_id": d.name,
                "role": meta.get("role"),
                "status": meta.get("status"),
                "model": meta.get("model"),
                "created_at": created_at,
                "paths": meta.get("paths") if isinstance(meta.get("paths"), dict) else {},
            }
        )

    def sort_key(x: Dict[str, Any]) -> float:
        s = x.get("created_at")
        try:
            # ISO sort-ish; fallback to mtime
            dt = datetime.fromisoformat(s.replace("Z", "+00:00")) if isinstance(s, str) else None
            if dt:
                return dt.timestamp()
        except Exception:
            pass
        try:
            return (runs_dir / x["run_id"]).stat().st_mtime
        except Exception:
            return 0.0

    items.sort(key=sort_key, reverse=True)
    return items


def read_run(book: str, run_id: str) -> Dict[str, Any]:
    run_dir = _books_dir() / book / "runs" / run_id
    meta_path = run_dir / "meta.json"
    in_path = run_dir / "input.json"
    out_path = run_dir / "output.json"

    if not meta_path.exists():
        raise FileNotFoundError("meta.json not found")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    input_obj = json.loads(in_path.read_text(encoding="utf-8")) if in_path.exists() else None
    output_obj = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else None

    return {"meta": meta, "input": input_obj, "output": output_obj}

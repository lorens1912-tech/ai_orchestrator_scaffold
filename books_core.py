from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


BOOK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def safe_book_root(book: str, base_dir: Optional[Path] = None) -> Path:
    if not isinstance(book, str) or not BOOK_ID_RE.match(book):
        raise ValueError("Invalid book id. Allowed: letters/digits/_- (max 64), no dots, no slashes.")
    base = base_dir or Path(__file__).resolve().parent
    root = (base / "books" / book).resolve()
    books_dir = (base / "books").resolve()
    if not str(root).startswith(str(books_dir)):
        raise ValueError("Unsafe book root resolution.")
    return root


def safe_resolve_under(root: Path, rel_path: str) -> Path:
    if not isinstance(rel_path, str) or rel_path.strip() == "":
        raise ValueError("path is required")
    p = Path(rel_path)
    if p.is_absolute():
        raise ValueError("absolute paths are not allowed")
    resolved = (root / p).resolve(strict=False)
    root_res = root.resolve(strict=False)
    if not str(resolved).startswith(str(root_res)):
        raise ValueError("path escapes book root")
    return resolved


def make_run_id(prefix: str = "run") -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"{prefix}_{ts}_{short}"


def _win_retry_sleep(i: int) -> None:
    time.sleep(min(0.03 * (1.6 ** i), 0.25))


def atomic_write_bytes(path: Path, data: bytes, attempts: int = 8) -> Dict[str, Any]:
    ensure_dir(path.parent)
    tmp = path.parent / f".tmp_{path.name}.{uuid.uuid4().hex}"

    try:
        with open(tmp, "wb") as f:
            f.write(data)

        last_err = None
        for i in range(attempts):
            try:
                os.replace(tmp, path)
                return {"ok": True, "mode": "atomic_replace", "attempts": i + 1}
            except OSError as e:
                last_err = e
                _win_retry_sleep(i)

        try:
            with open(path, "wb") as f:
                f.write(data)
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            return {"ok": True, "mode": "fallback_direct_write", "attempts": attempts, "error": repr(last_err)}
        except Exception as e2:
            return {"ok": False, "mode": "fallback_failed", "error": repr(e2), "atomic_error": repr(last_err)}
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def atomic_write_text(path: Path, text: str, attempts: int = 8, encoding: str = "utf-8") -> Dict[str, Any]:
    return atomic_write_bytes(path, text.encode(encoding), attempts=attempts)


def atomic_write_json(path: Path, obj: Any, attempts: int = 8) -> Dict[str, Any]:
    txt = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    return atomic_write_text(path, txt, attempts=attempts)


def read_text_safe(path: Path, max_bytes: int = 2_000_000) -> str:
    data = path.read_bytes()
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="replace")


@dataclass
class RunPaths:
    meta: str
    input: str
    output: str


def write_run(
    book_root: Path,
    run_id: str,
    tool: str,
    title: str,
    status: str,
    role: str,
    input_obj: Any,
    output_obj: Any,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    runs_dir = book_root / "runs" / run_id
    ensure_dir(runs_dir)

    meta_path = runs_dir / "meta.json"
    input_path = runs_dir / "input.json"
    output_path = runs_dir / "output.json"

    paths = RunPaths(
        meta=str(meta_path.relative_to(book_root)),
        input=str(input_path.relative_to(book_root)),
        output=str(output_path.relative_to(book_root)),
    )

    meta: Dict[str, Any] = {
        "run_id": run_id,
        "tool": tool,
        "title": title,
        "status": status,
        "role": role,
        "created_at": utc_now_iso(),
        "paths": asdict(paths),
    }
    if extra_meta:
        meta.update(extra_meta)

    w1 = atomic_write_json(meta_path, meta)
    w2 = atomic_write_json(input_path, input_obj)
    w3 = atomic_write_json(output_path, output_obj)

    return {
        "ok": bool(w1.get("ok")) and bool(w2.get("ok")) and bool(w3.get("ok")),
        "meta": meta,
        "paths": asdict(paths),
        "writes": {"meta": w1, "input": w2, "output": w3},
    }


def write_latest(
    book_root: Path,
    kind: str,
    md_text: str,
    json_obj: Optional[Any] = None,
    raw_text: Optional[str] = None,
) -> Dict[str, Any]:
    analysis_dir = book_root / "analysis"
    ensure_dir(analysis_dir)

    md_path = analysis_dir / f"{kind}_latest.md"
    json_path = analysis_dir / f"{kind}_latest.json"
    raw_path = analysis_dir / f"{kind}_latest.raw"

    stub = {"ok": True, "stub": json_obj is None, "kind": kind, "created_at": utc_now_iso()}

    w_md = atomic_write_text(md_path, md_text)
    w_json = atomic_write_json(json_path, json_obj if json_obj is not None else stub)
    w_raw = atomic_write_text(raw_path, raw_text if raw_text is not None else md_text)

    return {
        "ok": bool(w_md.get("ok")) and bool(w_json.get("ok")) and bool(w_raw.get("ok")),
        "paths": {
            "md": str(md_path.relative_to(book_root)),
            "json": str(json_path.relative_to(book_root)),
            "raw": str(raw_path.relative_to(book_root)),
        },
        "writes": {"md": w_md, "json": w_json, "raw": w_raw},
    }

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT_DIR / "runs"


def new_run_id(prefix: str = "run") -> str:
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    rnd = secrets.token_hex(4)
    return f"{prefix}_{ts}_{rnd}"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: Dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def read_json_if_exists(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def relpath(p: Path) -> str:
    # stable rel path from project root (not cwd)
    return str(p.relative_to(ROOT_DIR))

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional

from .run_store import atomic_write_json, read_json_if_exists, run_dir

STATE_FILE = "state.json"


def _now() -> float:
    return float(time.time())


def state_path(run_id: str) -> Path:
    return run_dir(run_id) / STATE_FILE


def init_state(run_id: str, total_steps: int) -> Dict[str, Any]:
    st = {
        "run_id": run_id,
        "status": "QUEUED",
        "total_steps": int(total_steps),
        "completed_steps": 0,
        "last_mode": None,
        "created_ts": _now(),
        "started_ts": None,
        "finished_ts": None,
        "updated_ts": _now(),
        "error": None
    }
    atomic_write_json(state_path(run_id), st)
    return st


def load_state(run_id: str) -> Optional[Dict[str, Any]]:
    return read_json_if_exists(state_path(run_id))


def save_state(run_id: str, st: Dict[str, Any]) -> None:
    st["updated_ts"] = _now()
    atomic_write_json(state_path(run_id), st)


def set_status(run_id: str, st: Dict[str, Any], status: str, error: Optional[str] = None) -> None:
    st["status"] = status
    if status == "RUNNING" and st.get("started_ts") is None:
        st["started_ts"] = _now()
    if status in ("DONE", "ERROR"):
        st["finished_ts"] = _now()
    st["error"] = error
    save_state(run_id, st)

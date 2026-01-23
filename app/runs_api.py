from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pathlib import Path
from typing import Any, Dict, List

from .run_store import RUNS_DIR, read_json_if_exists, relpath

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("")
def list_runs(limit: int = 50):
    if not RUNS_DIR.exists():
        return {"runs": []}

    items = []
    for d in sorted(RUNS_DIR.iterdir(), key=lambda p: p.name, reverse=True):
        if not d.is_dir():
            continue
        st = read_json_if_exists(d / "state.json")
        items.append({
            "run_id": d.name,
            "status": (st or {}).get("status"),
            "completed_steps": (st or {}).get("completed_steps"),
            "total_steps": (st or {}).get("total_steps"),
            "updated_ts": (st or {}).get("updated_ts")
        })
        if len(items) >= limit:
            break

    return {"runs": items}


@router.get("/{run_id}")
def get_run(run_id: str):
    d = RUNS_DIR / run_id
    if not d.exists():
        raise HTTPException(status_code=404, detail="run not found")

    manifest = read_json_if_exists(d / "run.json")
    state = read_json_if_exists(d / "state.json")

    steps_dir = d / "steps"
    steps = []
    if steps_dir.exists():
        for p in sorted(steps_dir.glob("*.json")):
            steps.append(relpath(p))

    return {
        "run_id": run_id,
        "manifest": manifest,
        "state": state,
        "steps": steps
    }

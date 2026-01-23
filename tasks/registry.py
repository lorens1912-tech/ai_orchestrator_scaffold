
from __future__ import annotations

import importlib
from typing import Dict, Any, Callable

TASKS = {
    "book_pipeline": "tasks.book_pipeline",
    "marketing_pipeline": "tasks.marketing_pipeline",
    "diet_pipeline": "tasks.diet_pipeline",
    "business_pipeline": "tasks.business_pipeline",
    "android_pipeline": "tasks.android_pipeline",
    "ai_pipeline": "tasks.ai_pipeline",
}

def run_pipeline(task_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if task_name not in TASKS:
        raise ValueError(f"Unknown task: {task_name}. Allowed: {sorted(TASKS.keys())}")

    module = importlib.import_module(TASKS[task_name])

    run_fn: Callable[[Dict[str, Any]], Dict[str, Any]] | None = getattr(module, "run", None)
    if not callable(run_fn):
        raise RuntimeError(f"Task module {TASKS[task_name]} has no callable run(payload)")

    out = run_fn(payload)
    if not isinstance(out, dict):
        raise RuntimeError(f"Task {task_name} must return dict, got: {type(out)}")
    return out

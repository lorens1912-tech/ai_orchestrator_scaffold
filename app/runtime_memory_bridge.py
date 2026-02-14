from __future__ import annotations

from typing import Any, Dict, List

from app.runtime_memory_adapter import RuntimeMemoryAdapter, normalize_memory


def _normalize_run_state(run_state: Dict[str, Any] | None) -> Dict[str, Any]:
    src = run_state if isinstance(run_state, dict) else {}
    return {
        "project_id": str(src.get("project_id", "")),
        "run_id": str(src.get("run_id", "")),
        "mode": str(src.get("mode", "")),
        "step_index": int(src.get("step_index", 0)),
        "profile": str(src.get("profile", "")),
    }


def build_runtime_context(
    memory_payload: Dict[str, Any] | None,
    run_state: Dict[str, Any] | None,
    tail_size: int = 20,
) -> Dict[str, Any]:
    mem = normalize_memory(memory_payload)
    rs = _normalize_run_state(run_state)
    size = max(1, int(tail_size))

    entries: List[Dict[str, Any]] = mem.get("entries", [])
    tail = entries[-size:] if isinstance(entries, list) else []

    project_id = rs["project_id"] if rs["project_id"] else str(mem.get("project_id", ""))
    run_id = rs["run_id"] if rs["run_id"] else str(mem.get("run_id", ""))

    return {
        "project_id": project_id,
        "run_id": run_id,
        "memory_version": str(mem.get("version", "1.0")),
        "memory_updated_at": str(mem.get("updated_at", "")),
        "memory_entries_count": len(entries),
        "memory_tail": tail,
        "state": {
            "mode": rs["mode"],
            "step_index": rs["step_index"],
            "profile": rs["profile"],
        },
    }


def load_and_bind_runtime_memory(
    adapter: RuntimeMemoryAdapter,
    run_state: Dict[str, Any] | None,
    tail_size: int = 20,
) -> Dict[str, Any]:
    memory_payload = adapter.read()
    runtime_context = build_runtime_context(memory_payload, run_state, tail_size=tail_size)
    return {"runtime_memory": runtime_context}

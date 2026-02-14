
from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

TOOL_BY_MODE: Dict[str, str] = {
    "PLAN": "plan",
    "OUTLINE": "outline",
    "WRITE": "write",
    "CRITIC": "critic",
    "EDIT": "edit",
    "REWRITE": "rewrite",
    "QUALITY": "quality",
    "UNIQUENESS": "uniqueness",
    "CONTINUITY": "continuity",
    "FACTCHECK": "factcheck",
    "STYLE": "style",
    "TRANSLATE": "translate",
    "EXPAND": "expand",
    "CANON_CHECK": "canon_check",
    "CANON_EXTRACT": "canon_extract",
}
MODE_TO_TOOL = TOOL_BY_MODE


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _deep_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    return {}


def _first_non_empty_str(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _team_id_from_payload(payload: Dict[str, Any]) -> Optional[str]:
    team = _deep_dict(payload.get("team"))
    runtime = _deep_dict(payload.get("runtime"))
    team_runtime = _deep_dict(payload.get("team_runtime"))
    return _first_non_empty_str(
        payload.get("_team_id"),
        payload.get("team_id"),
        team.get("id"),
        runtime.get("team_id"),
        team_runtime.get("team_id"),
    )


def _team_runtime_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    value = payload.get("_team_runtime")
    if isinstance(value, dict):
        return copy.deepcopy(value)

    for key in ("team_runtime", "runtime"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            return copy.deepcopy(candidate)

    return {}


def _runtime_memory_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("_runtime_memory", "runtime_memory", "memory"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            return copy.deepcopy(candidate)
    return {}


def _normalize_input(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _deep_dict(payload.get("input"))

    team_id = _team_id_from_payload(payload)
    if team_id and not normalized.get("_team_id"):
        normalized["_team_id"] = team_id

    team_runtime = _team_runtime_from_payload(payload)
    if team_runtime and "_team_runtime" not in normalized:
        normalized["_team_runtime"] = team_runtime

    runtime_memory = _runtime_memory_from_payload(payload)
    if runtime_memory and "_runtime_memory" not in normalized:
        normalized["_runtime_memory"] = runtime_memory

    return normalized


def _resolve_mode(payload: Dict[str, Any], explicit_mode: Optional[str]) -> str:
    mode = _first_non_empty_str(payload.get("mode"), explicit_mode) or "WRITE"
    return mode.upper()


def _safe_mode_token(mode: str) -> str:
    safe = "".join(ch for ch in mode.upper() if ch.isalnum() or ch == "_")
    return safe or "WRITE"


def _resolve_run_id(payload: Dict[str, Any], kwargs: Dict[str, Any]) -> str:
    direct = _first_non_empty_str(payload.get("run_id"), kwargs.get("run_id"))
    if direct:
        return direct

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"run_{stamp}_{uuid4().hex[:8]}"


def _resolve_run_dir(payload: Dict[str, Any], kwargs: Dict[str, Any], run_id: str) -> Path:
    explicit_run_dir = kwargs.get("run_dir") or payload.get("run_dir")
    if isinstance(explicit_run_dir, str) and explicit_run_dir.strip():
        return Path(explicit_run_dir)

    runs_root = (
        kwargs.get("runs_root")
        or payload.get("runs_root")
        or os.environ.get("RUNS_DIR")
        or "runs"
    )
    return Path(str(runs_root)) / run_id


def _next_step_index(steps_dir: Path) -> int:
    max_idx = 0
    if not steps_dir.exists():
        return 1

    for file in steps_dir.glob("*.json"):
        stem = file.stem
        prefix = stem.split("_", 1)[0]
        if prefix.isdigit():
            idx = int(prefix)
            if idx > max_idx:
                max_idx = idx
    return max_idx + 1


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_output(payload: Dict[str, Any], mode: str) -> Dict[str, Any]:
    output = payload.get("output")
    if isinstance(output, dict):
        return copy.deepcopy(output)

    text = payload.get("text")
    if isinstance(text, str):
        return {"text": text, "mode": mode}

    return {"mode": mode}


def execute_stub(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """
    Backward-compatible orchestrator stub entrypoint.

    Supported forms:
    - execute_stub(payload_dict)
    - execute_stub("WRITE", payload_dict)
    - execute_stub(payload=payload_dict, mode="WRITE")
    """
    explicit_mode = kwargs.get("mode")
    payload: Dict[str, Any] = {}

    if args:
        first = args[0]
        if isinstance(first, dict):
            payload = copy.deepcopy(first)
        elif isinstance(first, str):
            explicit_mode = explicit_mode or first
            if len(args) > 1 and isinstance(args[1], dict):
                payload = copy.deepcopy(args[1])

    kw_payload = kwargs.get("payload")
    if isinstance(kw_payload, dict):
        payload = copy.deepcopy(kw_payload)

    mode = _resolve_mode(payload, explicit_mode)
    mode_token = _safe_mode_token(mode)

    normalized_input = _normalize_input(payload)
    run_id = _resolve_run_id(payload, kwargs)
    run_dir = _resolve_run_dir(payload, kwargs, run_id)

    artifacts_dir = run_dir / "artifacts"
    steps_dir = artifacts_dir / "steps"
    step_index = _next_step_index(steps_dir)

    step_file = steps_dir / f"{step_index:03d}_{mode_token}.json"
    flat_file = artifacts_dir / f"{mode_token.lower()}_step.json"

    step_record: Dict[str, Any] = {
        "run_id": run_id,
        "mode": mode,
        "tool": payload.get("tool") or TOOL_BY_MODE.get(mode, mode.lower()),
        "status": "ok",
        "timestamp": _utc_now_iso(),
        "input": normalized_input,
        "output": _build_output(payload, mode),
        "meta": {
            "team_id": normalized_input.get("_team_id"),
            "step_index": step_index,
        },
    }

    _write_json(flat_file, step_record)
    _write_json(step_file, step_record)

    return {
        "status": "ok",
        "ok": True,
        "run_id": run_id,
        "mode": mode,
        "input": normalized_input,
        "artifact": step_record,
        "artifacts": [step_record],
        "result": {
            "run_id": run_id,
            "artifact": step_record,
            "artifact_paths": {
                "flat": str(flat_file),
                "step": str(step_file),
            },
        },
        "artifact_paths": {
            "flat": str(flat_file),
            "step": str(step_file),
        },
        "flat_artifact_path": str(flat_file),
        "step_artifact_path": str(step_file),
    }


def execute(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return execute_stub(*args, **kwargs)


def run_step(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return execute_stub(*args, **kwargs)


def orchestrate(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return execute_stub(*args, **kwargs)


def run_orchestrator_step(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return execute_stub(*args, **kwargs)


__all__ = [
    "TOOL_BY_MODE",
    "MODE_TO_TOOL",
    "execute_stub",
    "execute",
    "run_step",
    "orchestrate",
    "run_orchestrator_step",
]

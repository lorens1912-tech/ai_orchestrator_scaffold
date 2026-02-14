from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

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


class ResolvedModes(dict):
    """
    Dict-like + tuple-unpack compatible container:
    mode, strict_team, team_id = resolve_modes(...)
    """

    def __iter__(self):
        yield self.get("mode")
        yield self.get("strict_team")
        yield self.get("team_id")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _as_dict(value: Any) -> Dict[str, Any]:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _first_non_empty_str(*values: Any) -> Optional[str]:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _deep_get(dct: Any, *path: str) -> Any:
    cur = dct
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _effective_body(envelope: Dict[str, Any]) -> Dict[str, Any]:
    inner = envelope.get("payload")
    if isinstance(inner, dict):
        return _as_dict(inner)
    return _as_dict(envelope)


def _extract_team_id(envelope: Dict[str, Any], body: Dict[str, Any]) -> Optional[str]:
    return _first_non_empty_str(
        _deep_get(body, "_team_id"),
        _deep_get(body, "team_id"),
        _deep_get(body, "team", "id"),
        _deep_get(body, "runtime", "team_id"),
        _deep_get(body, "team_runtime", "team_id"),
        _deep_get(envelope, "_team_id"),
        _deep_get(envelope, "team_id"),
        _deep_get(envelope, "team", "id"),
        _deep_get(envelope, "runtime", "team_id"),
        _deep_get(envelope, "team_runtime", "team_id"),
    )


def _extract_team_runtime(envelope: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    return (
        _as_dict(body.get("_team_runtime"))
        or _as_dict(body.get("team_runtime"))
        or _as_dict(body.get("runtime"))
        or _as_dict(envelope.get("_team_runtime"))
        or _as_dict(envelope.get("team_runtime"))
        or _as_dict(envelope.get("runtime"))
        or {}
    )


def _extract_runtime_memory(envelope: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    return (
        _as_dict(body.get("_runtime_memory"))
        or _as_dict(body.get("runtime_memory"))
        or _as_dict(body.get("memory"))
        or _as_dict(envelope.get("_runtime_memory"))
        or _as_dict(envelope.get("runtime_memory"))
        or _as_dict(envelope.get("memory"))
        or {}
    )


def _safe_mode_token(mode: str) -> str:
    safe = "".join(ch for ch in mode.upper() if ch.isalnum() or ch == "_")
    return safe or "WRITE"


def _resolve_mode(envelope: Dict[str, Any], body: Dict[str, Any], explicit_mode: Optional[str]) -> str:
    mode = (
        _first_non_empty_str(
            explicit_mode,
            envelope.get("mode"),
            body.get("mode"),
        )
        or "WRITE"
    )
    return mode.upper()


def _resolve_run_id(envelope: Dict[str, Any], body: Dict[str, Any], kwargs: Dict[str, Any]) -> str:
    direct = _first_non_empty_str(
        envelope.get("run_id"),
        body.get("run_id"),
        kwargs.get("run_id"),
    )
    if direct:
        return direct

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = os.urandom(4).hex()
    return f"run_{stamp}_{suffix}"


def _resolve_run_dir(envelope: Dict[str, Any], kwargs: Dict[str, Any], run_id: str) -> Path:
    explicit_run_dir = kwargs.get("run_dir") or envelope.get("run_dir")
    if isinstance(explicit_run_dir, str) and explicit_run_dir.strip():
        return Path(explicit_run_dir)

    runs_root = (
        kwargs.get("runs_root")
        or envelope.get("runs_root")
        or os.environ.get("RUNS_DIR")
        or "runs"
    )
    return Path(str(runs_root)) / run_id


def _next_step_index(steps_dir: Path) -> int:
    if not steps_dir.exists():
        return 1

    max_idx = 0
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


def _build_output(envelope: Dict[str, Any], body: Dict[str, Any], mode: str) -> Dict[str, Any]:
    output = body.get("output")
    if isinstance(output, dict):
        return copy.deepcopy(output)

    text = body.get("text")
    if isinstance(text, str):
        return {"text": text, "mode": mode}

    env_output = envelope.get("output")
    if isinstance(env_output, dict):
        return copy.deepcopy(env_output)

    return {"mode": mode}


def _extract_envelope_and_mode(args: tuple[Any, ...], kwargs: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[str]]:
    explicit_mode = kwargs.get("mode")
    envelope: Dict[str, Any] = {}

    if args:
        first = args[0]
        if isinstance(first, dict):
            envelope = copy.deepcopy(first)
        elif isinstance(first, str):
            explicit_mode = explicit_mode or first
            if len(args) > 1 and isinstance(args[1], dict):
                envelope = copy.deepcopy(args[1])

    kw_payload = kwargs.get("payload")
    if isinstance(kw_payload, dict):
        envelope = copy.deepcopy(kw_payload)

    return envelope, explicit_mode


def resolve_modes(*args: Any, **kwargs: Any) -> ResolvedModes:
    envelope, explicit_mode = _extract_envelope_and_mode(args, kwargs)
    body = _effective_body(envelope)

    mode = _resolve_mode(envelope, body, explicit_mode)
    team_id = _extract_team_id(envelope, body)
    team_runtime = _extract_team_runtime(envelope, body)
    runtime_memory = _extract_runtime_memory(envelope, body)

    if team_id and isinstance(team_runtime, dict) and team_runtime:
        strict_team = True
    else:
        strict_team = bool(team_id)

    return ResolvedModes(
        mode=mode,
        strict_team=strict_team,
        team_id=team_id,
        team_runtime=team_runtime,
        runtime_memory=runtime_memory,
    )


def _normalize_input(envelope: Dict[str, Any], body: Dict[str, Any], resolved: ResolvedModes) -> Dict[str, Any]:
    inp = _as_dict(body.get("input")) or _as_dict(envelope.get("input")) or {}

    team_id = resolved.get("team_id")
    if team_id and not inp.get("_team_id"):
        inp["_team_id"] = team_id

    team_runtime = _as_dict(resolved.get("team_runtime"))
    if team_runtime and "_team_runtime" not in inp:
        inp["_team_runtime"] = team_runtime

    runtime_memory = _as_dict(resolved.get("runtime_memory"))
    if runtime_memory and "_runtime_memory" not in inp:
        inp["_runtime_memory"] = runtime_memory

    return inp


def execute_stub(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """
    Backward-compatible orchestrator stub entrypoint.
    Supported forms:
    - execute_stub(payload_dict)
    - execute_stub("WRITE", payload_dict)
    - execute_stub(payload=payload_dict, mode="WRITE")
    """
    envelope, explicit_mode = _extract_envelope_and_mode(args, kwargs)
    body = _effective_body(envelope)

    resolved = resolve_modes(envelope, mode=explicit_mode)
    mode = str(resolved.get("mode") or "WRITE").upper()
    mode_token = _safe_mode_token(mode)

    normalized_input = _normalize_input(envelope, body, resolved)
    run_id = _resolve_run_id(envelope, body, kwargs)
    run_dir = _resolve_run_dir(envelope, kwargs, run_id)

    artifacts_dir = run_dir / "artifacts"
    steps_dir = artifacts_dir / "steps"
    step_index = _next_step_index(steps_dir)

    step_file = steps_dir / f"{step_index:03d}_{mode_token}.json"
    flat_file = artifacts_dir / f"{mode_token.lower()}_step.json"

    tool_name = (
        _first_non_empty_str(
            body.get("tool"),
            envelope.get("tool"),
            TOOL_BY_MODE.get(mode),
        )
        or mode.lower()
    )

    step_record: Dict[str, Any] = {
        "run_id": run_id,
        "mode": mode,
        "tool": tool_name,
        "status": "ok",
        "timestamp": _utc_now_iso(),
        "input": normalized_input,
        "output": _build_output(envelope, body, mode),
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
        "artifacts": [str(step_file)],
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
    "ResolvedModes",
    "resolve_modes",
    "execute_stub",
    "execute",
    "run_step",
    "orchestrate",
    "run_orchestrator_step",
]

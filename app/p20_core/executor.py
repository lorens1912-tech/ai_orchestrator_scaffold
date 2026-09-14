from __future__ import annotations

import inspect
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from app.config_registry import load_modes, load_presets
from app.model_policy import resolve_model
from app.p20_core.context_runtime import (
    ProjectExecutionContext,
    build_runtime_context_package,
)
from app.p20_core.storage_paths import get_books_root, get_runs_root, get_storage_root
from app.team_resolver import resolve_team
from app.tools import TOOLS, _p15_hardfail_quality_payload


TEXT_MODES = {
    "CRITIC",
    "EDIT",
    "REWRITE",
    "QUALITY",
    "UNIQUENESS",
    "CONTINUITY",
    "FACTCHECK",
    "STYLE",
    "TRANSLATE",
    "EXPAND",
}

StepItem = Union[str, Dict[str, Any]]


def _iso() -> str:
    return datetime.utcnow().isoformat()


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_active_presets() -> List[Dict[str, Any]]:
    raw = load_presets()
    presets = raw.get("presets") if isinstance(raw, dict) else raw
    if not isinstance(presets, list):
        raise ValueError("presets must be a list")
    return [item for item in presets if isinstance(item, dict) and item.get("id")]


def _find_preset(preset_id: str) -> Optional[Dict[str, Any]]:
    pid = str(preset_id or "").upper().strip()
    if not pid:
        return None
    for item in _load_active_presets():
        item_id = str(item.get("id") or item.get("preset_id") or item.get("name") or "").upper().strip()
        if item_id == pid:
            return item
    return None


def _preset_doc(preset_id: str) -> Dict[str, Any]:
    doc = _find_preset(preset_id)
    if isinstance(doc, dict):
        return doc
    raise ValueError(f"Unknown preset: {str(preset_id or '').upper().strip()}")


def _preset_modes(preset_id: str) -> List[str]:
    doc = _preset_doc(preset_id)
    modes = doc.get("modes")
    if isinstance(modes, list) and modes:
        return [str(item).upper().strip() for item in modes if str(item).strip()]

    steps = doc.get("steps")
    if isinstance(steps, list):
        out = [
            str(item.get("mode")).upper().strip()
            for item in steps
            if isinstance(item, dict) and item.get("mode")
        ]
        if out:
            return out

    return ["WRITE"]


def _known_mode_ids() -> set[str]:
    raw = load_modes()
    modes = raw.get("modes") if isinstance(raw, dict) else raw
    if not isinstance(modes, list):
        return set()
    return {
        str(item.get("id") or "").upper().strip()
        for item in modes
        if isinstance(item, dict) and item.get("id")
    }


def _validate_modes(modes: List[str]) -> None:
    known = _known_mode_ids()
    if not known:
        return
    for mode_id in modes:
        if mode_id not in known:
            raise ValueError(f"Unknown mode: {mode_id}")


def resolve_modes(arg1: Any = None, arg2: Any = None, **kwargs) -> Tuple[List[str], Optional[str], Dict[str, Any]]:
    if kwargs:
        payload = kwargs.get("payload") if isinstance(kwargs.get("payload"), dict) else {}
        payload = _p15_hardfail_quality_payload(payload) or payload
        preset_id = kwargs.get("preset_id") or kwargs.get("preset") or payload.get("preset")
        modes_kw = _normalize_modes_list(kwargs.get("modes"))
        if preset_id:
            preset_key = str(preset_id).upper().strip()
            payload.setdefault("preset", preset_key)
            payload.setdefault("_preset_id", preset_key)
            modes = _preset_modes(preset_key)
            _validate_modes(modes)
            return modes, preset_key, payload
        if modes_kw:
            _validate_modes(modes_kw)
            return modes_kw, None, payload
        mode = payload.get("mode")
        if mode:
            modes = [str(mode).upper().strip()]
            _validate_modes(modes)
            return modes, None, payload

    if isinstance(arg2, str) and arg1 is None:
        preset_key = str(arg2).upper().strip()
        payload = {"preset": preset_key, "_preset_id": preset_key}
        modes = _preset_modes(preset_key)
        _validate_modes(modes)
        return modes, preset_key, payload

    payload = _p15_hardfail_quality_payload(arg1) if isinstance(arg1, dict) else arg2
    if not isinstance(payload, dict):
        raise TypeError("resolve_modes expects payload dict (or None, preset_id)")

    preset_id = payload.get("preset")
    if preset_id:
        preset_key = str(preset_id).upper().strip()
        payload.setdefault("_preset_id", preset_key)
        payload["preset"] = preset_key
        modes = _preset_modes(preset_key)
        _validate_modes(modes)
        return modes, preset_key, payload

    mode = payload.get("mode")
    modes = _normalize_modes_list(payload.get("modes"))
    if mode and not modes:
        modes = [str(mode).upper().strip()]

    if not modes:
        raise ValueError("No mode or preset specified")

    _validate_modes(modes)
    return modes, None, payload


def _preset_steps(preset_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    if not preset_id:
        return None
    doc = _find_preset(preset_id)
    if not isinstance(doc, dict):
        return None
    steps = doc.get("steps")
    if isinstance(steps, list) and all(isinstance(item, dict) and item.get("mode") for item in steps):
        return list(steps)
    return None


def _call_tool_tolerant(tool_fn, payload: Dict[str, Any], run_dir: Path):
    try:
        return tool_fn(payload, run_dir=run_dir)
    except TypeError as exc:
        if "unexpected keyword argument" in str(exc) and "run_dir" in str(exc):
            return tool_fn(payload)
        raise


def _normalize_modes_list(modes: Any) -> List[str]:
    if isinstance(modes, str):
        modes = [modes]
    if isinstance(modes, tuple):
        modes = list(modes)
    if not isinstance(modes, list):
        return []
    return [str(item).strip().upper() for item in modes if str(item).strip()]


def _step_to_mode_and_overrides(item: StepItem) -> Tuple[str, Dict[str, Any]]:
    if isinstance(item, dict):
        return str(item.get("mode") or "").strip().upper(), item
    return str(item).strip().upper(), {}


def _runtime_override_for(payload: Dict[str, Any], mode_id: str) -> Dict[str, Any]:
    overrides = payload.get("runtime_overrides")
    if not isinstance(overrides, dict):
        return {}
    candidate = overrides.get(mode_id)
    if candidate is None:
        candidate = overrides.get(mode_id.upper())
    if candidate is None:
        candidate = overrides.get(mode_id.lower())
    return candidate if isinstance(candidate, dict) else {}


def _quality_decision(result: Dict[str, Any]) -> str:
    payload = result.get("payload") if isinstance(result, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    return str(payload.get("DECISION") or payload.get("decision") or "").upper().strip()


def _quality_retry_steps(preset_doc: Dict[str, Any], decision: str, attempts_done: int) -> List[StepItem]:
    retry = preset_doc.get("quality_retry") if isinstance(preset_doc, dict) else None
    if not isinstance(retry, dict):
        return []

    try:
        max_attempts = int(retry.get("max_attempts") or 0)
    except Exception:
        max_attempts = 0
    if attempts_done >= max_attempts:
        return []

    retry_on = retry.get("on")
    if not isinstance(retry_on, list) or not retry_on:
        retry_on = ["REVISE", "REJECT"]
    retry_on = [str(item).upper().strip() for item in retry_on]
    if str(decision).upper().strip() not in retry_on:
        return []

    edit_mode = str(retry.get("edit_mode") or "EDIT").upper().strip() or "EDIT"
    return [{"mode": edit_mode}, {"mode": "QUALITY"}]


def _normalize_execute_call(*args, **kwargs) -> Tuple[str, str, List[str], Dict[str, Any], Optional[List[Any]], bool]:
    run_id = kwargs.get("run_id")
    book_id = kwargs.get("book_id")
    modes = kwargs.get("modes")
    explicit_modes_arg = modes is not None
    payload = kwargs.get("payload")
    steps = kwargs.get("steps")

    rem: List[Any] = []
    if args:
        run_id = run_id or args[0]
        rem = list(args[1:])

    if rem:
        if len(rem) >= 3 and isinstance(rem[0], str) and isinstance(rem[1], (list, tuple)) and isinstance(rem[2], dict):
            book_id = book_id or rem[0]
            modes = modes or list(rem[1])
            explicit_modes_arg = explicit_modes_arg or modes is not None
            payload = (_p15_hardfail_quality_payload(payload) if isinstance(payload, dict) else payload) or rem[2]
            if len(rem) >= 4 and steps is None:
                steps = rem[3]
        elif len(rem) >= 2 and isinstance(rem[0], (list, tuple)) and isinstance(rem[1], dict):
            modes = modes or list(rem[0])
            explicit_modes_arg = explicit_modes_arg or modes is not None
            payload = (_p15_hardfail_quality_payload(payload) if isinstance(payload, dict) else payload) or rem[1]
            if len(rem) >= 3 and steps is None:
                steps = rem[2]
        elif len(rem) >= 1 and isinstance(rem[0], dict):
            payload = (_p15_hardfail_quality_payload(payload) if isinstance(payload, dict) else payload) or rem[0]

    if not isinstance(payload, dict):
        payload = {}
    payload = _p15_hardfail_quality_payload(payload) or payload

    modes = _normalize_modes_list(modes if modes is not None else payload.get("modes"))
    if not modes:
        mode_single = payload.get("mode")
        if mode_single:
            modes = [str(mode_single).upper().strip()]

    if not book_id:
        book_id = payload.get("book_id") or "book_runtime_test"

    if not run_id:
        run_id = f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

    return str(run_id), str(book_id), modes, payload, steps, explicit_modes_arg


def _public_storage_path(path_like: Any) -> str:
    path = Path(path_like)
    try:
        return path.resolve().relative_to(get_storage_root().resolve()).as_posix()
    except ValueError:
        return str(path_like).replace("\\", "/")


def _storage_path(path_like: Any) -> Path:
    path = Path(path_like)
    if path.is_absolute():
        return path
    parts = path.parts
    if parts and parts[0] in {"books", "runs", "audit", "novel_runs"}:
        return get_storage_root() / path
    return path


def _run_dir(run_id: str) -> Path:
    return get_runs_root() / str(run_id)


def _run_steps_dir(run_id: str) -> Path:
    return _run_dir(run_id) / "steps"


def _merge_preset_context(payload: Dict[str, Any], preset_id: str, preset_doc: Dict[str, Any]) -> None:
    context = payload.get("context")
    if not isinstance(context, dict):
        context = {}
        payload["context"] = context

    merged = dict(context.get("preset") or {})
    for key, value in dict(preset_doc).items():
        if key == "quality_thresholds":
            thresholds = dict(merged.get("quality_thresholds") or {})
            thresholds.update(dict(value or {}))
            merged["quality_thresholds"] = thresholds
        else:
            merged[key] = value

    if preset_id == "WRITING_STANDARD":
        thresholds = dict(merged.get("quality_thresholds") or {})
        thresholds["accept_min"] = float(thresholds.get("accept_min", 0.70))
        thresholds["revise_min"] = float(thresholds.get("revise_min", 0.60))
        merged["quality_thresholds"] = thresholds

    merged["id"] = preset_id
    context["preset"] = merged


def _queue_modes(queue: List[StepItem]) -> List[str]:
    modes: List[str] = []
    for item in queue:
        mode_id, _overrides = _step_to_mode_and_overrides(item)
        if mode_id:
            modes.append(mode_id)
    return modes


def _write_sequence_artifact(
    *,
    steps_dir: Path,
    run_id: str,
    book_id: str,
    preset_id: Optional[str],
    queue_initial: List[StepItem],
    modes: List[str],
) -> None:
    steps_by_mode: Dict[str, Dict[str, Any]] = {}
    for item in queue_initial:
        mode_id, overrides = _step_to_mode_and_overrides(item)
        if mode_id and overrides:
            steps_by_mode[mode_id] = dict(overrides)

    _atomic_write_json(
        steps_dir / "000_SEQUENCE.json",
        {
            "sequence_version": 1,
            "mode": "SEQUENCE",
            "book_id": book_id,
            "run_id": run_id,
            "preset_id": preset_id,
            "queue_initial": queue_initial,
            "created_at": _iso(),
            "team": {
                "id": "SYSTEM",
                "team_id": "SYSTEM",
                "policy_id": "SYSTEM",
            },
            "effective_policy_id": "SYSTEM",
            "effective_policy": {
                "model": "SYSTEM",
            },
            "input": {
                "preset": preset_id,
                "modes": modes,
            },
            "result": {
                "tool": "SEQUENCE",
                "payload": {
                    "preset_id": preset_id,
                    "modes": modes,
                    "steps": [
                        {
                            "index": index + 1,
                            "mode": mode_id,
                            "requested_policy": steps_by_mode.get(mode_id, {}).get("policy"),
                            "effective_policy_id": steps_by_mode.get(mode_id, {}).get("policy"),
                        }
                        for index, mode_id in enumerate(modes)
                    ],
                    "meta": {
                        "effective_policy_id": "SYSTEM",
                        "policy_id": "SYSTEM",
                        "team_id": "SYSTEM",
                    },
                },
            },
        },
    )


def _apply_policy_metadata(result: Dict[str, Any], requested_policy: Any) -> None:
    if not requested_policy:
        return
    payload = result.get("payload")
    if not isinstance(payload, dict):
        payload = {}
        result["payload"] = payload
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        payload["meta"] = meta
    meta["effective_policy_id"] = requested_policy
    meta.setdefault("policy_id", requested_policy)


def _resolve_step_team(mode_id: str, team_override: Any, preset_id: Optional[str]) -> Dict[str, Any]:
    try:
        return resolve_team(mode_id, team_override=str(team_override) if team_override else None)
    except ValueError as exc:
        if preset_id and team_override and str(exc).startswith("TEAM_OVERRIDE_NOT_ALLOWED:"):
            return resolve_team(mode_id)
        raise


def _models_for_step(
    *,
    step_overrides: Dict[str, Any],
    runtime_overrides: Dict[str, Any],
    tool_input: Dict[str, Any],
    team: Dict[str, Any],
) -> Tuple[Optional[str], str]:
    requested = (
        step_overrides.get("model")
        or runtime_overrides.get("model")
        or tool_input.get("requested_model")
        or tool_input.get("model")
    )
    decision = resolve_model(
        str(requested) if requested else None,
        preset_model=str(team.get("model") or "") or None,
    )
    requested_identity = str(requested or team.get("model") or "").strip() or None
    return requested_identity, decision.effective_model


def execute_p20(*args, **kwargs) -> List[str]:
    run_id, book_id, modes, payload, steps, explicit_modes_arg = _normalize_execute_call(*args, **kwargs)
    execution_context = kwargs.get("execution_context")
    context_sources = kwargs.get("context_sources")
    if execution_context is not None and not isinstance(execution_context, ProjectExecutionContext):
        raise TypeError("execution_context must be ProjectExecutionContext")
    if context_sources is None:
        context_sources = {}
    if not isinstance(context_sources, dict):
        raise TypeError("context_sources must be a mapping")

    if not modes:
        modes, _preset_id, payload = resolve_modes(payload)

    payload_exec = dict(payload)
    modes_exec = list(modes)
    preset_id = str(
        payload_exec.get("_preset_id")
        or payload_exec.get("preset")
        or kwargs.get("preset")
        or ""
    ).upper().strip()
    preset_doc = _find_preset(preset_id) if preset_id else None

    if preset_id and not isinstance(preset_doc, dict):
        raise ValueError(f"Unknown preset: {preset_id}")

    if preset_id and isinstance(preset_doc, dict):
        if not explicit_modes_arg:
            modes_exec = _preset_modes(preset_id)
        _merge_preset_context(payload_exec, preset_id, preset_doc)

    if not modes_exec:
        if payload_exec.get("mode"):
            modes_exec = [str(payload_exec.get("mode")).upper().strip()]
        else:
            raise ValueError("No mode or preset specified")

    _validate_modes(modes_exec)

    run_dir = _run_dir(run_id)
    steps_dir = _run_steps_dir(run_id)
    steps_dir.mkdir(parents=True, exist_ok=True)

    state_path = run_dir / "state.json"
    state: Dict[str, Any] = {"run_id": run_id, "latest_text": "", "last_step": 0, "created_at": _iso()}
    latest_text = ""
    artifact_paths: List[str] = []

    queue: List[StepItem]
    if isinstance(steps, list) and steps:
        queue = list(steps)
    else:
        preset_steps = _preset_steps(preset_id) if preset_id and not explicit_modes_arg else None
        queue = list(preset_steps) if preset_steps else [{"mode": mode_id} for mode_id in modes_exec]

    initial_queue = list(queue)
    initial_modes = _queue_modes(initial_queue)
    _write_sequence_artifact(
        steps_dir=steps_dir,
        run_id=run_id,
        book_id=book_id,
        preset_id=preset_id or None,
        queue_initial=initial_queue,
        modes=initial_modes,
    )

    quality_retry_attempts = 0
    step_index = 0

    while queue:
        item = queue.pop(0)
        mode_id, step_overrides = _step_to_mode_and_overrides(item)
        if not mode_id:
            continue

        _validate_modes([mode_id])
        step_index += 1
        runtime_overrides = _runtime_override_for(payload_exec, mode_id)

        team_override = (
            step_overrides.get("team_id")
            or step_overrides.get("team")
            or runtime_overrides.get("team_id")
            or runtime_overrides.get("team")
            or payload_exec.get("team_id")
            or payload_exec.get("team")
        )
        team = _resolve_step_team(mode_id, team_override, preset_id or None)

        tool_input: Dict[str, Any] = dict(payload_exec)
        if isinstance(runtime_overrides.get("payload"), dict):
            tool_input.update(runtime_overrides["payload"])
        if isinstance(step_overrides.get("payload"), dict):
            tool_input.update(step_overrides["payload"])

        tool_input.setdefault("book_id", book_id)

        requested_policy = (
            step_overrides.get("policy")
            or runtime_overrides.get("policy")
            or tool_input.get("requested_policy")
            or team.get("policy_id")
        )
        requested_model, effective_model = _models_for_step(
            step_overrides=step_overrides,
            runtime_overrides=runtime_overrides,
            tool_input=tool_input,
            team=team,
        )

        team_id = str(team.get("id") or team.get("team_id") or "").strip()
        team_policy_id = str(team.get("policy_id") or "").strip()
        team_prompts = team.get("prompts")
        if team_id:
            tool_input["team"] = team_id
            tool_input["team_id"] = team_id
            tool_input["_team_id"] = team_id
        if team_policy_id:
            tool_input["_team_policy_id"] = team_policy_id
        if effective_model:
            tool_input["_team_model"] = effective_model
            tool_input["_requested_model"] = effective_model
            tool_input["_effective_model"] = effective_model
            tool_input["requested_model"] = effective_model
        if isinstance(team_prompts, dict):
            tool_input["_team_prompts"] = team_prompts
        if requested_policy:
            tool_input["_requested_policy"] = requested_policy
            tool_input["requested_policy"] = requested_policy

        if mode_id in TEXT_MODES:
            tool_input["text"] = latest_text if latest_text else str(tool_input.get("text") or "")

        step_execution_context = None
        context_package = None
        if execution_context is not None:
            step_execution_context = execution_context.for_step(step_index, mode_id)
            context_package = build_runtime_context_package(
                execution_context=step_execution_context,
                mode=mode_id,
                role=team_id,
                requested_model=requested_model,
                effective_model=effective_model,
                tool_input=tool_input,
                context_sources=context_sources,
            )
            tool_input["context_package_id"] = context_package.context_package_id
            tool_input["context_hash"] = context_package.context_hash
            tool_input["_context_package"] = context_package.to_dict()

        if mode_id not in TOOLS:
            result: Dict[str, Any] = {"ok": False, "error": f"UNKNOWN_MODE_TOOL: {mode_id}", "tool": mode_id}
        else:
            out = _call_tool_tolerant(TOOLS[mode_id], tool_input, run_dir)
            if inspect.isawaitable(out):
                raise RuntimeError(f"Tool {mode_id} returned awaitable in sync execute_p20")
            result = (
                out
                if isinstance(out, dict)
                else {"ok": False, "error": "TOOL_RETURNED_NON_DICT", "tool": mode_id, "raw_result": str(out)}
            )
            result.setdefault("tool", mode_id)

        _apply_policy_metadata(result, requested_policy)

        result_payload = result.get("payload") if isinstance(result, dict) else {}
        if isinstance(result_payload, dict) and result_payload.get("text"):
            latest_text = str(result_payload["text"])

        step_doc = {
            "run_id": run_id,
            "index": step_index,
            "mode": mode_id,
            "role": team_id,
            "team": team,
            "requested_model": requested_model,
            "effective_model": effective_model,
            "effective_model_id": effective_model,
            "effective_policy_id": requested_policy,
            "preset_id": preset_id or None,
            "preset_step": step_overrides if step_overrides else None,
            "runtime_override": runtime_overrides if runtime_overrides else None,
            "input": tool_input,
            "result": result,
            "created_at": _iso(),
        }
        if step_execution_context is not None and context_package is not None:
            step_doc.update({
                "project_id": step_execution_context.project_id,
                "book_id": step_execution_context.book_id,
                "series_id": step_execution_context.series_id,
                "step_id": step_execution_context.step_id,
                "context_package_id": context_package.context_package_id,
                "context_hash": context_package.context_hash,
                "context_role": context_package.role.value,
            })

        step_path = steps_dir / f"{step_index:03d}_{mode_id}.json"
        if step_path.exists():
            base, ext = step_path.stem, step_path.suffix
            attempt = 2
            while True:
                candidate = step_path.with_name(f"{base}__attempt_{attempt:02d}{ext}")
                if not candidate.exists():
                    step_path = candidate
                    break
                attempt += 1

        _atomic_write_json(step_path, step_doc)
        artifact_paths.append(_public_storage_path(step_path))

        if mode_id == "QUALITY" and isinstance(preset_doc, dict):
            quality_decision = _quality_decision(result)
            if quality_decision and quality_decision != "ACCEPT":
                retry_steps = _quality_retry_steps(preset_doc, quality_decision, quality_retry_attempts)
                if retry_steps:
                    quality_retry_attempts += 1
                    queue = list(retry_steps) + queue
                    continue

                if bool(preset_doc.get("stop_on_quality_non_accept")):
                    state["stopped"] = True
                    state["stop_reason"] = "QUALITY_NON_ACCEPT"
                    state["stop"] = {
                        "mode": "QUALITY",
                        "decision": quality_decision,
                        "blocked": True,
                    }
                    break

    state["last_step"] = step_index
    state["completed_steps"] = step_index
    state["latest_text"] = latest_text
    state["status"] = "DONE"
    _atomic_write_json(state_path, state)

    book_dir = get_books_root() / book_id / "draft"
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "latest.txt").write_text(latest_text, encoding="utf-8")

    return artifact_paths


execute_stub = execute_p20

__all__ = ["execute_p20", "execute_stub", "resolve_modes"]

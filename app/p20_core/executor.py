from __future__ import annotations

import inspect
import copy
import hashlib
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
from app.p20_core.adaptive_style import (
    AdaptiveStyleSession,
    prepare_adaptive_style_session,
)
from app.p20_core.project_repository import ProjectRepository, StorageResolver
from app.p20_core.evaluation import (
    EvaluationBinding,
    EvaluationConflict,
    canonical_hash as evaluation_hash,
    finalize_evaluation,
    start_evaluation,
)


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


class MemoryModelInvocationError(RuntimeError):
    """Safe failure wrapper carrying the durable invocation lineage."""

    def __init__(self, failure_reason: str, evidence: dict) -> None:
        super().__init__(failure_reason)
        self.failure_reason = failure_reason
        self.evidence = evidence


def invoke_memory_model(*, execution_context: ProjectExecutionContext, role: str,
                        requested_model: str | None, effective_model: str,
                        source: dict, candidate: dict | None,
                        context_sources: dict, model_routing: dict | None = None) -> dict:
    """Internal integrity call through the same ContextBuilder/provider boundary.

    These are internal roles, not additional user-selectable execution modes.
    """
    from app.p20_core.memory_extraction import ModelInvocation, validate_memory_model_result
    from dataclasses import fields
    from app.p20_core.memory_extraction import memory_record_types
    payload = {"project_id": execution_context.project_id, "book_id": execution_context.book_id,
               "series_id": execution_context.series_id, "run_id": execution_context.run_id,
               "step_id": execution_context.step_id, "source": source, "candidate": candidate,
               "role": role, "_requested_model": requested_model,
               "_effective_model": effective_model,
               "record_schema": {kind: {field.name: str(field.type) for field in fields(record_type)}
                                 for kind, record_type in memory_record_types().items()},
               "instruction": ("Return the complete records list as [{record_type: schema name, payload: full record}]. "
                               "Explicit boolean frozen and author_locked are required for every record. Preserve IDs and protection from context; "
                               "use current version + 1 for updates and 1 for creates. Bind provenance to the supplied source. "
                               "Do not discard unsupported entities: fail explicitly if the complete set cannot be represented."
                               if role == "EXTRACTOR" else
                               "Independently verify every record against source. Return precision_status and completeness_status: ACCEPT, REVISE or REJECT, with reasons.")}
    package = build_runtime_context_package(execution_context=execution_context, mode="MEMORY_" + role,
        role=role, requested_model=requested_model, effective_model=effective_model,
        tool_input=payload, context_sources=context_sources)
    payload.update(context_package_id=package.context_package_id, context_hash=package.context_hash,
                   _context_package=package.to_dict())
    metadata = {"context_package_id": package.context_package_id, "context_hash": package.context_hash,
                "requested_model": requested_model, "effective_model": effective_model}
    provider = "P20_PROVIDER_BOUNDARY"
    from app.p20_core.model_provenance import ModelInvocationAudit
    audit_repository = ProjectRepository(StorageResolver().resolve_project(
        execution_context.project_id, book_id=execution_context.book_id))
    invocation_audit = ModelInvocationAudit(audit_repository, execution_context, package,
        role=role, mode="MEMORY_" + role, requested_model=requested_model,
        routing=model_routing,
        artifact_refs=[source[k] for k in ("artifact_ref", "accepted_step_artifact") if k in source],
        scope="SERIES" if "series_metadata:" in str(source.get("artifact_ref", "")) else "PROJECT")
    try:
        from app.tools import memory_integrity_provider
        provider_result = memory_integrity_provider(payload, invocation_audit=invocation_audit)
        transport = None
        if isinstance(provider_result, dict) and set(provider_result) == {"result", "transport"}:
            result = provider_result["result"]
            transport = provider_result["transport"]
        else:
            result = provider_result
        result = validate_memory_model_result(role, result)
        if transport is not None:
            if (not isinstance(transport, dict)
                    or transport.get("requested_model") != requested_model
                    or transport.get("effective_model") != effective_model
                    or transport.get("provider") != "OPENAI"):
                raise ValueError("memory transport metadata binding mismatch")
            metadata.update(transport)
            provider = transport["provider"]
        invocation_audit.validated(quality=(
            {key: result[key] for key in ("precision_status", "completeness_status") if key in result}
            or None))
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        invocation_audit.validated(status="INVALID", failure_phase=(
            getattr(exc, "audit_metadata", {}) or {}).get("failure_phase"))
        failure_reason = type(exc).__name__
        transport_audit = getattr(exc, "audit_metadata", None)
        if (isinstance(transport_audit, dict)
                and transport_audit.get("requested_model") == requested_model
                and transport_audit.get("effective_model") == effective_model):
            allowed = {
                "boundary", "provider", "requested_model", "effective_model",
                "provider_returned_model", "refused", "raw_type", "params", "dropped_params",
                "retried", "boundary_reached", "failure_phase",
            }
            metadata.update({key: value for key, value in transport_audit.items() if key in allowed})
            if transport_audit.get("provider") == "OPENAI":
                provider = "OPENAI"
        metadata.update(status="FAILED", failure_reason=failure_reason)
        invocation = ModelInvocation(role=role, provider=provider, model=effective_model,
            call_id=execution_context.operation_id, metadata=metadata)
        evidence = {
            "status": "FAILED",
            "reason": failure_reason,
            "invocation": invocation.to_dict(),
        }
        raise MemoryModelInvocationError(failure_reason, evidence) from exc
    invocation = ModelInvocation(role=role, provider=provider, model=effective_model,
        call_id=execution_context.operation_id, metadata=metadata)
    return {"invocation": invocation.to_dict(), "input": payload, "result": result}


def _iso() -> str:
    return datetime.utcnow().isoformat()


def _atomic_write_json(path: Path, data: Any) -> None:
    if (isinstance(data, dict) and data.get("project_id") and data.get("step_id")
            and data.get("mode") in TOOLS and data.get("context_package_id")):
        from app.p20_core.cross_store_recovery import (
            ArtifactRoot, CrossStoreOperationPlan, CrossStoreRecoveryService,
        )
        repo = ProjectRepository(StorageResolver().resolve_project(
            data["project_id"], book_id=data["book_id"]))
        relative = path.resolve().relative_to(get_runs_root().resolve()).as_posix()
        CrossStoreRecoveryService(repo).execute(CrossStoreOperationPlan(
            operation_id="gap017-step-" + evaluation_hash("GAP017_STEP_PATH_V1", relative),
            project_id=data["project_id"], book_id=data["book_id"],
            run_id=data["run_id"], step_id=data["step_id"],
            operation_type="GAP017_STEP_PROJECTION_V1", source_ref=data["context_package_id"],
            artifact_root=ArtifactRoot.RUNS, artifact_relative_path=relative,
            artifact_bytes=(json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            expected_versions={"context_hash": data["context_hash"]},
            provenance_refs=(data["context_package_id"],),
        ))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _bind_execution_input(execution_context, payload, queue, preset):
    """Pin the retry queue and caller inputs in the existing project owner."""
    repository = ProjectRepository(StorageResolver().resolve_project(
        execution_context.project_id, book_id=execution_context.book_id))
    value = {
        "queue": queue, "preset": preset,
        "input": {k: v for k, v in payload.items()
                  if k not in {"technical_retry", "evaluation_intent"}},
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    key = "p20_execution_input.v1:" + execution_context.operation_id
    repository.initialize()
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM project_metadata WHERE key=?", (key,)).fetchone()
        if row is not None:
            if row["value"] != raw:
                raise EvaluationConflict("evaluation binding changed for the same operation: execution inputs")
        elif execution_context.technical_retry:
            raise EvaluationConflict("technical retry execution inputs are not recorded")
        else:
            connection.execute("INSERT INTO project_metadata(key,value) VALUES (?,?)", (key, raw))
        repository.checkpoint_reevaluation_request(
            connection, execution_context.operation_id, "input_hash",
            hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )


def _completed_step(repository, execution_context, tool_input):
    """Replay an immutable step through its existing F-004 projection owner."""
    from app.p20_core.cross_store_recovery import (
        CrossStoreOperationPlan, CrossStoreRecoveryService, RecoveryInterventionRequired,
    )
    from app.p20_core.evaluation import EvaluationNeedsIntervention
    matches = [item for item in repository.list_cross_store_operations()
               if item.get("operation_type") == "GAP017_STEP_PROJECTION_V1"
               and item.get("payload", {}).get("step_id") == execution_context.step_id
               and item.get("payload", {}).get("run_id") == execution_context.run_id]
    if not matches:
        return None
    # Every attempt is validated; a damaged older projection must not be bypassed.
    documents = []
    for item in matches:
        try:
            record = CrossStoreRecoveryService(repository).recover(item["operation_id"])
        except RecoveryInterventionRequired as exc:
            raise EvaluationNeedsIntervention("EVALUATION_PROJECTION_CONFLICT") from exc
        plan = CrossStoreOperationPlan.from_record(record)
        documents.append(json.loads(plan.artifact_bytes))
    document = sorted(documents, key=lambda value: value["created_at"])[0]
    ignored = {"technical_retry", "evaluation_intent", "_context_package"}
    normalize = lambda value: {k: v for k, v in value.items() if k not in ignored}
    if normalize(document["input"]) != normalize(tool_input):
        raise EvaluationConflict("evaluation binding changed for the same operation: completed step input")
    return copy.deepcopy(document)


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


def _quality_reasons(result: Dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    payload = result.get("payload") if isinstance(result, dict) else None
    if not isinstance(payload, dict):
        return [], []
    raw_reasons = payload.get("REASONS")
    if raw_reasons is None:
        raw_reasons = payload.get("REJECT_REASONS")
    raw_must_fix = payload.get("MUST_FIX")
    reasons = [str(value) for value in raw_reasons] if isinstance(raw_reasons, list) else []
    must_fix = (
        [dict(value) for value in raw_must_fix if isinstance(value, dict)]
        if isinstance(raw_must_fix, list)
        else []
    )
    return reasons, must_fix


def _quality_contract(
    tool_input: Dict[str, Any],
    style_session: AdaptiveStyleSession | None,
) -> tuple[str, str, dict[str, Any], dict[str, Any], dict[str, str]]:
    if "min_words" in tool_input:
        evaluator_id = "app.quality_rules.evaluate_quality"
        evaluator_version = "QUALITY_RULES_V1"
        configuration = {
            "min_words": int(tool_input.get("min_words") or 200),
            "forbid_lists": bool(tool_input.get("forbid_lists", True)),
        }
    else:
        from app.quality_contract import _fq_extract_thresholds
        evaluator_id = "app.quality_contract._fq_tool_quality"
        evaluator_version = "FINAL_QUALITY_CANON_20260325_V4"
        configuration = dict(_fq_extract_thresholds(tool_input))
    criteria: dict[str, Any] = {
        "decision_contract": ["ACCEPT", "REVISE", "REJECT"],
        "evaluator": evaluator_id,
        "version": evaluator_version,
        "configuration": configuration,
        "style_veto": "ADAPTIVE_STYLE_COMPLIANT_V1",
    }
    additional: dict[str, str] = {}
    if style_session is not None:
        criteria["style_recipe_id"] = style_session.recipe.recipe_id
        additional["style_recipe"] = evaluation_hash(
            "GAP017_STYLE_RECIPE_V1", style_session.recipe.to_dict()
        )
        if style_session.latest_evaluation is not None:
            additional["style_evaluation"] = evaluation_hash(
                "GAP017_STYLE_EVALUATION_V1",
                style_session.latest_evaluation.to_dict(),
            )
    return evaluator_id, evaluator_version, criteria, configuration, additional


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
    execution_context: ProjectExecutionContext | None = None,
) -> None:
    identity = {}
    if execution_context is not None:
        identity = {
            "project_id": execution_context.project_id,
            "domain_book_id": execution_context.book_id,
            "series_id": execution_context.series_id,
            "step_id": execution_context.step_id,
        }
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
            **identity,
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
    audit: dict | None = None,
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
    if audit is not None:
        audit.update(decision.provenance)
        audit["request_sources"] = {
            "step": {k: step_overrides[k] for k in ("model",) if k in step_overrides},
            "runtime": {k: runtime_overrides[k] for k in ("model",) if k in runtime_overrides},
            "payload": {k: tool_input[k] for k in ("requested_model", "model") if k in tool_input},
            "team": {k: team[k] for k in ("model",) if k in team},
        }
    if not decision.allowlist_ok and os.getenv("MODEL_POLICY_MODE", "PERMISSIVE").upper() == "STRICT":
        raise ValueError("MODEL_POLICY_DENIED")
    requested_identity = str(requested or team.get("model") or "").strip() or None
    return requested_identity, decision.effective_model


def execute_p20(*args, **kwargs) -> List[str]:
    run_id, book_id, modes, payload, steps, explicit_modes_arg = _normalize_execute_call(*args, **kwargs)
    execution_context = kwargs.get("execution_context")
    context_sources = kwargs.get("context_sources")
    if execution_context is not None and not isinstance(execution_context, ProjectExecutionContext):
        raise TypeError("execution_context must be ProjectExecutionContext")
    if execution_context is not None and execution_context.run_id != run_id:
        raise ValueError("execution_context run_id does not match executor run_id")
    if context_sources is None:
        context_sources = {}
    if not isinstance(context_sources, dict):
        raise TypeError("context_sources must be a mapping")

    if not modes:
        modes, _preset_id, payload = resolve_modes(payload)

    payload_exec = dict(payload)
    if execution_context is not None:
        payload_exec["project_id"] = execution_context.project_id
        payload_exec["domain_book_id"] = execution_context.book_id
        payload_exec["series_id"] = execution_context.series_id
        payload_exec["step_id"] = execution_context.step_id
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

    style_session: AdaptiveStyleSession | None = None
    if execution_context is not None:
        style_session = prepare_adaptive_style_session(
            ProjectRepository(
                StorageResolver().resolve_project(
                    execution_context.project_id,
                    book_id=execution_context.book_id,
                )
            ),
            payload_exec,
            technical_retry=execution_context.technical_retry,
            operation_id=execution_context.operation_id,
            require_style=any(
                mode in {"WRITE", "EDIT", "REWRITE"}
                for mode in modes_exec
            ),
        )

    run_dir = _run_dir(run_id)
    steps_dir = _run_steps_dir(run_id)
    steps_dir.mkdir(parents=True, exist_ok=True)

    state_path = run_dir / "state.json"
    state: Dict[str, Any] = {
        "run_id": run_id,
        "latest_text": "",
        "last_step": 0,
        "created_at": _iso(),
    }
    if execution_context is not None:
        state.update({
            "project_id": execution_context.project_id,
            "book_id": execution_context.book_id,
            "series_id": execution_context.series_id,
            "step_id": execution_context.step_id,
        })
    latest_text = ""
    latest_text_artifact_id = ""
    artifact_paths: List[str] = []

    queue: List[StepItem]
    if isinstance(steps, list) and steps:
        queue = list(steps)
    else:
        preset_steps = _preset_steps(preset_id) if preset_id and not explicit_modes_arg else None
        queue = list(preset_steps) if preset_steps else [{"mode": mode_id} for mode_id in modes_exec]

    initial_queue = list(queue)
    initial_modes = _queue_modes(initial_queue)
    if execution_context is not None:
        _bind_execution_input(execution_context, payload_exec, initial_queue, preset_doc)
    _write_sequence_artifact(
        steps_dir=steps_dir,
        run_id=run_id,
        book_id=book_id,
        preset_id=preset_id or None,
        queue_initial=initial_queue,
        modes=initial_modes,
        execution_context=execution_context,
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
        if execution_context is not None:
            tool_input["project_id"] = execution_context.project_id
            tool_input["domain_book_id"] = execution_context.book_id
            tool_input["series_id"] = execution_context.series_id

        requested_policy = (
            step_overrides.get("policy")
            or runtime_overrides.get("policy")
            or tool_input.get("requested_policy")
            or team.get("policy_id")
        )
        model_routing = {}
        requested_model, effective_model = _models_for_step(
            step_overrides=step_overrides,
            runtime_overrides=runtime_overrides,
            tool_input=tool_input,
            team=team,
            audit=model_routing,
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
            tool_input["text"] = latest_text if latest_text else str(
                tool_input.get("text") or tool_input.get("input") or tool_input.get("content") or "")
        if style_session is not None and mode_id in {"WRITE", "EDIT", "REWRITE"}:
            # The writer receives only abstract, bounded features. Library source
            # references and author-like instructions never cross this boundary.
            tool_input["style_features"] = style_session.writer_features()

        step_execution_context = None
        context_package = None
        if execution_context is not None:
            step_execution_context = execution_context.for_step(step_index, mode_id)
            tool_input["step_id"] = step_execution_context.step_id
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
            route_repo = ProjectRepository(StorageResolver().resolve_project(
                execution_context.project_id, book_id=execution_context.book_id))
            route_key = "model_route.v1:" + context_package.context_package_id
            pinned_route = route_repo.get_metadata(route_key)
            if pinned_route is None:
                route_repo.set_metadata(route_key, json.dumps(model_routing, sort_keys=True))
            else:
                model_routing = json.loads(pinned_route)

        prior_step = (_completed_step(route_repo, step_execution_context, tool_input)
                      if step_execution_context is not None else None)
        evaluation_binding = None
        evaluation_start = None
        evaluation_repository = None
        evaluated_text = ""
        evaluated_artifact_id = ""
        evaluated_artifact_hash = ""
        if (
            mode_id == "QUALITY"
            and step_execution_context is not None
            and context_package is not None
        ):
            evaluated_text = str(tool_input.get("text") or "")
            evaluated_artifact_id = (
                latest_text_artifact_id
                or f"{run_id}:input:{step_index:03d}:QUALITY"
            )
            evaluated_artifact_hash = hashlib.sha256(
                evaluated_text.encode("utf-8")
            ).hexdigest()
            (
                evaluator_id,
                evaluator_version,
                criteria,
                configuration,
                additional_input_hashes,
            ) = _quality_contract(tool_input, style_session)
            evaluation_binding = EvaluationBinding.local_deterministic(
                project_id=step_execution_context.project_id,
                book_id=step_execution_context.book_id,
                series_id=step_execution_context.series_id,
                run_id=step_execution_context.run_id,
                step_id=step_execution_context.step_id,
                operation_id=step_execution_context.operation_id,
                artifact_id=evaluated_artifact_id,
                artifact_version=evaluated_artifact_id,
                artifact_hash=evaluated_artifact_hash,
                criteria_version=evaluator_version,
                criteria=criteria,
                context_package_id=context_package.context_package_id,
                context_hash=context_package.context_hash,
                evaluator_id=evaluator_id,
                evaluator_version=evaluator_version,
                configuration=configuration,
                additional_input_hashes=additional_input_hashes,
                reevaluation_of=(
                    str(payload_exec.get("reevaluation_of") or "").strip() or None
                ),
            )
            evaluation_repository = route_repo
            evaluation_start = start_evaluation(
                evaluation_repository,
                evaluation_binding,
                claim_recovery=step_execution_context.technical_retry,
            )

        if evaluation_start is not None and evaluation_start.reused:
            result = dict(evaluation_start.output or {})
        elif (
            prior_step is not None
            and mode_id in {"WRITE", "EDIT", "REWRITE"}
            and "QUALITY" in initial_modes
        ):
            result = copy.deepcopy(prior_step["result"])
        elif mode_id not in TOOLS:
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

        style_trace: Dict[str, Any] = {}
        if mode_id in {"WRITE", "EDIT", "REWRITE"} and latest_text:
            latest_text_artifact_id = f"{run_id}:{step_index:03d}:{mode_id}"
        if (
            style_session is not None
            and mode_id in {"WRITE", "EDIT", "REWRITE"}
            and latest_text
        ):
            latest_text_artifact_id = f"{run_id}:{step_index:03d}:{mode_id}"
            result_meta = result_payload.get("meta") if isinstance(result_payload, dict) else None
            achieved_genome = (
                result_meta.get("style_achieved_genome")
                if isinstance(result_meta, dict)
                and isinstance(result_meta.get("style_achieved_genome"), dict)
                else None
            )
            style_evaluation = style_session.evaluate(
                artifact_id=latest_text_artifact_id,
                text=latest_text,
                achieved_genome=achieved_genome,
            )
            style_trace = {
                "recipe": style_session.recipe.to_dict(),
                "evaluation": style_evaluation.to_dict(),
            }

        durable_evaluation = None
        if mode_id == "QUALITY" and evaluation_start is not None:
            quality_decision = _quality_decision(result)
            evaluation = (
                style_session.latest_evaluation if style_session is not None else None
            )
            quality_evaluation_id = evaluation_start.evaluation_id
            if isinstance(result_payload, dict):
                quality_meta = result_payload.setdefault("meta", {})
                if isinstance(quality_meta, dict):
                    quality_meta["artifact_id"] = evaluated_artifact_id
                    quality_meta["artifact_hash"] = evaluated_artifact_hash
                    quality_meta["quality_evaluation_id"] = quality_evaluation_id
            if (
                quality_decision == "ACCEPT"
                and evaluation is not None
                and evaluation.style_status != "COMPLIANT"
                and isinstance(result_payload, dict)
            ):
                result_payload["DECISION"] = "REVISE"
                reason_field = (
                    "REASONS" if isinstance(result_payload.get("REASONS"), list)
                    else "REJECT_REASONS"
                )
                reasons = result_payload.setdefault(reason_field, [])
                if isinstance(reasons, list) and "STYLE_NOT_COMPLIANT" not in reasons:
                    reasons.append("STYLE_NOT_COMPLIANT")
                quality_decision = "REVISE"
            reasons, must_fix = _quality_reasons(result)
            if evaluation_start.reused:
                durable_evaluation = evaluation_start.record
            else:
                if evaluation_start.attempt_token is None or evaluation_binding is None:
                    raise RuntimeError("evaluation attempt token is missing")
                durable_evaluation = finalize_evaluation(
                    evaluation_repository,
                    evaluation_binding,
                    attempt_token=evaluation_start.attempt_token,
                    decision=quality_decision,
                    reasons=reasons,
                    must_fix=must_fix,
                    output=result,
                )
            if isinstance(result_payload, dict) and durable_evaluation is not None:
                quality_meta = result_payload.setdefault("meta", {})
                if isinstance(quality_meta, dict):
                    quality_meta["evaluation_record_hash"] = durable_evaluation.record_hash
                    quality_meta["evaluation_reused"] = evaluation_start.reused
            quality_score_raw = result_payload.get("SCORE", 0) if isinstance(result_payload, dict) else 0
            try:
                quality_score = float(quality_score_raw)
            except (TypeError, ValueError):
                quality_score = 0.0
            quality_score = min(1.0, max(0.0, quality_score))
            if style_session is not None:
                performance = style_session.finalize(
                    quality_decision=quality_decision,
                    quality_score=quality_score,
                    quality_evaluation_id=quality_evaluation_id,
                    quality_artifact_hash=evaluated_artifact_hash,
                    artifact_id=evaluated_artifact_id,
                    artifact_text=evaluated_text,
                )
                style_trace = {
                    "recipe": style_session.recipe.to_dict(),
                    "evaluation": (
                        evaluation.to_dict() if evaluation is not None else None
                    ),
                    "quality_decision": quality_decision,
                    "performance_record": (
                        performance.to_dict() if performance is not None else None
                    ),
                    "performance_reused_from_evaluation": (
                        performance.quality_evaluation_id
                        if performance is not None
                        and performance.quality_evaluation_id != quality_evaluation_id
                        else None
                    ),
                }

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
            "model_routing": model_routing,
            "input": tool_input,
            "result": result,
            "created_at": _iso(),
        }
        if style_trace:
            step_doc["adaptive_style"] = style_trace
        if durable_evaluation is not None:
            step_doc["evaluation_record"] = durable_evaluation.to_dict()
        bible_binding = payload_exec.get("_book_bible")
        if isinstance(bible_binding, dict) and bible_binding:
            step_doc["book_bible"] = dict(bible_binding)
            step_doc["book_bible_path"] = bible_binding["path"]
            step_doc["book_bible_sha256"] = bible_binding["sha256"]
            result["book_bible"] = dict(bible_binding)
            if isinstance(result.get("payload"), dict):
                result["payload"]["book_bible"] = dict(bible_binding)
        if step_execution_context is not None and context_package is not None:
            from app.p20_core.model_provenance import public_trace
            step_doc["model_provenance"] = [
                trace for trace in public_trace(route_repo, run_id=run_id)
                if trace["step_id"] == step_execution_context.step_id
                or trace["step_id"].startswith(step_execution_context.step_id + ":")]
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

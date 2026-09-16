from __future__ import annotations

import hashlib

import inspect
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import HTTPException

from app.config_registry import load_presets
from app.p20_core.executor import execute_p20 as execute_stub
from app.p20_core.canon_service import (
    APP_VERSION,
    REPO_ROOT,
    _path_for_read,
    _public_path,
    canon_blocks,
    coerce_text,
    commit_chapter_to_canon,
    ensure_book_dirs,
    ensure_run_dirs,
    json_write,
    load_canon_snapshot,
    load_run_state,
    read_artifact_text,
    resolve_resume_run_id,
    run_canon_check,
    save_run_state,
    update_latest_run_marker,
    write_audit,
)
from app.p20_core.contracts import AgentStepRequest
from app.p20_core.context_runtime import (
    ProjectExecutionContext,
    ProjectExecutionIdentityError,
    resolve_runtime_project_id,
    resolve_runtime_series_id,
)
from app.p20_core.book_bible_contract import load_book_bible_or_raise
from app.p20_core.master_canon import resolve_master_canon
from app.p20_core.project_truth import build_project_truth_binding, assert_resume_project_truth_consistency
from app.p20_core.lock_service import (
    acquire_book_lock,
    acquire_run_lock,
    get_book_lock,
    get_run_lock,
    release_book_lock,
    release_run_lock,
)
from app.p20_core.storage_paths import get_runs_root
from app.p20_core.chapter_lineage import persist_chapter_lineage
from app.p20_core.project_repository import ProjectRepository, StorageResolver

MODES_FILE = Path(__file__).resolve().parents[1] / "modes.json"


MASTER_CANON_CONTRACT_VERSION = "1.0"

MASTER_CANON_VERSIONING_POLICY: tuple[str, ...] = (
    "Bump MASTER_CANON_CONTRACT_VERSION whenever MASTER_CANON_CONTRACT_FIELDS changes.",
    "Refresh MASTER_CANON_CONTRACT_GUARD_FROZEN_VERSION together with the new version.",
    "Refresh MASTER_CANON_CONTRACT_GUARD_FROZEN_REQUIRED_FINGERPRINT after intentional contract-field changes only.",
    "A change to master_canon binding fields without version bump and guard refresh is a contract violation.",
)

MASTER_CANON_CONTRACT_FIELDS: tuple[str, ...] = (
    "contract",
    "path",
    "sha256",
    "contract_version",
)

MASTER_CANON_CONTRACT_GUARD_FROZEN_VERSION = "1.0"
MASTER_CANON_CONTRACT_GUARD_FROZEN_REQUIRED_FINGERPRINT = "e0802a2dd5b9383d02cc6cd5649396559b98b5ea37effefd21ef80aa48d0683f"


def build_master_canon_contract_field_payload() -> dict[str, list[str]]:
    return {
        "contract_fields": list(MASTER_CANON_CONTRACT_FIELDS),
    }


def compute_master_canon_contract_field_fingerprint() -> str:
    raw = json.dumps(
        build_master_canon_contract_field_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
def _run_id() -> str:
    return f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


def _json_load(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def load_mode_ids() -> List[str]:
    data = _json_load(MODES_FILE, {})
    modes = data.get("modes") if isinstance(data, dict) else None
    out: List[str] = []
    if isinstance(modes, list):
        for item in modes:
            if isinstance(item, dict):
                mid = str(item.get("id") or "").upper().strip()
            else:
                mid = str(item).upper().strip()
            if mid:
                out.append(mid)
    if not out:
        out = ["PLAN", "OUTLINE", "WRITE", "CRITIC", "EDIT", "REWRITE", "QUALITY", "CANON_CHECK"]
    return out


def health_payload() -> Dict[str, Any]:
    return {
        "ok": True,
        "engine": APP_VERSION,
        "runtime": "P20.x",
        "mode": "NOVEL",
    }


def config_validate_payload() -> Dict[str, Any]:
    mode_ids = load_mode_ids()
    presets = load_presets()
    preset_ids = list(presets.get("preset_ids") or [])
    presets_count = int(presets.get("presets_count") or len(preset_ids))
    return {
        "ok": True,
        "mode_ids": mode_ids,
        "modes_count": len(mode_ids),
        "preset_ids": preset_ids,
        "presets_count": presets_count,
        "presets_source": "config_registry",
        "bad_presets": [],
        "missing_tools": {},
        "data": {
            "mode_ids": mode_ids,
            "modes_count": len(mode_ids),
            "preset_ids": preset_ids,
            "presets_count": presets_count,
            "presets_source": "config_registry",
        },
    }


def build_request_payload(req: AgentStepRequest) -> Dict[str, Any]:
    payload = dict(req.payload or {})

    if req.book_id and not payload.get("book_id"):
        payload["book_id"] = req.book_id
    if req.project_id and not payload.get("project_id"):
        payload["project_id"] = req.project_id
    if req.series_id and not payload.get("series_id"):
        payload["series_id"] = req.series_id
    if req.run_id and not payload.get("run_id"):
        payload["run_id"] = req.run_id
    if req.step_id and not payload.get("step_id"):
        payload["step_id"] = req.step_id
    if req.technical_retry is not None and "technical_retry" not in payload:
        payload["technical_retry"] = req.technical_retry
    if req.text and not payload.get("text"):
        payload["text"] = req.text
    if req.content and not payload.get("content"):
        payload["content"] = req.content
    if req.input and not payload.get("input"):
        payload["input"] = req.input
    if req.topic and not payload.get("topic"):
        payload["topic"] = req.topic
    if req.steps is not None and "steps" not in payload:
        payload["steps"] = req.steps
    if req.resume is not None and "resume" not in payload:
        payload["resume"] = req.resume
    if req.preset and not payload.get("preset"):
        payload["preset"] = req.preset
    if req.mode and not payload.get("mode"):
        payload["mode"] = req.mode
    if req.modes and not payload.get("modes"):
        payload["modes"] = req.modes

    return payload


def _request_has_explicit_modes(req: AgentStepRequest, payload: Dict[str, Any]) -> bool:
    if isinstance(payload.get("modes"), list) and payload["modes"]:
        return True
    if req.modes:
        return True
    if payload.get("mode") or req.mode:
        return True
    return False


def _preset_doc(preset_id: str) -> Dict[str, Any]:
    preset_key = str(preset_id or "").upper().strip()
    presets_data = load_presets()
    presets = presets_data.get("presets") if isinstance(presets_data, dict) else presets_data
    if isinstance(presets, list):
        for item in presets:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("preset_id") or item.get("name") or "").upper().strip()
            if item_id == preset_key:
                return item
    raise ValueError(f"Unknown preset: {preset_key}")


def _preset_modes(preset_id: str) -> List[str]:
    doc = _preset_doc(preset_id)
    modes = doc.get("modes")
    if isinstance(modes, list) and modes:
        return [str(x).upper().strip() for x in modes if str(x).strip()]

    steps = doc.get("steps")
    if isinstance(steps, list):
        step_modes = [
            str(item.get("mode")).upper().strip()
            for item in steps
            if isinstance(item, dict) and item.get("mode")
        ]
        if step_modes:
            return step_modes

    return ["WRITE"]


def resolve_modes(req: AgentStepRequest, payload: Dict[str, Any]) -> List[str]:
    if isinstance(payload.get("modes"), list) and payload["modes"]:
        return [str(x).upper() for x in payload["modes"]]

    if req.modes:
        return [str(x).upper() for x in req.modes]

    mode = payload.get("mode") or req.mode
    if mode:
        return [str(mode).upper()]

    preset = str(payload.get("preset") or req.preset or "").upper()
    if preset:
        return _preset_modes(preset)

    return ["WRITE"]


def extract_input_text(payload: Dict[str, Any], req: AgentStepRequest) -> str:
    for value in (
        payload.get("text"),
        payload.get("content"),
        payload.get("input"),
        payload.get("topic"),
        req.text,
        req.content,
        req.input,
        req.topic,
    ):
        txt = coerce_text(value).strip()
        if txt:
            return txt
    return ""


def normalize_artifact_paths(result: Any) -> List[str]:
    if isinstance(result, list):
        return [str(x) for x in result if x is not None]

    if isinstance(result, str):
        return [result]

    if not isinstance(result, dict):
        return []

    raw = result.get("artifact_paths")
    if isinstance(raw, list):
        return [str(x) for x in raw if x is not None]

    raw = result.get("artifacts")
    if isinstance(raw, list):
        return [str(x) for x in raw if x is not None]

    raw = result.get("artifact_path")
    if isinstance(raw, str) and raw.strip():
        return [raw]

    return []


def normalize_public_artifact_paths(paths: List[str]) -> List[str]:
    if not os.environ.get("AGENTPRO_STORAGE_ROOT"):
        return paths

    out: List[str] = []
    for path_value in paths:
        path = Path(path_value)
        if path.is_absolute():
            out.append(_public_path(path))
        else:
            out.append(str(path_value))
    return out


def _context_traces(artifact_paths: List[str]) -> List[Dict[str, Any]]:
    traces: List[Dict[str, Any]] = []
    for artifact_path in artifact_paths:
        doc = _json_load(_path_for_read(Path(artifact_path)), {})
        context_package_id = str(doc.get("context_package_id") or "").strip()
        context_hash = str(doc.get("context_hash") or "").strip()
        if not context_package_id or not context_hash:
            continue
        traces.append({
            "artifact_path": artifact_path,
            "project_id": doc.get("project_id"),
            "book_id": doc.get("book_id"),
            "series_id": doc.get("series_id"),
            "step_id": doc.get("step_id"),
            "mode": doc.get("mode"),
            "role": doc.get("role"),
            "context_package_id": context_package_id,
            "context_hash": context_hash,
            "requested_model": doc.get("requested_model"),
            "effective_model": doc.get("effective_model"),
            "model_routing": doc.get("model_routing"),
        })
    return traces


def _adaptive_style_trace(artifact_paths: List[str]) -> Dict[str, Any] | None:
    trace = None
    for artifact_path in artifact_paths:
        doc = _json_load(_path_for_read(Path(artifact_path)), {})
        candidate = doc.get("adaptive_style")
        if isinstance(candidate, dict):
            trace = dict(candidate)
            trace["artifact_path"] = artifact_path
            trace["step_id"] = doc.get("step_id")
            trace["mode"] = doc.get("mode")
    return trace


def _attach_execution_trace(
    doc: Dict[str, Any],
    execution_context: ProjectExecutionContext,
    traces: List[Dict[str, Any]],
) -> Dict[str, Any]:
    doc["project_id"] = execution_context.project_id
    doc["domain_book_id"] = execution_context.book_id
    doc["series_id"] = execution_context.series_id
    doc["step_id"] = execution_context.step_id
    doc["context_packages"] = list(traces)
    if traces:
        doc["context_package_id"] = traces[-1]["context_package_id"]
        doc["context_hash"] = traces[-1]["context_hash"]
    return doc


def _assert_run_execution_identity(
    run_id: str,
    execution_context: ProjectExecutionContext,
    *,
    storage_book_id: str,
) -> None:
    run_dir = get_runs_root() / run_id
    state = _json_load(run_dir / "run_state.json", {})
    if not isinstance(state, dict) or not state:
        state = _json_load(run_dir / "state.json", {})
    if not isinstance(state, dict) or not state:
        return

    stored_project_id = str(state.get("project_id") or "").strip()
    if stored_project_id and stored_project_id != execution_context.project_id:
        raise ProjectExecutionIdentityError(
            "run_id belongs to a different project_id"
        )

    stored_book_id = str(
        state.get("domain_book_id") or state.get("book_id") or ""
    ).strip()
    if stored_book_id and stored_book_id not in {
        execution_context.book_id,
        storage_book_id,
    }:
        raise ProjectExecutionIdentityError("run_id belongs to a different book_id")

    stored_series_id = state.get("series_id")
    if stored_series_id is not None and str(stored_series_id) != str(
        execution_context.series_id
    ):
        raise ProjectExecutionIdentityError("run_id belongs to a different series_id")
    if stored_series_id is None and execution_context.series_id is not None and stored_project_id:
        raise ProjectExecutionIdentityError("run_id is not bound to requested series_id")


async def run_agent_step(req: AgentStepRequest) -> Dict[str, Any]:
    payload = build_request_payload(req)

    book_id = str(payload.get("book_id") or "book_runtime_test")
    project_id, domain_book_id = resolve_runtime_project_id(
        payload.get("project_id"),
        book_id=book_id,
    )
    series_id = resolve_runtime_series_id(
        payload.get("series_id"),
        project_id=project_id,
        book_id=domain_book_id,
    )
    resume = bool(payload.get("resume"))
    run_id = resolve_resume_run_id(
        book_id,
        payload.get("run_id"),
        resume,
        project_id=project_id,
        domain_book_id=domain_book_id,
    )
    payload["run_id"] = run_id
    technical_retry = bool(payload.get("technical_retry"))
    requested_step_id = str(payload.get("step_id") or "").strip()
    if technical_retry and not requested_step_id:
        raise ValueError("technical_retry requires step_id")
    execution_context = ProjectExecutionContext.create(
        project_id=project_id,
        book_id=domain_book_id,
        series_id=series_id,
        run_id=run_id,
        step_id=requested_step_id or f"step-{uuid4().hex}",
        technical_retry=technical_retry,
    )
    payload["project_id"] = execution_context.project_id
    payload["domain_book_id"] = execution_context.book_id
    payload["series_id"] = execution_context.series_id
    payload["step_id"] = execution_context.step_id
    _assert_run_execution_identity(
        run_id,
        execution_context,
        storage_book_id=book_id,
    )

    modes = resolve_modes(req, payload)
    preset_id = str(payload.get("preset") or req.preset or "").upper().strip()
    preset_doc = _preset_doc(preset_id) if preset_id else {}
    preset_only_execution = bool(preset_id) and not _request_has_explicit_modes(req, payload)
    scene_ref = str(payload.get("scene_ref") or payload.get("scene") or "").strip() or None
    is_write = "WRITE" in modes
    book_bible: Dict[str, Any] = {}
    book_bible_binding: Dict[str, Any] = {}

    if is_write:
        book_bible = load_book_bible_or_raise(book_id)
        book_bible_contract = book_bible["_contract"]
        book_bible_binding = {
            "path": book_bible_contract["path"],
            "sha256": book_bible_contract["sha256"],
            "contract_version": book_bible_contract["contract_version"],
        }
        payload["_book_bible"] = dict(book_bible_binding)

    book_dir = ensure_book_dirs(
        book_id,
        project_id=execution_context.project_id,
        domain_book_id=execution_context.book_id,
    )
    if not is_write:
        loaded_book_bible = _json_load(book_dir / "book_bible.json", {})
        if isinstance(loaded_book_bible, dict):
            book_bible = loaded_book_bible

    book_lock_acquired = False
    run_lock_acquired = False

    try:
        try:
            acquire_book_lock(book_id, run_id)
            book_lock_acquired = True
        except RuntimeError:
            existing = get_book_lock(book_id) or {
                "book_id": book_id,
                "run_id": run_id,
                "status": "locked",
                "engine": APP_VERSION,
            }
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "BOOK_LOCKED",
                    "book_id": book_id,
                    "run_id": run_id,
                    "lock": existing,
                },
            )

        try:
            acquire_run_lock(run_id, book_id)
            run_lock_acquired = True
        except RuntimeError:
            existing = get_run_lock(run_id) or {
                "run_id": run_id,
                "book_id": book_id,
                "status": "locked",
                "engine": APP_VERSION,
            }
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "RUN_LOCKED",
                    "run_id": run_id,
                    "book_id": book_id,
                    "lock": existing,
                },
            )

        canon_snapshot, canon_snapshot_path = load_canon_snapshot(
            book_id,
            project_id=execution_context.project_id,
            domain_book_id=execution_context.book_id,
        )
        context_sources: Dict[str, Any] = {
            "canon": canon_snapshot,
            "canon_snapshot_path": _public_path(canon_snapshot_path),
            "book_bible": book_bible,
            "book_bible_path": (
                book_bible_binding.get("path")
                or _public_path(book_dir / "book_bible.json")
            ),
            "book_bible_version": (
                book_bible_binding.get("contract_version") or "unvalidated"
            ),
            "style_version": payload.get("style_version"),
            "memory_snapshot_id": payload.get("memory_snapshot_id"),
        }

        _mc = resolve_master_canon()
        project_truth_ref = build_project_truth_binding()
        project_truth_binding = project_truth_ref
        master_canon_ref = {
            "scope": "MASTER_CANON_AGENTPRO",
            "path": Path(str(project_truth_ref.get("path") or "MASTER_CANON_AGENTPRO.md")).name,
            "sha256": str(project_truth_ref.get("sha256") or ""),
        }
        if isinstance(canon_snapshot, dict):
            if canon_snapshot.get("project_truth") != project_truth_ref:
                canon_snapshot["project_truth"] = dict(project_truth_ref)
                canon_snapshot_path.write_text(
                    json.dumps(canon_snapshot, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

        if resume:
            _resume_run_state = locals().get("run_state")
            if _resume_run_state is None:
                _resume_run_state = load_run_state(run_id)
            if (
                isinstance(_resume_run_state, dict)
                and _resume_run_state
                and str(_resume_run_state.get("book_id") or "") == book_id
                and isinstance(_resume_run_state.get("project_truth"), dict)
            ):
                assert_resume_project_truth_consistency(project_truth_binding, _resume_run_state)
        input_text = extract_input_text(payload, req)
        pre_report = run_canon_check(input_text, canon_snapshot, scene_ref)

        if canon_blocks(pre_report):
            state = save_run_state(
                run_id,
                book_id=book_id,
                status="REJECT",
                last_modes=modes,
                last_artifact_paths=[],
                chapter_path=None,
                project_id=execution_context.project_id,
                domain_book_id=execution_context.book_id,
                series_id=execution_context.series_id,
                step_id=execution_context.step_id,
            )
            state["decision"] = "REJECT"
            state["master_canon"] = dict(master_canon_ref)
            state["project_truth"] = dict(project_truth_ref)
            _attach_execution_trace(state, execution_context, [])
            json_write(ensure_run_dirs(run_id) / "run_state.json", state)
            update_latest_run_marker(
                book_id,
                run_id,
                project_id=execution_context.project_id,
                domain_book_id=execution_context.book_id,
            )
            write_audit(
                book_id=book_id,
                run_id=run_id,
                modes=modes,
                artifact_paths=[],
                decision="REJECT",
                chapter_path=None,
                canon_snapshot_path=canon_snapshot_path,
                master_canon=master_canon_ref,
                project_truth=project_truth_ref,
                project_id=execution_context.project_id,
                domain_book_id=execution_context.book_id,
                series_id=execution_context.series_id,
                step_id=execution_context.step_id,
                context_packages=[],
            )
            return {
                "ok": False,
                "status": "error",
                "run_id": run_id,
                "book_id": book_id,
                "domain_book_id": execution_context.book_id,
                "project_id": execution_context.project_id,
                "series_id": execution_context.series_id,
                "step_id": execution_context.step_id,
                "mode_ids": modes,
                "artifact_paths": [],
                "artifacts": [],
                "quality_gate": {
                    "decision": "REJECT",
                    "reasons": ["pre_write_canon_check_failed"],
                },
                "pre_canon_check": pre_report,
                "post_canon_check": {"status": "skipped", "reason": "pre_write_failed"},
                "canon_snapshot_path": _public_path(canon_snapshot_path),
                "master_canon": master_canon_ref,
                "project_truth": project_truth_ref,
                "run_state": state,
                "context_packages": [],
            }

        stub_modes = None if preset_only_execution else modes
        stub_out = execute_stub(
            run_id=run_id,
            book_id=book_id,
            modes=stub_modes,
            payload=payload,
            steps=payload.get("steps"),
            execution_context=execution_context,
            context_sources=context_sources,
        )
        if inspect.isawaitable(stub_out):
            stub_out = await stub_out

        artifact_paths = normalize_public_artifact_paths(normalize_artifact_paths(stub_out))
        context_traces = _context_traces(artifact_paths)
        adaptive_style_trace = _adaptive_style_trace(artifact_paths)
        research_results = []
        for artifact_path in artifact_paths:
            document = _json_load(_path_for_read(Path(artifact_path)), {})
            if document.get("mode") == "FACTCHECK":
                research_results.append(document.get("result", {}).get("payload", {}))
        research_failed = any(r.get("execution_status") in {"FAILED", "NOT_PERFORMED"}
                              for r in research_results)

        if is_write:
            for _artifact_path in artifact_paths:
                try:
                    _p = _path_for_read(Path(_artifact_path))
                    if _p.exists():
                        _doc = json.loads(_p.read_text(encoding="utf-8"))
                        _doc["book_bible"] = dict(book_bible_binding)
                        _doc["book_bible_path"] = book_bible_binding["path"]
                        _doc["book_bible_sha256"] = book_bible_binding["sha256"]

                        if isinstance(_doc.get("result"), dict):
                            _doc["result"]["book_bible"] = dict(book_bible_binding)
                            if isinstance(_doc["result"].get("payload"), dict):
                                _doc["result"]["payload"]["book_bible"] = dict(book_bible_binding)

                        _p.write_text(json.dumps(_doc, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
        output_text = ""
        for artifact_path in reversed(artifact_paths):
            output_text = read_artifact_text(artifact_path)
            if output_text:
                break
        if not output_text:
            output_text = input_text

        post_report = run_canon_check(output_text, canon_snapshot, scene_ref)
        decision = "REJECT" if canon_blocks(post_report) else "ACCEPT"
        quality_decision = ""
        quality_reasons: List[str] = []

        # QUALITY override (KANON)
        for _ap in artifact_paths:
            if str(_ap).upper().endswith("_QUALITY.JSON"):
                try:
                    _doc = _json_load(_path_for_read(Path(_ap)), {})
                    _payload = _doc.get("result", {}).get("payload", {})
                    _qd = str(_payload.get("DECISION") or "").upper()
                    if _qd:
                        quality_decision = _qd
                        decision = _qd
                    _raw_reasons = _payload.get("REASONS")
                    if _raw_reasons is None:
                        _raw_reasons = _payload.get("REJECT_REASONS")
                    if isinstance(_raw_reasons, list):
                        quality_reasons = [str(x) for x in _raw_reasons]
                except Exception:
                    pass
                break

        chapter_path = None
        chapter_lineage = None
        canon_memory = None
        canonical_change = None

        if decision == "ACCEPT" and "WRITE" in modes and not research_failed:
            chapter_commit = persist_chapter_lineage(
                repository=ProjectRepository(
                    StorageResolver().resolve_project(
                        execution_context.project_id,
                        book_id=execution_context.book_id,
                    )
                ),
                execution_context=execution_context,
                storage_book_id=book_id,
                artifact_paths=artifact_paths,
                text=output_text,
                quality_decision=quality_decision or None,
                canon_snapshot_path=_public_path(canon_snapshot_path),
                pre_report=pre_report,
                post_report=post_report,
                master_canon=master_canon_ref,
                project_truth=project_truth_ref,
                book_bible_binding=book_bible_binding,
                engine=APP_VERSION,
            )
            chapter_path = chapter_commit.chapter_path
            chapter_lineage = chapter_commit.summary()
            if chapter_path:
                chapter_full = _path_for_read(Path(chapter_path))
                chapter_doc = chapter_commit.document
                canon_memory = commit_chapter_to_canon(
                    book_id=book_id,
                    run_id=run_id,
                    chapter_path=chapter_path,
                    chapter_id=str(chapter_doc.get("chapter_id") or chapter_full.stem),
                    chapter_sha256=str(chapter_doc.get("sha256") or ""),
                    project_id=execution_context.project_id,
                    domain_book_id=execution_context.book_id,
                )
                canon_snapshot, canon_snapshot_path = load_canon_snapshot(
                    book_id,
                    project_id=execution_context.project_id,
                    domain_book_id=execution_context.book_id,
                )
                if isinstance(canon_snapshot, dict):
                    if canon_snapshot.get("project_truth") != project_truth_ref:
                        canon_snapshot["project_truth"] = dict(project_truth_ref)
                        canon_snapshot_path.write_text(
                            json.dumps(canon_snapshot, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                if isinstance(chapter_doc, dict):
                    write_trace = next(
                        (
                            trace
                            for trace in reversed(context_traces)
                            if trace.get("mode") == "WRITE"
                        ),
                        context_traces[-1] if context_traces else None,
                    )
                    if write_trace is not None:
                        from app.p20_core.canon_service import process_accepted_artifact
                        canonical_change = process_accepted_artifact(
                            execution_context=execution_context, text=output_text,
                            source_trace=write_trace, context_sources=context_sources,
                            requested_model=write_trace.get("requested_model"),
                            effective_model=write_trace["effective_model"],
                            scope_type=payload.get("scope_type", "PROJECT"))

        canonical_failed = (
            isinstance(canonical_change, dict)
            and canonical_change.get("status") == "FAILED"
        )
        execution_decision = "FAILED" if canonical_failed or research_failed else decision

        state = save_run_state(
            run_id,
            book_id=book_id,
            status=execution_decision,
            last_modes=modes,
            last_artifact_paths=artifact_paths,
            chapter_path=chapter_path,
            project_id=execution_context.project_id,
            domain_book_id=execution_context.book_id,
            series_id=execution_context.series_id,
            step_id=execution_context.step_id,
        )
        state["decision"] = execution_decision
        state["canonical_change"] = canonical_change
        if research_results:
            state["research"] = research_results
        state["master_canon"] = dict(master_canon_ref)
        state["project_truth"] = dict(project_truth_ref)
        if adaptive_style_trace is not None:
            state["adaptive_style"] = adaptive_style_trace
        if is_write:
            state["book_bible"] = dict(book_bible_binding)
        if chapter_lineage is not None:
            state["chapter_lineage"] = dict(chapter_lineage)
        from app.p20_core.model_provenance import public_trace
        provenance_repository = ProjectRepository(StorageResolver().resolve_project(
            execution_context.project_id, book_id=execution_context.book_id))
        state["model_provenance"] = public_trace(provenance_repository, run_id=run_id)
        _attach_execution_trace(state, execution_context, context_traces)
        json_write(ensure_run_dirs(run_id) / "run_state.json", state)
        update_latest_run_marker(
            book_id,
            run_id,
            project_id=execution_context.project_id,
            domain_book_id=execution_context.book_id,
        )

        write_audit(
            book_id=book_id,
            run_id=run_id,
            modes=modes,
            artifact_paths=artifact_paths,
            decision=execution_decision,
            chapter_path=chapter_path,
            canon_snapshot_path=canon_snapshot_path,
            master_canon=master_canon_ref,
            project_truth=project_truth_ref,
            project_id=execution_context.project_id,
            domain_book_id=execution_context.book_id,
            series_id=execution_context.series_id,
            step_id=execution_context.step_id,
            context_packages=context_traces,
            chapter_lineage=chapter_lineage,
            adaptive_style=adaptive_style_trace,
        )

        execution_ok = (decision == "ACCEPT" or bool(quality_decision)) and not (canonical_failed or research_failed)
        if quality_decision and decision != "ACCEPT":
            quality_gate_reasons = quality_reasons
        else:
            quality_gate_reasons = [] if decision == "ACCEPT" else ["post_write_canon_check_failed"]

        stop_fields: Dict[str, Any] = {}
        if (
            quality_decision
            and quality_decision != "ACCEPT"
            and isinstance(preset_doc, dict)
            and bool(preset_doc.get("stop_on_quality_non_accept"))
        ):
            stop_fields = {
                "stopped": True,
                "stop_reason": "QUALITY_NON_ACCEPT",
                "stop": {
                    "mode": "QUALITY",
                    "decision": quality_decision,
                    "blocked": True,
                },
            }

        response = {
            "ok": execution_ok,
            "status": "ok" if execution_ok else "error",
            "decision": execution_decision,
            "run_id": run_id,
            "book_id": book_id,
            "domain_book_id": execution_context.book_id,
            "project_id": execution_context.project_id,
            "series_id": execution_context.series_id,
            "step_id": execution_context.step_id,
            "mode_ids": modes,
            "artifact_paths": artifact_paths,
            "artifacts": list(artifact_paths),
            "artifact_count": len(artifact_paths),
            "quality_gate": {
                "decision": decision,
                "reasons": quality_gate_reasons,
            },
            "pre_canon_check": pre_report,
            "post_canon_check": post_report,
            "canon_snapshot_path": _public_path(canon_snapshot_path),
            "chapter_path": chapter_path,
            "chapter_lineage": chapter_lineage,
            "master_canon": master_canon_ref,
            "project_truth": project_truth_ref,
            "book_bible": dict(book_bible_binding) if is_write else {},
            "run_state": state,
            "canon_memory": canon_memory,
            "canonical_change": canonical_change,
            "context_packages": context_traces,
            "adaptive_style": adaptive_style_trace,
            "context_package_id": (
                context_traces[-1]["context_package_id"] if context_traces else None
            ),
            "context_hash": context_traces[-1]["context_hash"] if context_traces else None,
        }
        response.update(stop_fields)
        if research_results:
            response["research"] = research_results
        return response
    finally:
        if run_lock_acquired:
            release_run_lock(run_id)
        if book_lock_acquired:
            release_book_lock(book_id)





def resolve_master_canon() -> dict[str, Any]:
    candidate_path = REPO_ROOT / "MASTER_CANON_AGENTPRO.md"
    if not candidate_path.exists():
        return {}

    raw = candidate_path.read_text(encoding="utf-8")
    return {
        "contract": "MASTER_CANON_AGENTPRO/v1",
        "path": str(candidate_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "contract_version": MASTER_CANON_CONTRACT_VERSION,
    }

# ---- master_canon versioning hard override v3 ----
def resolve_master_canon() -> dict[str, Any]:
    candidate_path = REPO_ROOT / "MASTER_CANON_AGENTPRO.md"
    if not candidate_path.exists():
        return {
            "contract": "MASTER_CANON_AGENTPRO/v1",
            "path": "MASTER_CANON_AGENTPRO.md",
            "sha256": "",
            "scope": "global",
            "contract_version": MASTER_CANON_CONTRACT_VERSION,
        }

    raw = candidate_path.read_text(encoding="utf-8")
    return {
        "contract": "MASTER_CANON_AGENTPRO/v1",
        "path": str(candidate_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "scope": "global",
        "contract_version": MASTER_CANON_CONTRACT_VERSION,
    }





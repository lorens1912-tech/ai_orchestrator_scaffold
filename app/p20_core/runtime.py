from __future__ import annotations

import hashlib

import inspect
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException

import app.orchestrator_stub as orchestrator_stub
from app.orchestrator_stub import execute_stub
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
    save_chapter,
    save_run_state,
    update_latest_run_marker,
    write_audit,
)
from app.p20_core.contracts import AgentStepRequest
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
from app.p20_core.storage_paths import get_storage_root

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


_EXECUTE_STUB_STORAGE_SCOPE_LOCK = threading.RLock()


@contextmanager
def _execute_stub_storage_scope():
    with _EXECUTE_STUB_STORAGE_SCOPE_LOCK:
        storage_root = get_storage_root()
        previous_cwd = Path.cwd()
        previous_stub_root = getattr(orchestrator_stub, "ROOT", None)

        storage_root.mkdir(parents=True, exist_ok=True)
        try:
            if previous_stub_root is not None:
                orchestrator_stub.ROOT = storage_root
            os.chdir(storage_root)
            yield
        finally:
            os.chdir(previous_cwd)
            if previous_stub_root is not None:
                orchestrator_stub.ROOT = previous_stub_root


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
    return {
        "ok": True,
        "mode_ids": mode_ids,
        "modes_count": len(mode_ids),
        "presets_count": 1,
        "presets_source": "p20_core",
        "bad_presets": [],
        "missing_tools": {},
        "data": {
            "mode_ids": mode_ids,
            "modes_count": len(mode_ids),
            "presets_count": 1,
            "presets_source": "p20_core",
        },
    }


def build_request_payload(req: AgentStepRequest) -> Dict[str, Any]:
    payload = dict(req.payload or {})

    if req.book_id and not payload.get("book_id"):
        payload["book_id"] = req.book_id
    if req.run_id and not payload.get("run_id"):
        payload["run_id"] = req.run_id
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


def resolve_modes(req: AgentStepRequest, payload: Dict[str, Any]) -> List[str]:
    if isinstance(payload.get("modes"), list) and payload["modes"]:
        return [str(x).upper() for x in payload["modes"]]

    if req.modes:
        return [str(x).upper() for x in req.modes]

    mode = payload.get("mode") or req.mode
    if mode:
        return [str(mode).upper()]

    preset = str(payload.get("preset") or req.preset or "").upper()
    if preset in {"DEFAULT", "PIPELINE_DRAFT", "ORCH_STANDARD", "WRITING_STANDARD", "DRAFT_EDIT_QUALITY"}:
        return ["WRITE"]

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


async def run_agent_step(req: AgentStepRequest) -> Dict[str, Any]:
    payload = build_request_payload(req)

    book_id = str(payload.get("book_id") or "book_runtime_test")
    resume = bool(payload.get("resume"))
    run_id = resolve_resume_run_id(book_id, payload.get("run_id"), resume)
    payload["run_id"] = run_id

    modes = resolve_modes(req, payload)
    scene_ref = str(payload.get("scene_ref") or payload.get("scene") or "").strip() or None
    is_write = "WRITE" in modes
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

    ensure_book_dirs(book_id)

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

        canon_snapshot, canon_snapshot_path = load_canon_snapshot(book_id)

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
            )
            state["decision"] = "REJECT"
            state["master_canon"] = dict(master_canon_ref)
            state["project_truth"] = dict(project_truth_ref)
            json_write(ensure_run_dirs(run_id) / "run_state.json", state)
            update_latest_run_marker(book_id, run_id)
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
            )
            return {
                "ok": False,
                "status": "error",
                "run_id": run_id,
                "book_id": book_id,
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
            }

        with _execute_stub_storage_scope():
            stub_out = execute_stub(
                run_id=run_id,
                book_id=book_id,
                modes=modes,
                payload=payload,
                steps=payload.get("steps"),
            )
            if inspect.isawaitable(stub_out):
                stub_out = await stub_out

        artifact_paths = normalize_public_artifact_paths(normalize_artifact_paths(stub_out))

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
        if artifact_paths:
            output_text = read_artifact_text(artifact_paths[-1])
        if not output_text:
            output_text = input_text

        post_report = run_canon_check(output_text, canon_snapshot, scene_ref)
        decision = "REJECT" if canon_blocks(post_report) else "ACCEPT"

        # QUALITY override (KANON)
        for _ap in artifact_paths:
            if str(_ap).upper().endswith("_QUALITY.JSON"):
                try:
                    _doc = _json_load(_path_for_read(Path(_ap)), {})
                    _payload = _doc.get("result", {}).get("payload", {})
                    _qd = str(_payload.get("DECISION") or "").upper()
                    if _qd:
                        decision = _qd
                except Exception:
                    pass
                break

        chapter_path = None
        canon_memory = None

        if decision == "ACCEPT" and "WRITE" in modes:
            chapter_path = save_chapter(
                book_id=book_id,
                run_id=run_id,
                text=output_text,
                source_artifact=artifact_paths[-1] if artifact_paths else None,
                canon_snapshot_path=canon_snapshot_path,
                pre_report=pre_report,
                post_report=post_report,
            )
            if chapter_path:
                chapter_full = _path_for_read(Path(chapter_path))
                chapter_doc = _json_load(chapter_full, {})
                canon_memory = commit_chapter_to_canon(
                    book_id=book_id,
                    run_id=run_id,
                    chapter_path=chapter_path,
                    chapter_id=str(chapter_doc.get("chapter_id") or chapter_full.stem),
                    chapter_sha256=str(chapter_doc.get("sha256") or ""),
                )
                canon_snapshot, canon_snapshot_path = load_canon_snapshot(book_id)
                if isinstance(canon_snapshot, dict):
                    if canon_snapshot.get("project_truth") != project_truth_ref:
                        canon_snapshot["project_truth"] = dict(project_truth_ref)
                        canon_snapshot_path.write_text(
                            json.dumps(canon_snapshot, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                if isinstance(chapter_doc, dict):
                    chapter_doc["canon_snapshot_path"] = _public_path(canon_snapshot_path)
                    chapter_doc["master_canon"] = dict(master_canon_ref)
                    chapter_doc["project_truth"] = dict(project_truth_ref)
                    if is_write:
                        chapter_doc["book_bible_path"] = book_bible_binding["path"]
                        chapter_doc["book_bible_sha256"] = book_bible_binding["sha256"]
                        chapter_doc["book_bible_contract_version"] = book_bible_binding["contract_version"]
                        chapter_doc["book_bible"] = dict(book_bible_binding)
                        chapter_doc["canon_validation"] = {
                            "source": "book_bible.json",
                            "path": book_bible_binding["path"],
                            "sha256": book_bible_binding["sha256"],
                            "contract_version": book_bible_binding["contract_version"],
                        }
                    chapter_full.write_text(
                        __import__("json").dumps(chapter_doc, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

        state = save_run_state(
            run_id,
            book_id=book_id,
            status=decision,
            last_modes=modes,
            last_artifact_paths=artifact_paths,
            chapter_path=chapter_path,
        )
        state["decision"] = decision
        state["master_canon"] = dict(master_canon_ref)
        state["project_truth"] = dict(project_truth_ref)
        if is_write:
            state["book_bible"] = dict(book_bible_binding)
        json_write(ensure_run_dirs(run_id) / "run_state.json", state)
        update_latest_run_marker(book_id, run_id)

        write_audit(
            book_id=book_id,
            run_id=run_id,
            modes=modes,
            artifact_paths=artifact_paths,
            decision=decision,
            chapter_path=chapter_path,
            canon_snapshot_path=canon_snapshot_path,
            master_canon=master_canon_ref,
            project_truth=project_truth_ref,
        )

        return {
            "ok": decision == "ACCEPT",
            "status": "ok" if decision == "ACCEPT" else "error",
            "run_id": run_id,
            "book_id": book_id,
            "mode_ids": modes,
            "artifact_paths": artifact_paths,
            "artifacts": list(artifact_paths),
            "artifact_count": len(artifact_paths),
            "quality_gate": {
                "decision": decision,
                "reasons": [] if decision == "ACCEPT" else ["post_write_canon_check_failed"],
            },
            "pre_canon_check": pre_report,
            "post_canon_check": post_report,
            "canon_snapshot_path": _public_path(canon_snapshot_path),
            "chapter_path": chapter_path,
            "master_canon": master_canon_ref,
            "project_truth": project_truth_ref,
            "book_bible": dict(book_bible_binding) if is_write else {},
            "run_state": state,
            "canon_memory": canon_memory,
        }
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





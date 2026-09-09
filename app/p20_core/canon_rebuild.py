from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import HTTPException

from app.p20_core.canon_service import (
    APP_VERSION,
    _path_for_read,
    _public_path,
    ensure_book_dirs,
    json_write,
    load_run_state,
    rebuild_canon_from_chapters,
    resolve_resume_run_id,
    save_run_state,
    update_latest_run_marker,
    write_audit,
)
from app.p20_core.lock_service import (
    acquire_book_lock,
    acquire_run_lock,
    get_book_lock,
    get_run_lock,
    release_book_lock,
    release_run_lock,
)
from app.p20_core.master_canon import resolve_master_canon
from app.p20_core.project_truth import (
    assert_resume_project_truth_consistency,
    build_project_truth_binding,
)
from app.p20_core.storage_paths import get_books_root, get_runs_root


def _strip_project_truth_loaded_at(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(doc, dict):
        return doc
    cleaned = dict(doc)
    cleaned.pop("loaded_at", None)
    return cleaned


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _rel(path: Path) -> str:
    return _public_path(path)


def _book_dir(book_id: str) -> Path:
    return get_books_root() / book_id


def _run_dir(run_id: str) -> Path:
    return get_runs_root() / run_id


def _canon_memory_path(book_id: str) -> Path:
    return _book_dir(book_id) / "memory" / "canon.json"


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_json(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _stable_canon_for_hash(canon: dict[str, Any]) -> dict[str, Any]:
    stable = json.loads(json.dumps(canon, ensure_ascii=False))

    if isinstance(stable, dict):
        stable.pop("updated_at", None)

        project_truth = stable.get("project_truth")
        if isinstance(project_truth, dict):
            project_truth.pop("loaded_at", None)

    return stable


def _normalize_project_truth_request(
    requested: Any,
    expected: dict[str, Any],
) -> list[str]:
    if requested is None:
        return []

    violations: list[str] = []

    if isinstance(requested, str):
        if requested != expected.get("sha256"):
            violations.append("project_truth_sha256_mismatch")
        return violations

    if not isinstance(requested, dict):
        violations.append("project_truth_invalid_type")
        return violations

    if requested.get("contract") != expected.get("contract"):
        violations.append("project_truth_contract_mismatch")

    if requested.get("path") != expected.get("path"):
        violations.append("project_truth_path_mismatch")

    if requested.get("sha256") != expected.get("sha256"):
        violations.append("project_truth_sha256_mismatch")

    return violations


def _extract_source_hashes(canon: dict[str, Any]) -> list[str]:
    approved = canon.get("approved_chapters") or []
    hashes: list[str] = []
    for item in approved:
        if isinstance(item, dict):
            value = item.get("sha256")
            if value:
                hashes.append(str(value))
    return sorted(set(hashes))


def _build_rebuild_summary(canon: dict[str, Any]) -> dict[str, Any]:
    last = canon.get("last_accepted_chapter") or {}
    return {
        "mode": "CANON_REBUILD",
        "chapter_count": canon.get("chapter_count", 0),
        "last_accepted_chapter_id": last.get("chapter_id"),
        "approved_chapter_count": len(canon.get("approved_chapters") or []),
        "canonical_content_sha256": _sha256_json(_stable_canon_for_hash(canon)),
    }


def _write_contract_artifacts(
    *,
    run_id: str,
    book_id: str,
    decision: str,
    status: str,
    canon_memory: dict[str, Any],
    master_canon: dict[str, Any],
    project_truth: dict[str, Any],
    violations: list[str],
) -> dict[str, str]:
    run_dir = _run_dir(run_id)
    steps_dir = run_dir / "steps"
    canon_artifacts_dir = _book_dir(book_id) / "artifacts" / "canon"

    steps_dir.mkdir(parents=True, exist_ok=True)
    canon_artifacts_dir.mkdir(parents=True, exist_ok=True)

    stamp = _stamp()
    rebuild_artifact_path = canon_artifacts_dir / f"rebuild_{stamp}.json"
    snapshot_path = canon_artifacts_dir / f"canon_snapshot_{stamp}.json"
    step_path = steps_dir / "001_CANON_REBUILD.json"

    rebuild_summary = _build_rebuild_summary(canon_memory)

    common_doc = {
        "run_id": run_id,
        "book_id": book_id,
        "created_at": _utc_now(),
        "owner_api": "app.main",
        "runtime": "P20.x",
        "engine": APP_VERSION,
        "decision": decision,
        "status": status,
        "violations": list(violations),
        "master_canon": dict(master_canon),
        "project_truth": dict(project_truth),
        "input_sources": {
            "book_memory_canon": _rel(_canon_memory_path(book_id)),
            "chapters_dir": _rel(_book_dir(book_id) / "chapters"),
        },
        "source_hashes": _extract_source_hashes(canon_memory),
        "rebuild_summary": rebuild_summary,
        "chapter_count": canon_memory.get("chapter_count", 0),
        "canon_memory": canon_memory,
    }

    json_write(rebuild_artifact_path, common_doc)
    json_write(snapshot_path, common_doc)
    json_write(step_path, common_doc)

    return {
        "rebuild_artifact_path": _rel(rebuild_artifact_path),
        "canon_snapshot_path": _rel(snapshot_path),
        "step_path": _rel(step_path),
    }


def _finalize_run(
    *,
    run_id: str,
    book_id: str,
    decision: str,
    status: str,
    canon_memory: dict[str, Any],
    master_canon: dict[str, Any],
    project_truth: dict[str, Any],
    violations: list[str],
) -> dict[str, Any]:
    artifact_paths = _write_contract_artifacts(
        run_id=run_id,
        book_id=book_id,
        decision=decision,
        status=status,
        canon_memory=canon_memory,
        master_canon=master_canon,
        project_truth=project_truth,
        violations=violations,
    )

    state = save_run_state(
        run_id,
        book_id=book_id,
        status=decision,
        last_modes=["CANON_REBUILD"],
        last_artifact_paths=[
            artifact_paths["rebuild_artifact_path"],
            artifact_paths["step_path"],
        ],
        chapter_path=None,
    )
    state["decision"] = decision
    state["owner_api"] = "app.main"
    state["runtime"] = "P20.x"
    state["master_canon"] = dict(master_canon)
    state["project_truth"] = dict(project_truth)
    state["rebuild_artifact_path"] = artifact_paths["rebuild_artifact_path"]
    state["canon_snapshot_path"] = artifact_paths["canon_snapshot_path"]

    update_latest_run_marker(book_id, run_id)

    canon_snapshot_abs = _path_for_read(Path(artifact_paths["canon_snapshot_path"]))
    write_audit(
        book_id=book_id,
        run_id=run_id,
        modes=["CANON_REBUILD"],
        artifact_paths=[
            artifact_paths["rebuild_artifact_path"],
            artifact_paths["step_path"],
        ],
        decision=decision,
        chapter_path=None,
        canon_snapshot_path=canon_snapshot_abs,
        master_canon=master_canon,
        project_truth=project_truth,
    )

    audit_path = _run_dir(run_id) / "audit.json"
    audit_doc = _read_json_if_exists(audit_path)
    audit_doc["owner_api"] = "app.main"
    audit_doc["runtime"] = "P20.x"
    audit_doc["rebuild_summary"] = _build_rebuild_summary(canon_memory)
    audit_doc["violations"] = list(violations)
    audit_doc["chapter_count"] = canon_memory.get("chapter_count", 0)
    audit_doc["rebuild_artifact_path"] = artifact_paths["rebuild_artifact_path"]
    json_write(audit_path, audit_doc)

    state["audit_path"] = _rel(audit_path)
    json_write(_run_dir(run_id) / "run_state.json", state)

    return {
        "rebuild_artifact_path": artifact_paths["rebuild_artifact_path"],
        "canon_snapshot_path": artifact_paths["canon_snapshot_path"],
        "audit_path": _rel(audit_path),
        "run_state": state,
    }


def _accept_response(book_id: str, run_id: str) -> dict[str, Any]:
    master_canon = resolve_master_canon()
    project_truth = build_project_truth_binding()

    canon_memory = rebuild_canon_from_chapters(book_id)
    canon_memory["master_canon"] = dict(master_canon)
    canon_memory["project_truth"] = dict(project_truth)
    json_write(_canon_memory_path(book_id), canon_memory)

    finalized = _finalize_run(
        run_id=run_id,
        book_id=book_id,
        decision="ACCEPT",
        status="ok",
        canon_memory=canon_memory,
        master_canon=master_canon,
        project_truth=project_truth,
        violations=[],
    )

    return {
        "ok": True,
        "status": "ok",
        "run_id": run_id,
        "book_id": book_id,
        "decision": "ACCEPT",
        "chapter_count": canon_memory.get("chapter_count", 0),
        "master_canon": master_canon,
        "project_truth": project_truth,
        "rebuild_artifact_path": finalized["rebuild_artifact_path"],
        "canon_snapshot_path": finalized["canon_snapshot_path"],
        "audit_path": finalized["audit_path"],
        "rebuild_summary": _build_rebuild_summary(canon_memory),
        "violations": [],
        "run_state": finalized["run_state"],
        "canon_memory": canon_memory,
    }


def _reject_response(book_id: str, run_id: str, violations: list[str]) -> dict[str, Any]:
    master_canon = resolve_master_canon()
    project_truth = build_project_truth_binding()

    canon_memory = _read_json_if_exists(_canon_memory_path(book_id))
    canon_memory.setdefault("timeline", [])
    canon_memory.setdefault("decisions", {})
    canon_memory.setdefault("facts", {})
    canon_memory.setdefault("approved_chapters", [])
    canon_memory.setdefault("chapter_count", len(canon_memory.get("approved_chapters") or []))
    canon_memory["master_canon"] = dict(master_canon)
    canon_memory["project_truth"] = dict(project_truth)

    finalized = _finalize_run(
        run_id=run_id,
        book_id=book_id,
        decision="REJECT",
        status="reject",
        canon_memory=canon_memory,
        master_canon=master_canon,
        project_truth=project_truth,
        violations=violations,
    )

    return {
        "ok": False,
        "status": "reject",
        "run_id": run_id,
        "book_id": book_id,
        "decision": "REJECT",
        "chapter_count": canon_memory.get("chapter_count", 0),
        "master_canon": master_canon,
        "project_truth": project_truth,
        "rebuild_artifact_path": finalized["rebuild_artifact_path"],
        "canon_snapshot_path": finalized["canon_snapshot_path"],
        "audit_path": finalized["audit_path"],
        "rebuild_summary": _build_rebuild_summary(canon_memory),
        "violations": list(violations),
        "run_state": finalized["run_state"],
        "canon_memory": canon_memory,
    }


def canon_rebuild_endpoint(body: Dict[str, Any]) -> Dict[str, Any]:
    body = dict(body or {})
    book_id = str(body.get("book_id") or "default")
    resume = bool(body.get("resume"))
    run_id = resolve_resume_run_id(book_id, body.get("run_id"), resume)
    requested_project_truth = body.get("project_truth")

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

        expected_project_truth = build_project_truth_binding()

        if resume:
            _resume_run_state = load_run_state(run_id)
            if (
                isinstance(_resume_run_state, dict)
                and _resume_run_state
                and str(_resume_run_state.get("book_id") or "") == book_id
                and isinstance(_resume_run_state.get("project_truth"), dict)
            ):
                assert_resume_project_truth_consistency(expected_project_truth, _resume_run_state)

        violations = _normalize_project_truth_request(
            requested_project_truth,
            expected_project_truth,
        )
        if violations:
            return _reject_response(book_id=book_id, run_id=run_id, violations=violations)

        return _accept_response(book_id=book_id, run_id=run_id)

    finally:
        if run_lock_acquired:
            release_run_lock(run_id)
        if book_lock_acquired:
            release_book_lock(book_id)


def rebuild_canon_endpoint(body: Dict[str, Any]) -> Dict[str, Any]:
    return canon_rebuild_endpoint(body)


def rebuild_canon(body: Dict[str, Any]) -> Dict[str, Any]:
    return canon_rebuild_endpoint(body)


def canon_rebuild(body: Dict[str, Any]) -> Dict[str, Any]:
    return canon_rebuild_endpoint(body)


__all__ = [
    "canon_rebuild_endpoint",
    "rebuild_canon_endpoint",
    "rebuild_canon",
    "canon_rebuild",
]

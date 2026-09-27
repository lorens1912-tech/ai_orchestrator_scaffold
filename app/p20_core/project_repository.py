from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from app.p20_core.domain_records import EdgeRecord
    from app.p20_core.project_graph import (
        DependencyTraversalPolicy, DependencyTraversalStep, GraphNodeRef,
    )

from app.p20_core.domain_mutation_guard import (
    DEFAULT_MUTATION_POLICY,
    DomainMutationGuard,
    MutationContext,
    MutationPolicy,
    MutationSource,
    MutationType,
)
from app.p20_core.storage_paths import (
    get_storage_root,
)
from app.p20_core.memory_ledger import (
    _canonical_schema_sql,
    _canonical_sqlite_identifier,
    _schema_check_signatures,
    _schema_has_token_sequence,
    _schema_sql_tokens,
    _sqlite_index_key_signature,
    _sqlite_table_xinfo_signature,
    MEMORY_LEDGER_CONTROL_KEY,
    MEMORY_LEDGER_COVERAGE,
    MemoryEventRecord,
    MemoryLedgerError,
    MemoryLedgerIntegrityError,
    MemoryLedgerMigrationRequired,
    MemoryLedgerNotActive,
    append_memory_event as _append_memory_event,
    build_memory_event,
    canonical_json as _ledger_canonical_json,
    create_memory_ledger_schema,
    event_key_json as _ledger_event_key_json,
    memory_event_id as _ledger_memory_event_id,
    next_sequence as _ledger_next_sequence,
    parse_json_object as _ledger_parse_json_object,
    read_memory_event as _read_memory_event,
    sha256_text as _ledger_sha256_text,
    validate_memory_ledger_schema,
    validate_memory_event_indexes,
)


PROJECT_DB_SCHEMA_VERSION = 7
PROJECT_DB_FILENAME = "project.db"
SERIES_DB_SCHEMA_VERSION = 4
SERIES_DB_FILENAME = "series.db"
SYSTEM_DB_SCHEMA_VERSION = 1
SYSTEM_DB_FILENAME = "agentpro_system.db"
PROJECT_REGISTRY_METADATA_KEY = "project_registry.v1"
_DOMAIN_DB_BUSY_TIMEOUT_MS = 30_000
_SYSTEM_DB_BUSY_TIMEOUT_MS = 30_000
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ProjectStorageError(ValueError):
    pass


class SeriesStorageError(ValueError):
    pass


class SystemStorageError(ValueError):
    pass


class SchemaMigrationError(ValueError):
    pass


class ScopeValidationError(ValueError):
    pass


class SeriesAccessError(ValueError):
    pass


def _classify_required_memory_event(
    conn: sqlite3.Connection,
    *,
    scope_type: str,
    scope_id: str,
    operation_namespace: str,
    operation_id: str,
    event_slot: Iterable[str],
    baseline_locator: str | None = None,
    baseline_category: str | None = None,
    baseline_identity: tuple[str, str] | None = None,
) -> str:
    """Distinguish an emitted event from a specific legacy bootstrap identity."""
    if baseline_locator is None and baseline_category is None:
        raise MemoryLedgerError("legacy classification requires a concrete baseline identity")
    expected_id = _ledger_memory_event_id(
        scope_type,
        scope_id,
        {"namespace": operation_namespace, "id": operation_id},
        tuple(event_slot),
    )
    if _read_memory_event(conn, expected_id) is not None:
        return "RECORDED"
    rows = conn.execute(
        "SELECT record_json FROM memory_events WHERE event_type = 'LEDGER_BASELINE_OBJECT'"
    ).fetchall()
    for row in rows:
        event = MemoryEventRecord.from_dict(json.loads(str(row["record_json"])))
        payload = event.structured_payload
        if baseline_locator is not None and payload.get("locator") != baseline_locator:
            continue
        if baseline_category is not None and payload.get("category") != baseline_category:
            continue
        if baseline_identity is None:
            return "LEGACY_BEFORE_LEDGER"
        try:
            snapshot = json.loads(str(payload.get("snapshot_text") or ""))
        except json.JSONDecodeError as exc:
            raise MemoryLedgerIntegrityError("legacy baseline snapshot is invalid") from exc
        field, expected = baseline_identity
        if isinstance(snapshot, dict):
            observed = snapshot.get(field)
            if str(observed) == expected or isinstance(observed, dict) and expected in observed:
                return "LEGACY_BEFORE_LEDGER"
    return "MEMORY_LEDGER_NEEDS_INTERVENTION"


def _decode_memory_ledger_cursor(
    raw: str,
    *,
    conn: sqlite3.Connection,
    scope_type: str,
    scope_id: str,
    activation_event_id: str,
    filter_data: dict[str, Any],
    error_type: type[ValueError],
) -> tuple[int, int]:
    try:
        parsed = _ledger_parse_json_object(raw)
    except MemoryLedgerError as exc:
        raise error_type("memory ledger cursor is invalid") from exc
    expected_fields = {
        "scope_type", "scope_id", "activation_event_id", "filter",
        "after_sequence", "high_watermark", "high_watermark_content_hash",
    }
    if set(parsed) != expected_fields:
        raise error_type("memory ledger cursor fields are invalid")
    if (
        parsed["scope_type"] != scope_type
        or parsed["scope_id"] != scope_id
        or parsed["activation_event_id"] != activation_event_id
        or parsed["filter"] != filter_data
    ):
        raise error_type("memory ledger cursor does not match request")
    after, high_watermark = parsed["after_sequence"], parsed["high_watermark"]
    if (
        not isinstance(after, int) or isinstance(after, bool) or after < 0
        or not isinstance(high_watermark, int) or isinstance(high_watermark, bool)
        or high_watermark < after
    ):
        raise error_type("memory ledger cursor bounds are invalid")
    row = conn.execute(
        "SELECT content_hash FROM memory_events WHERE sequence = ?", (high_watermark,)
    ).fetchone()
    if row is None or str(row["content_hash"]) != parsed["high_watermark_content_hash"]:
        raise error_type("memory ledger cursor high-watermark is invalid")
    return after, high_watermark


def _encode_memory_ledger_cursor(
    *,
    scope_type: str,
    scope_id: str,
    activation_event_id: str,
    filter_data: dict[str, Any],
    after_sequence: int,
    high_watermark: int,
    high_watermark_content_hash: str,
) -> str:
    return _ledger_canonical_json({
        "scope_type": scope_type,
        "scope_id": scope_id,
        "activation_event_id": activation_event_id,
        "filter": filter_data,
        "after_sequence": after_sequence,
        "high_watermark": high_watermark,
        "high_watermark_content_hash": high_watermark_content_hash,
    })


def _validate_active_memory_ledger(
    conn: sqlite3.Connection,
    *,
    control: dict[str, Any],
    scope_type: str,
    scope_id: str,
) -> MemoryEventRecord:
    operation_id = control.get("bootstrap_operation_id")
    activation_id = control.get("activation_event_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise MemoryLedgerIntegrityError("active ledger bootstrap identity is invalid")
    if not isinstance(activation_id, str) or not activation_id:
        raise MemoryLedgerIntegrityError("active ledger activation identity is invalid")
    activation = _read_memory_event(conn, activation_id)
    if activation is None:
        raise MemoryLedgerIntegrityError("active ledger is missing activation event")
    if (
        activation.scope_type != scope_type
        or activation.scope_id != scope_id
        or activation.operation != {"namespace": "LEDGER_BOOTSTRAP", "id": operation_id}
        or activation.event_slot != ("activated",)
        or activation.event_type != "LEDGER_ACTIVATED"
    ):
        raise MemoryLedgerIntegrityError("active ledger activation binding is invalid")
    rows = conn.execute(
        "SELECT memory_event_id FROM memory_events "
        "WHERE operation_namespace = 'LEDGER_BOOTSTRAP' AND operation_id = ? "
        "AND event_type = 'LEDGER_BASELINE_OBJECT' ORDER BY sequence",
        (operation_id,),
    ).fetchall()
    manifest: list[dict[str, str]] = []
    for row in rows:
        event = _read_memory_event(conn, str(row["memory_event_id"]))
        if event is None:
            raise MemoryLedgerIntegrityError("active ledger baseline event disappeared")
        payload = event.structured_payload
        manifest.append({
            "category": str(payload["category"]),
            "locator": str(payload["locator"]),
            "bytes_hash": str(payload["bytes_hash"]),
        })
    manifest_hash = _ledger_sha256_text(_ledger_canonical_json(manifest))
    baseline_count = len(manifest)
    payload = activation.structured_payload
    if (
        control.get("coverage") != MEMORY_LEDGER_COVERAGE
        or control.get("baseline_count") != baseline_count
        or control.get("manifest_hash") != manifest_hash
        or payload.get("coverage") != MEMORY_LEDGER_COVERAGE
        or payload.get("baseline_count") != baseline_count
        or payload.get("manifest_hash") != manifest_hash
    ):
        raise MemoryLedgerIntegrityError("active ledger baseline manifest is invalid")
    return activation


def _validate_memory_ledger_control_document(control: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "state", "coverage", "bootstrap_operation_id", "activation_event_id",
        "baseline_count", "manifest_hash",
    }
    if set(control) != expected:
        raise MemoryLedgerIntegrityError("memory ledger control fields are invalid")
    if control["coverage"] != MEMORY_LEDGER_COVERAGE:
        raise MemoryLedgerIntegrityError("memory ledger control coverage is invalid")
    state = control["state"]
    if state == "SCHEMA_READY":
        if any(control[key] is not None for key in (
            "bootstrap_operation_id", "activation_event_id", "baseline_count", "manifest_hash",
        )):
            raise MemoryLedgerIntegrityError("schema-ready ledger control has activation data")
    elif state == "ACTIVE":
        if (
            not isinstance(control["bootstrap_operation_id"], str)
            or not control["bootstrap_operation_id"]
            or not isinstance(control["activation_event_id"], str)
            or not control["activation_event_id"]
            or not isinstance(control["baseline_count"], int)
            or isinstance(control["baseline_count"], bool)
            or control["baseline_count"] < 0
            or not isinstance(control["manifest_hash"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", control["manifest_hash"])
        ):
            raise MemoryLedgerIntegrityError("active ledger control is incomplete")
    else:
        raise MemoryLedgerIntegrityError("memory ledger control state is invalid")
    return control


def _read_validated_memory_ledger_control(
    conn: sqlite3.Connection, *, metadata_table: str,
) -> dict[str, Any]:
    row = conn.execute(
        f"SELECT value FROM {metadata_table} WHERE key = ?", (MEMORY_LEDGER_CONTROL_KEY,)
    ).fetchone()
    if row is None:
        raise MemoryLedgerNotActive("memory ledger control is missing")
    try:
        control = _ledger_parse_json_object(str(row["value"]))
    except MemoryLedgerError as exc:
        raise MemoryLedgerIntegrityError("memory ledger control is invalid") from exc
    return _validate_memory_ledger_control_document(control)


_PROJECT_LEDGER_METADATA_PREFIXES = (
    "canonical_versions.v1:",
    "canonical_proposal.v1:",
    "canonical_pipeline.v1:",
    "canonical_commit.v1:",
    "cross_store_operation.v1:",
    "cross_store_audit.v1:",
)
_SERIES_LEDGER_METADATA_PREFIXES = (
    "canonical_versions.v1:",
    "canonical_proposal.v1:",
    "canonical_pipeline.v1:",
    "canonical_commit.v1:",
)
_PROJECT_OUT_OF_COVERAGE_METADATA_PREFIXES = (
    "model_invocation.v1:",
    "evaluation.v1:",
    "reevaluation_request.v1:",
    "p20_execution_input.v1:",
    "model_route.v1:",
    "adaptive_style_recipe.v1:",
    "cross_store_project_write.v1:",
)
_SECRET_FIELD_NAMES = {
    "api_key", "access_token", "refresh_token", "password", "secret",
    "client_secret", "credential", "credentials",
}


def _contains_secret_field(value: Any) -> bool:
    if isinstance(value, dict):
        if any(str(key).lower() in _SECRET_FIELD_NAMES for key in value):
            return True
        return any(_contains_secret_field(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_secret_field(item) for item in value)
    return False


def _validate_canonical_versions_metadata(
    conn: sqlite3.Connection,
    *,
    key: str,
    parsed: Any,
    scope_type: str,
    scope_id: str,
    project_id: str | None,
) -> None:
    from app.p20_core.memory_extraction import memory_record_types

    suffix = key.removeprefix("canonical_versions.v1:")
    record_type, separator, record_id = suffix.partition(":")
    constructor = memory_record_types().get(record_type)
    if constructor is None or not separator or not record_id or not isinstance(parsed, list) or not parsed:
        raise MemoryLedgerIntegrityError("canonical history bootstrap key is invalid")
    versions: list[int] = []
    normalized: list[dict[str, Any]] = []
    for raw in parsed:
        if not isinstance(raw, dict):
            raise MemoryLedgerIntegrityError("canonical history entry is invalid")
        try:
            entity = constructor(**raw)
            canonical = entity.to_dict()
        except (TypeError, ValueError) as exc:
            raise MemoryLedgerIntegrityError("canonical history entry is invalid") from exc
        if canonical != raw or str(entity.record_id) != record_id or entity.memory_record_type != record_type:
            raise MemoryLedgerIntegrityError("canonical history identity is invalid")
        if scope_type == "PROJECT":
            if str(entity.project_id) != project_id:
                raise MemoryLedgerIntegrityError("canonical history project binding is invalid")
        else:
            membership = conn.execute(
                "SELECT 1 FROM series_memberships WHERE project_id=?", (str(entity.project_id),)
            ).fetchone()
            if membership is None:
                raise MemoryLedgerIntegrityError("canonical history series membership is invalid")
        versions.append(int(entity.version))
        normalized.append(canonical)
    if any(current <= previous for previous, current in zip(versions, versions[1:])):
        raise MemoryLedgerIntegrityError("canonical history versions are not strictly increasing")
    if scope_type == "PROJECT":
        current = conn.execute(
            "SELECT payload_json FROM project_structured_memory_records "
            "WHERE scope_type='PROJECT' AND scope_id=? AND record_type=? AND record_id=?",
            (project_id, record_type, record_id),
        ).fetchone()
        authoritative = None if current is None else json.loads(str(current["payload_json"]))
    else:
        current = conn.execute(
            "SELECT payload_json FROM series_state_records "
            "WHERE scope_type='SERIES' AND scope_id=? AND state_kind='SERIES_CANON' "
            "AND record_type=? AND record_id=?",
            (scope_id, record_type, record_id),
        ).fetchone()
        wrapper = None if current is None else json.loads(str(current["payload_json"]))
        authoritative = None if not isinstance(wrapper, dict) else wrapper.get("state")
    if authoritative != normalized[-1]:
        raise MemoryLedgerIntegrityError("canonical history latest version is not authoritative")


def _validate_canonical_pipeline_metadata(
    conn: sqlite3.Connection,
    *,
    key: str,
    parsed: Any,
    scope_type: str,
    scope_id: str,
    project_id: str | None,
    book_id: str | None,
) -> None:
    from app.p20_core.memory_extraction import (
        MemoryExtractionVerification,
        ModelInvocation,
        SceneMemorySource,
        StructuredMemoryExtractionCandidate,
        decode_memory_entities,
        validate_memory_model_result,
        verify_memory_extraction_candidate,
    )
    from app.p20_core.context_builder import ContextPackage

    operation_id = key.removeprefix("canonical_pipeline.v1:")
    if not operation_id or operation_id in {".", ".."} or not isinstance(parsed, dict):
        raise MemoryLedgerIntegrityError("canonical pipeline operation id is invalid")
    allowed = {"started", "source", "attempts", "last_extractor", "last_verifier", "result"}
    if set(parsed) - allowed or not {"started", "source", "attempts"}.issubset(parsed):
        raise MemoryLedgerIntegrityError("canonical pipeline bootstrap fields are invalid")
    if parsed["started"] is not True:
        raise MemoryLedgerIntegrityError("canonical pipeline bootstrap state is incomplete")
    source = parsed["source"]
    source_fields = {
        "text", "artifact_ref", "artifact_id", "version", "hash", "scene_id",
        "accepted_step_artifact", "context_package_id", "context_hash",
    }
    metadata_prefix = "project_metadata:" if scope_type == "PROJECT" else "series_metadata:"
    if not isinstance(source, dict) or set(source) != source_fields:
        raise MemoryLedgerIntegrityError("canonical pipeline source fields are invalid")
    expected_ref = metadata_prefix + key + "#source"
    if (
        source.get("version") != 1
        or source.get("artifact_id") != "artifact-" + operation_id
        or source.get("scene_id") != "SCENE-" + operation_id
        or source.get("artifact_ref") != expected_ref
        or not isinstance(source.get("text"), str)
        or hashlib.sha256(source["text"].encode("utf-8")).hexdigest() != source.get("hash")
        or re.fullmatch(r"[0-9a-f]{64}", str(source.get("context_hash") or "")) is None
        or any(not isinstance(source.get(name), str) or not source[name] for name in (
            "accepted_step_artifact", "context_package_id",
        ))
    ):
        raise MemoryLedgerIntegrityError("canonical pipeline source binding is invalid")
    attempts = parsed["attempts"]
    if not isinstance(attempts, list):
        raise MemoryLedgerIntegrityError("canonical pipeline attempts are invalid")
    bound_project_id, bound_book_id = project_id, book_id
    bound_series_id: str | None = scope_id if scope_type == "SERIES" else None
    project_series_seen = False
    if scope_type == "SERIES":
        if attempts:
            try:
                first_input = attempts[0]["extractor"]["input"]
                bound_project_id = str(first_input["project_id"])
                bound_book_id = str(first_input["book_id"])
            except (KeyError, TypeError) as exc:
                raise MemoryLedgerIntegrityError("series pipeline initiating identity is missing") from exc
            membership = conn.execute(
                "SELECT book_id FROM series_memberships WHERE project_id=?", (bound_project_id,)
            ).fetchone()
            if membership is None or str(membership["book_id"]) != bound_book_id:
                raise MemoryLedgerIntegrityError("series pipeline initiating identity is invalid")
        elif result := parsed.get("result"):
            raise MemoryLedgerIntegrityError("series terminal pipeline has no initiating identity")

    base_call_input_fields = {
        "project_id", "book_id", "series_id", "run_id", "step_id", "source", "candidate",
        "role", "_requested_model", "_effective_model", "record_schema", "instruction",
        "context_package_id", "context_hash", "_context_package",
    }
    call_input_field_sets = (
        base_call_input_fields,
        base_call_input_fields | {"revision_feedback"},
        base_call_input_fields | {"revision_feedback", "author_instruction"},
        base_call_input_fields | {
            "revision_feedback", "author_instruction", "current_canonical_versions",
        },
    )

    def validate_call(call: Any, role: str, *, candidate: dict[str, Any] | None) -> ModelInvocation:
        nonlocal bound_project_id, bound_book_id, bound_series_id, project_series_seen
        if not isinstance(call, dict) or set(call) != {"invocation", "input", "result"}:
            raise MemoryLedgerIntegrityError("canonical pipeline model evidence is invalid")
        if (not isinstance(call["input"], dict)
                or set(call["input"]) not in call_input_field_sets):
            raise MemoryLedgerIntegrityError("canonical pipeline model input is invalid")
        try:
            invocation = ModelInvocation(**call["invocation"])
            model_input = call["input"]
            package = ContextPackage.from_dict(model_input["_context_package"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MemoryLedgerIntegrityError("canonical pipeline context package is invalid") from exc
        if scope_type == "SERIES" and bound_project_id is None:
            bound_project_id = str(model_input.get("project_id") or "")
            bound_book_id = str(model_input.get("book_id") or "")
            membership = conn.execute(
                "SELECT book_id FROM series_memberships WHERE project_id=?", (bound_project_id,)
            ).fetchone()
            if membership is None or str(membership["book_id"]) != bound_book_id:
                raise MemoryLedgerIntegrityError("series pipeline initiating identity is invalid")
        model_series_id = model_input.get("series_id")
        if scope_type == "PROJECT":
            if model_series_id is not None and (
                not isinstance(model_series_id, str) or not model_series_id.strip()
            ):
                raise MemoryLedgerIntegrityError("canonical pipeline series binding is invalid")
            if not project_series_seen:
                bound_series_id = model_series_id
                project_series_seen = True
        author_instruction = model_input.get("author_instruction", "")
        current_canonical_versions = model_input.get("current_canonical_versions", {})
        if (
            not isinstance(author_instruction, str)
            or not isinstance(current_canonical_versions, dict)
            or any(
                not isinstance(identity, str)
                or not identity.strip()
                or isinstance(version, bool)
                or not isinstance(version, int)
                or version < 1
                for identity, version in current_canonical_versions.items()
            )
        ):
            raise MemoryLedgerIntegrityError("canonical pipeline model input extensions are invalid")
        if (
            invocation.role.value != role
            or model_input.get("role") != role
            or model_input.get("source") != source
            or model_input.get("candidate") != candidate
            or model_input.get("project_id") != bound_project_id
            or model_input.get("book_id") != bound_book_id
            or model_series_id != bound_series_id
            or not isinstance(model_input.get("context_package_id"), str)
            or not model_input["context_package_id"]
            or re.fullmatch(r"[0-9a-f]{64}", str(model_input.get("context_hash") or "")) is None
            or invocation.metadata.get("context_package_id") != model_input["context_package_id"]
            or invocation.metadata.get("context_hash") != model_input["context_hash"]
            or invocation.metadata.get("requested_model") != model_input.get("_requested_model")
            or invocation.metadata.get("effective_model") != model_input.get("_effective_model")
            or invocation.model != model_input.get("_effective_model")
            or package.context_package_id != model_input["context_package_id"]
            or package.context_hash != model_input["context_hash"]
            or package.project_id != bound_project_id
            or package.book_id != bound_book_id
            or package.series_id != bound_series_id
            or package.run_id != model_input.get("run_id")
            or package.step_id != model_input.get("step_id")
            or package.role.value != "CANON"
            or package.mode != "MEMORY_" + role
            or package.effective_model != model_input.get("_effective_model")
        ):
            raise MemoryLedgerIntegrityError("canonical pipeline model binding is invalid")
        validate_memory_model_result(role, call["result"])
        return invocation

    last_verification: dict[str, Any] | None = None
    for ordinal, attempt in enumerate(attempts, start=1):
        current_attempt_fields = {
            "source", "extractor", "verifier", "candidate_created_at", "attempt",
            "canonical_integrity_mismatches", "verification",
        }
        legacy_attempt_fields = current_attempt_fields - {"canonical_integrity_mismatches"}
        if (not isinstance(attempt, dict)
                or set(attempt) not in (current_attempt_fields, legacy_attempt_fields)):
            raise MemoryLedgerIntegrityError("canonical pipeline attempt fields are invalid")
        if attempt["source"] != source or attempt["attempt"] != ordinal:
            raise MemoryLedgerIntegrityError("canonical pipeline attempt ordering is invalid")
        extractor_invocation = validate_call(attempt["extractor"], "EXTRACTOR", candidate=None)
        expected_feedback = [] if last_verification is None else last_verification["must_fix"]
        if ("revision_feedback" in attempt["extractor"]["input"]
                and attempt["extractor"]["input"]["revision_feedback"] != expected_feedback):
            raise MemoryLedgerIntegrityError("canonical pipeline revision feedback is invalid")
        try:
            verification_raw = attempt["verification"]
            verifier_invocation = ModelInvocation(**verification_raw["verifier_call"])
            typed_verification = MemoryExtractionVerification(
                **{**verification_raw, "verifier_call": verifier_invocation}
            )
            source_contract = SceneMemorySource.from_text(
                project_id=str(bound_project_id),
                source_scene_id=source["scene_id"],
                source_artifact_ref=source["artifact_ref"],
                source_text=source["text"],
            )
            candidate_contract = StructuredMemoryExtractionCandidate.from_source(
                candidate_id=typed_verification.candidate_id,
                source=source_contract,
                extraction_attempt=ordinal,
                candidate_records=decode_memory_entities(attempt["extractor"]["result"]["records"]),
                extractor_call=extractor_invocation,
                created_at=attempt["candidate_created_at"],
                scene_quality_status=typed_verification.scene_quality_status,
            )
            verifier_call = validate_call(
                attempt["verifier"], "VERIFIER", candidate=candidate_contract.to_dict()
            )
            if attempt["verifier"]["input"].get("current_canonical_versions", {}) != (
                attempt["extractor"]["input"].get("current_canonical_versions", {})
            ):
                raise MemoryLedgerIntegrityError("canonical pipeline version basis is invalid")
            if ("revision_feedback" in attempt["verifier"]["input"]
                    and attempt["verifier"]["input"]["revision_feedback"] != []):
                raise MemoryLedgerIntegrityError("canonical verifier revision feedback is invalid")
            rebuilt = verify_memory_extraction_candidate(
                candidate_contract,
                source=source_contract,
                verifier_call=verifier_call,
                canonical_integrity_mismatches=attempt.get(
                    "canonical_integrity_mismatches", ()
                ),
                **attempt["verifier"]["result"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MemoryLedgerIntegrityError("canonical pipeline attempt binding is invalid") from exc
        if rebuilt.to_dict() != verification_raw or typed_verification.to_dict() != verification_raw:
            raise MemoryLedgerIntegrityError("canonical pipeline verification is invalid")
        last_verification = verification_raw

    for field, role in (("last_extractor", "EXTRACTOR"), ("last_verifier", "VERIFIER")):
        if field not in parsed:
            continue
        evidence = parsed[field]
        if not isinstance(evidence, dict):
            raise MemoryLedgerIntegrityError("canonical pipeline last-call evidence is invalid")
        if evidence.get("status") == "FAILED":
            if set(evidence) != {"status", "reason", "invocation"}:
                raise MemoryLedgerIntegrityError("canonical pipeline failed-call evidence is invalid")
            invocation = ModelInvocation(**evidence["invocation"])
            metadata = invocation.metadata
            if (
                invocation.role.value != role
                or not isinstance(evidence.get("reason"), str)
                or not evidence["reason"]
                or metadata.get("effective_model") != invocation.model
                or not isinstance(metadata.get("context_package_id"), str)
                or not metadata["context_package_id"]
                or re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("context_hash") or "")) is None
            ):
                raise MemoryLedgerIntegrityError("canonical pipeline failed-call binding is invalid")
        else:
            candidate = None if role == "EXTRACTOR" else evidence.get("input", {}).get("candidate")
            validate_call(evidence, role, candidate=candidate)
            settled = bool(attempts) and evidence == attempts[-1][
                "extractor" if role == "EXTRACTOR" else "verifier"
            ]
            if settled:
                continue
            if role == "EXTRACTOR":
                if candidate is not None:
                    raise MemoryLedgerIntegrityError("canonical pipeline pending extractor is invalid")
            else:
                if (
                    not isinstance(candidate, dict)
                    or candidate.get("extraction_attempt") != len(attempts) + 1
                    or not isinstance(parsed.get("last_extractor"), dict)
                    or candidate.get("extractor_call") != parsed["last_extractor"].get("invocation")
                ):
                    raise MemoryLedgerIntegrityError("canonical pipeline pending verifier is invalid")

    result = parsed.get("result")
    if result is not None:
        if not isinstance(result, dict) or result.get("canonical_commit") is not False:
            raise MemoryLedgerIntegrityError("canonical pipeline terminal result is invalid")
        if result.get("status") in {"REJECT", "ESCALATED"}:
            if set(result) != {"status", "canonical_commit", "verification"} or result.get("verification") != last_verification:
                raise MemoryLedgerIntegrityError("canonical pipeline terminal verification is invalid")
            if last_verification is None or last_verification.get("memory_extraction_status") != result["status"]:
                raise MemoryLedgerIntegrityError("canonical pipeline terminal status is invalid")
        elif result.get("status") == "FAILED":
            if set(result) != {"status", "canonical_commit", "reason"} or not isinstance(result.get("reason"), str) or not result["reason"]:
                raise MemoryLedgerIntegrityError("canonical pipeline failure result is invalid")
        else:
            raise MemoryLedgerIntegrityError("canonical pipeline terminal result is unsupported")
    elif last_verification is not None and last_verification.get("decision") == "ACCEPT":
        proposal_row = conn.execute(
            f"SELECT value FROM {'project_metadata' if scope_type == 'PROJECT' else 'series_metadata'} "
            "WHERE key=?",
            ("canonical_proposal.v1:proposal-" + operation_id,),
        ).fetchone()
        try:
            proposal_document = json.loads(str(proposal_row["value"]))
            record = proposal_document["versions"][proposal_document["current_version"]]
        except (TypeError, KeyError, json.JSONDecodeError) as exc:
            raise MemoryLedgerIntegrityError("accepted pipeline has no bound proposal") from exc
        if record.get("pipeline") != attempts[-1]:
            raise MemoryLedgerIntegrityError("accepted pipeline proposal binding is invalid")


def _validate_research_bootstrap_metadata(
    parsed: Any, *, project_id: str, book_id: str,
) -> None:
    from app.p20_core.research import (
        AuthorDecision,
        ConflictRecord,
        ResearchClaim,
        ResearchRecord,
        ResearchSource,
        SourceVersion,
        VERIFICATION_CRITERIA,
        citations,
        digest,
    )
    from app.p20_core.domain_records import DomainId, DomainNamespace

    top_fields = {
        "schema_version", "project_id", "book_id", "records", "sources",
        "claims", "operations", "conflicts", "decisions",
    }
    if (
        not isinstance(parsed, dict)
        or set(parsed) != top_fields
        or parsed.get("schema_version") != 1
        or parsed.get("project_id") != project_id
        or parsed.get("book_id") != book_id
        or any(not isinstance(parsed.get(name), dict) for name in (
            "records", "sources", "claims", "operations", "conflicts", "decisions",
        ))
    ):
        raise MemoryLedgerIntegrityError("research bootstrap state is invalid")

    typed_tables = {
        "records": (ResearchRecord, "research_id"),
        "sources": (ResearchSource, "source_id"),
        "claims": (ResearchClaim, "claim_id"),
    }
    try:
        for table, (model_type, identity_field) in typed_tables.items():
            for identity, versions in parsed[table].items():
                if not isinstance(identity, str) or not identity or not isinstance(versions, list) or not versions:
                    raise MemoryLedgerIntegrityError("research bootstrap version chain is invalid")
                normalized = [model_type.model_validate(value).model_dump() for value in versions]
                if normalized != versions or any(
                    value[identity_field] != identity
                    or value["version"] != ordinal
                    or value.get("project_id", project_id) != project_id
                    for ordinal, value in enumerate(normalized, start=1)
                ):
                    raise MemoryLedgerIntegrityError("research bootstrap version binding is invalid")
        for versions in parsed["claims"].values():
            for claim in versions:
                if claim["research_id"] not in parsed["records"]:
                    raise MemoryLedgerIntegrityError("research claim owner is unavailable")
                citations(parsed, claim["source_refs"])
        for conflict_id, raw in parsed["conflicts"].items():
            conflict = ConflictRecord.model_validate(raw).model_dump()
            if (
                conflict != raw
                or conflict["conflict_id"] != conflict_id
                or conflict["scope_id"] != project_id
                or conflict["entity_id"] not in parsed["claims"]
            ):
                raise MemoryLedgerIntegrityError("research conflict binding is invalid")
            citations(parsed, conflict["source_refs_a"])
            citations(parsed, conflict["source_refs_b"])
        for decision_id, raw in parsed["decisions"].items():
            decision = AuthorDecision.model_validate(raw).model_dump()
            if (
                decision != raw
                or decision["decision_id"] != decision_id
                or decision["scope_id"] != project_id
                or decision["subject_id"] not in parsed["claims"]
                or re.fullmatch(r"[0-9a-f]{64}", decision["proposal_hash"]) is None
                or re.fullmatch(r"[0-9a-f]{64}", decision["evidence_hash"]) is None
                or re.fullmatch(r"[0-9a-f]{64}", decision["target_state_hash"]) is None
                or DomainId.parse(decision["target_fact_id"]).namespace != DomainNamespace.FACT
            ):
                raise MemoryLedgerIntegrityError("research decision binding is invalid")
    except MemoryLedgerIntegrityError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise MemoryLedgerIntegrityError("research bootstrap typed state is invalid") from exc

    base_operation_fields = {
        "request_hash", "request", "inputs", "status", "started_at", "attempt_id", "attempts",
    }
    for operation_id, operation in parsed["operations"].items():
        if not isinstance(operation_id, str) or not operation_id or not isinstance(operation, dict):
            raise MemoryLedgerIntegrityError("research bootstrap operation is invalid")
        if operation.get("status") == "COMPLETED" and set(operation).issubset({
            "request_hash", "result", "status", "memory_ledger_status",
        }):
            if (
                set(operation) - {"request_hash", "result", "status", "memory_ledger_status"}
                or set(operation) < {"request_hash", "result", "status"}
                or re.fullmatch(r"[0-9a-f]{64}", str(operation.get("request_hash") or "")) is None
            ):
                raise MemoryLedgerIntegrityError("research command bootstrap operation is invalid")
            result = operation["result"]
            matches = [
                value for table in ("records", "sources")
                for versions in parsed[table].values() for value in versions
                if value == result
            ]
            if len(matches) != 1:
                raise MemoryLedgerIntegrityError("research command result is not owned by state")
            continue
        status = operation.get("status")
        if status not in {"RUNNING", "COMPLETED", "FAILED"}:
            raise MemoryLedgerIntegrityError("research bootstrap operation status is unknown")
        allowed = set(base_operation_fields)
        required = set(base_operation_fields)
        if status == "COMPLETED":
            allowed |= {"result", "invocation", "result_hash", "criteria_version", "criteria_hash"}
            required |= {"result", "invocation", "result_hash", "criteria_version", "criteria_hash"}
        elif status == "FAILED":
            allowed |= {"failure", "recovered_at", "recovered_by"}
            required |= {"failure"}
        if set(operation) - allowed or not required.issubset(operation):
            raise MemoryLedgerIntegrityError("research bootstrap operation fields are invalid")
        request = operation.get("request")
        inputs = operation.get("inputs")
        if (
            not isinstance(request, dict)
            or set(request) != {
                "action", "research_id", "source_refs", "claim_ids", "effective_model",
                "requested_model", "project_id", "book_id", "run_id", "step_id",
            }
            or request.get("action") not in {"EXTRACT", "VERIFY"}
            or request.get("project_id") != project_id
            or request.get("book_id") != book_id
            or not all(isinstance(request.get(name), str) and request[name] for name in (
                "research_id", "effective_model", "run_id", "step_id",
            ))
            or request["research_id"] not in parsed["records"]
            or operation.get("request_hash") != digest(request)
            or not isinstance(inputs, dict)
            or set(inputs) != {"research", "sources", "claims"}
            or not isinstance(operation.get("attempts"), list)
            or not isinstance(operation.get("started_at"), str)
            or not operation["started_at"]
        ):
            raise MemoryLedgerIntegrityError("research bootstrap request binding is invalid")
        research_input = inputs.get("research")
        if research_input not in parsed["records"].get(request["research_id"], []):
            raise MemoryLedgerIntegrityError("research bootstrap pinned record is invalid")
        source_inputs = inputs.get("sources")
        claim_inputs = inputs.get("claims")
        if not isinstance(source_inputs, list) or not isinstance(claim_inputs, list):
            raise MemoryLedgerIntegrityError("research bootstrap pinned inputs are invalid")
        for source in source_inputs:
            if source not in parsed["sources"].get(source.get("source_id") if isinstance(source, dict) else None, []):
                raise MemoryLedgerIntegrityError("research bootstrap pinned source is invalid")
        for claim in claim_inputs:
            if claim not in parsed["claims"].get(claim.get("claim_id") if isinstance(claim, dict) else None, []):
                raise MemoryLedgerIntegrityError("research bootstrap pinned claim is invalid")
        try:
            requested_sources = [
                SourceVersion.model_validate(value).model_dump()
                for value in request["source_refs"]
            ]
        except (TypeError, ValueError) as exc:
            raise MemoryLedgerIntegrityError("research requested source binding is invalid") from exc
        if requested_sources != request["source_refs"] or (
            not isinstance(request["claim_ids"], list)
            or any(not isinstance(value, str) or not value for value in request["claim_ids"])
            or len(set(request["claim_ids"])) != len(request["claim_ids"])
        ):
            raise MemoryLedgerIntegrityError("research requested input binding is invalid")
        if [claim["claim_id"] for claim in claim_inputs] != request["claim_ids"]:
            raise MemoryLedgerIntegrityError("research requested claims do not match pinned inputs")
        expected_source_keys = {
            (value["source_id"], value["version"]) for value in requested_sources
        }
        for claim in claim_inputs:
            if claim.get("research_id") != request["research_id"]:
                raise MemoryLedgerIntegrityError("research pinned claim owner is invalid")
            expected_source_keys.update(
                (value["source_id"], value["version"])
                for value in claim.get("source_refs", [])
            )
        actual_source_keys = [
            (source["source_id"], source["version"]) for source in source_inputs
        ]
        if len(actual_source_keys) != len(set(actual_source_keys)) or set(actual_source_keys) != expected_source_keys:
            raise MemoryLedgerIntegrityError("research pinned source set is invalid")
        attempt_fields = {
            "attempt_id", "status", "started_at", "failure", "recovered_at", "recovered_by",
        }
        for attempt in operation["attempts"]:
            if (
                not isinstance(attempt, dict)
                or set(attempt) != attempt_fields
                or attempt.get("status") != "FAILED"
                or not isinstance(attempt.get("started_at"), str)
                or not attempt["started_at"]
                or attempt.get("failure") is None
            ):
                raise MemoryLedgerIntegrityError("research prior attempt binding is invalid")
            recovered = attempt.get("failure") == "OPERATOR_RECOVERY"
            if recovered != (
                attempt.get("attempt_id") is None
                and isinstance(attempt.get("recovered_at"), str)
                and bool(attempt.get("recovered_at"))
                and isinstance(attempt.get("recovered_by"), str)
                and bool(attempt.get("recovered_by"))
            ):
                raise MemoryLedgerIntegrityError("research prior recovery binding is invalid")
        if status == "RUNNING":
            if not isinstance(operation.get("attempt_id"), str) or not operation["attempt_id"]:
                raise MemoryLedgerIntegrityError("pending research bootstrap operation lacks a durable binding")
            continue
        if status == "FAILED":
            failure = operation.get("failure")
            if failure == "OPERATOR_RECOVERY":
                if (
                    operation.get("attempt_id") is not None
                    or not isinstance(operation.get("recovered_at"), str)
                    or not operation["recovered_at"]
                    or not isinstance(operation.get("recovered_by"), str)
                    or not operation["recovered_by"]
                ):
                    raise MemoryLedgerIntegrityError("research recovery binding is invalid")
            elif not isinstance(failure, dict) or failure.get("phase") != request["action"]:
                raise MemoryLedgerIntegrityError("research failure binding is invalid")
            continue
        result = operation.get("result")
        invocation = operation.get("invocation")
        invocation_fields = {
            "call_id", "phase", "requested_model", "effective_model",
            "provider_returned_model", "context_package_id", "context_hash",
            "project_id", "book_id", "run_id", "step_id", "input_hash",
            "output", "transport",
        }
        if (
            not isinstance(result, dict)
            or set(result) != {"execution_status", "action", "research_id", "claims", "canonical_commit"}
            or result.get("execution_status") != "COMPLETED"
            or result.get("action") != request["action"]
            or result.get("research_id") != request["research_id"]
            or result.get("canonical_commit") is not False
            or not isinstance(result.get("claims"), list)
            or operation.get("result_hash") != digest(result)
            or operation.get("criteria_version") != "RESEARCH_EVIDENCE_V1"
            or operation.get("criteria_hash") != digest(VERIFICATION_CRITERIA)
            or not isinstance(invocation, dict)
            or set(invocation) != invocation_fields
            or invocation.get("call_id") != (
                f"context:{project_id}:{request['run_id']}:{request['step_id']}"
                + ":research:" + digest(operation_id)[:20]
            )
            or invocation.get("phase") != request["action"]
            or invocation.get("input_hash") != digest(inputs)
            or invocation.get("project_id") != project_id
            or invocation.get("book_id") != book_id
            or invocation.get("run_id") != request["run_id"]
            or invocation.get("step_id") != request["step_id"] + ":research:" + digest(operation_id)[:20]
            or invocation.get("effective_model") != request["effective_model"]
            or invocation.get("requested_model") != request["requested_model"]
            or re.fullmatch(r"[0-9a-f]{64}", str(invocation.get("context_hash") or "")) is None
            or not isinstance(invocation.get("context_package_id"), str)
            or not invocation["context_package_id"]
            or not isinstance(invocation.get("transport"), dict)
        ):
            raise MemoryLedgerIntegrityError("research terminal result binding is invalid")
        output = invocation.get("output")
        if request["action"] == "EXTRACT":
            valid_output = (
                isinstance(output, dict)
                and set(output) == {"claims"}
                and isinstance(output["claims"], list)
                and bool(output["claims"])
                and all(
                    isinstance(item, dict)
                    and set(item) == {"claim", "source_refs"}
                    and isinstance(item["claim"], str)
                    and bool(item["claim"].strip())
                    and isinstance(item["source_refs"], list)
                    for item in output["claims"]
                )
            )
        else:
            valid_output = (
                isinstance(output, dict)
                and set(output) == {"evaluations"}
                and isinstance(output["evaluations"], list)
                and {item.get("claim_id") for item in output["evaluations"] if isinstance(item, dict)}
                == set(request["claim_ids"])
                and all(
                    isinstance(item, dict)
                    and set(item) == {
                        "claim_id", "status", "confidence", "reason", "evidence", "contradiction",
                    }
                    for item in output["evaluations"]
                )
            )
        if not valid_output:
            raise MemoryLedgerIntegrityError("research invocation output contract is invalid")
        for claim in result["claims"]:
            try:
                normalized = ResearchClaim.model_validate(claim).model_dump()
            except (TypeError, ValueError) as exc:
                raise MemoryLedgerIntegrityError("research result claim is invalid") from exc
            if normalized != claim or claim not in parsed["claims"].get(claim["claim_id"], []):
                raise MemoryLedgerIntegrityError("research result claim is not durable")
        result_claim_ids = [claim["claim_id"] for claim in result["claims"]]
        if request["action"] == "VERIFY" and set(result_claim_ids) != set(request["claim_ids"]):
            raise MemoryLedgerIntegrityError("research verification result set is incomplete")
        if any(claim["research_id"] != request["research_id"] for claim in result["claims"]):
            raise MemoryLedgerIntegrityError("research result owner binding is invalid")


def _validate_cross_store_bootstrap_metadata(
    parsed: Any, *, operation_id: str, project_id: str, book_id: str,
) -> None:
    from app.p20_core.cross_store_recovery import (
        CrossStoreOperationPlan,
        CrossStoreRecoveryError,
        RECOVERY_RECORD_SCHEMA_VERSION,
        RecoveryStatus,
    )

    fields = {
        "schema_version", "operation_id", "scope", "owner_store", "operation_type",
        "input_hash", "expected_versions", "planned_writes", "completed_writes", "status",
        "retry_count", "last_error_class", "created_at", "updated_at",
        "logical_committed_at", "committed_at", "provenance_refs", "audit_refs",
        "lineage", "payload",
    }
    if not isinstance(parsed, dict) or set(parsed) != fields:
        raise MemoryLedgerIntegrityError("cross-store bootstrap fields are invalid")
    try:
        plan = CrossStoreOperationPlan.from_record(parsed)
    except (CrossStoreRecoveryError, KeyError, TypeError, ValueError) as exc:
        raise MemoryLedgerIntegrityError("cross-store bootstrap payload is invalid") from exc
    scope = parsed.get("scope")
    expected_scope = {
        "type": "PROJECT", "project_id": project_id, "book_id": book_id,
        "series_id": plan.series_id,
    }
    expected_planned = ["PROJECT_CHECKPOINT", "FILE_ARTIFACT"]
    if plan.series_snapshot is not None:
        expected_planned.append("SERIES_VOLUME_CLOSE")
    expected_planned.extend(("LOGICAL_COMMIT", "FINAL_AUDIT"))
    status = parsed.get("status")
    completed = parsed.get("completed_writes")
    if (
        parsed.get("schema_version") != RECOVERY_RECORD_SCHEMA_VERSION
        or parsed.get("operation_id") != operation_id
        or parsed.get("owner_store") != "project.db"
        or parsed.get("operation_type") != plan.operation_type
        or parsed.get("input_hash") != plan.input_hash
        or parsed.get("expected_versions") != dict(plan.expected_versions)
        or parsed.get("provenance_refs") != list(plan.provenance_refs)
        or parsed.get("payload") != plan.durable_payload()
        or scope != expected_scope
        or parsed.get("planned_writes") != expected_planned
        or not isinstance(completed, list)
        or completed != expected_planned[:len(completed)]
        or status not in {item.value for item in RecoveryStatus}
        or isinstance(parsed.get("retry_count"), bool)
        or not isinstance(parsed.get("retry_count"), int)
        or parsed["retry_count"] < 0
        or parsed.get("audit_refs") != [
            "cross_store_operation.v1:" + operation_id,
            "cross_store_audit.v1:" + operation_id,
        ]
        or not all(isinstance(parsed.get(name), str) and parsed[name] for name in (
            "created_at", "updated_at",
        ))
        or parsed.get("last_error_class") is not None
           and (not isinstance(parsed["last_error_class"], str) or not parsed["last_error_class"])
    ):
        raise MemoryLedgerIntegrityError("cross-store bootstrap binding is invalid")
    if status == RecoveryStatus.PENDING.value and completed:
        raise MemoryLedgerIntegrityError("pending cross-store operation has completed writes")
    if status == RecoveryStatus.AUDIT_PENDING.value and completed != expected_planned[:-1]:
        raise MemoryLedgerIntegrityError("cross-store audit-pending state is invalid")
    if status == RecoveryStatus.COMMITTED.value and completed != expected_planned:
        raise MemoryLedgerIntegrityError("committed cross-store state is incomplete")
    logical_complete = "LOGICAL_COMMIT" in completed
    final_complete = "FINAL_AUDIT" in completed
    if logical_complete:
        if not isinstance(parsed.get("logical_committed_at"), str) or not parsed["logical_committed_at"]:
            raise MemoryLedgerIntegrityError("cross-store logical commit time is missing")
    elif parsed.get("logical_committed_at") is not None:
        raise MemoryLedgerIntegrityError("cross-store logical commit time is premature")
    if final_complete:
        if not isinstance(parsed.get("committed_at"), str) or not parsed["committed_at"]:
            raise MemoryLedgerIntegrityError("cross-store commit time is missing")
    elif parsed.get("committed_at") is not None:
        raise MemoryLedgerIntegrityError("cross-store commit time is premature")
    lineage = parsed.get("lineage")
    allowed_kinds = {
        "INITIAL", "TECHNICAL_RETRY", "RECOVERY", "FINAL_AUDIT", "ERROR",
        "MANUAL_ESCALATION_REQUIRED",
    }
    if not isinstance(lineage, list) or not lineage:
        raise MemoryLedgerIntegrityError("cross-store lineage is invalid")
    for ordinal, entry in enumerate(lineage, start=1):
        if (
            not isinstance(entry, dict)
            or set(entry) not in ({"sequence", "kind", "at", "resume_from"},
                                  {"sequence", "kind", "at", "resume_from", "error_class"})
            or entry.get("sequence") != ordinal
            or entry.get("kind") not in allowed_kinds
            or not isinstance(entry.get("at"), str)
            or not entry["at"]
            or not isinstance(entry.get("resume_from"), list)
            or entry["resume_from"] != expected_planned[:len(entry["resume_from"])]
            or any(step not in completed for step in entry["resume_from"])
            or ("error_class" in entry and (
                not isinstance(entry["error_class"], str) or not entry["error_class"]
            ))
        ):
            raise MemoryLedgerIntegrityError("cross-store lineage binding is invalid")
    if lineage[0]["kind"] != "INITIAL":
        raise MemoryLedgerIntegrityError("cross-store lineage origin is invalid")
    retry_events = sum(
        entry["kind"] in {"TECHNICAL_RETRY", "RECOVERY"} for entry in lineage
    )
    if parsed["retry_count"] != retry_events:
        raise MemoryLedgerIntegrityError("cross-store retry count is not bound to lineage")
    if status == RecoveryStatus.NEEDS_INTERVENTION.value and (
        parsed.get("last_error_class") is None
        or lineage[-1]["kind"] != "MANUAL_ESCALATION_REQUIRED"
        or lineage[-1].get("error_class") != parsed.get("last_error_class")
    ):
        raise MemoryLedgerIntegrityError("cross-store intervention evidence is incomplete")
    final_indices = [index for index, entry in enumerate(lineage) if entry["kind"] == "FINAL_AUDIT"]
    if status == RecoveryStatus.COMMITTED.value and final_indices != [len(lineage) - 1]:
        raise MemoryLedgerIntegrityError("committed cross-store lineage is invalid")
    if status == RecoveryStatus.NEEDS_INTERVENTION.value and parsed.get("committed_at") is not None:
        if len(final_indices) != 1 or final_indices[0] >= len(lineage) - 1:
            raise MemoryLedgerIntegrityError("post-commit intervention lineage is invalid")


def _validate_cross_store_audit_binding(operation: dict[str, Any], audit: Any) -> None:
    from app.p20_core.cross_store_recovery import CrossStoreRecoveryService, RecoveryStatus

    if not isinstance(audit, dict):
        raise MemoryLedgerIntegrityError("cross-store bootstrap audit is invalid")
    status = operation.get("status")
    if status == RecoveryStatus.COMMITTED.value:
        if audit != CrossStoreRecoveryService._audit_document(operation):
            raise MemoryLedgerIntegrityError("cross-store bootstrap audit binding is invalid")
        return
    if status == RecoveryStatus.NEEDS_INTERVENTION.value and operation.get("committed_at") is not None:
        immutable = {
            "schema_version", "operation_id", "operation_type", "scope", "owner_store",
            "input_hash", "expected_versions", "planned_writes", "completed_writes",
            "created_at", "logical_committed_at", "committed_at", "provenance_refs", "audit_refs",
        }
        if any(audit.get(name) != operation.get(name) for name in immutable):
            raise MemoryLedgerIntegrityError("post-commit cross-store audit binding is invalid")
        if (
            audit.get("status") != RecoveryStatus.COMMITTED.value
            or not isinstance(audit.get("lineage"), list)
            or audit["lineage"] != operation["lineage"][:len(audit["lineage"])]
            or not audit["lineage"]
            or audit["lineage"][-1].get("kind") != "FINAL_AUDIT"
        ):
            raise MemoryLedgerIntegrityError("post-commit cross-store audit state is invalid")
        return
    raise MemoryLedgerIntegrityError("pre-commit cross-store operation has an audit")


def _validate_canonical_proposal_bootstrap_metadata(
    conn: sqlite3.Connection,
    parsed: Any,
    *,
    proposal_id: str,
    scope_type: str,
    scope_id: str,
    project_id: str | None,
    book_id: str | None,
) -> None:
    from app.p20_core.canon_service import canonical_proposal_hash
    from app.p20_core.domain_records import DomainId
    from app.p20_core.memory_extraction import (
        SUPPORTED_MEMORY_RECORD_TYPES,
    )

    if not isinstance(parsed, dict) or set(parsed) != {"current_version", "versions"}:
        raise MemoryLedgerIntegrityError("canonical proposal bootstrap document is invalid")
    versions = parsed.get("versions")
    if not isinstance(versions, dict) or not versions:
        raise MemoryLedgerIntegrityError("canonical proposal bootstrap versions are invalid")
    ordered = sorted(versions, key=lambda value: int(value) if re.fullmatch(r"[1-9][0-9]*", value) else -1)
    if ordered != [str(number) for number in range(1, len(versions) + 1)]:
        raise MemoryLedgerIntegrityError("canonical proposal versions are not sequential")
    if parsed.get("current_version") != ordered[-1]:
        raise MemoryLedgerIntegrityError("canonical proposal current version is invalid")
    if scope_type == "SERIES":
        first_record = versions[ordered[0]]
        first_proposal = first_record.get("proposal") if isinstance(first_record, dict) else None
        if not isinstance(first_proposal, dict):
            raise MemoryLedgerIntegrityError("canonical proposal bootstrap binding is invalid")
        project_id = first_proposal.get("project_id")
        book_id = first_proposal.get("book_id")
        if (
            not isinstance(project_id, str)
            or not project_id.strip()
            or not isinstance(book_id, str)
            or not book_id.strip()
        ):
            raise MemoryLedgerIntegrityError("series proposal initiating identity is missing")
        membership = conn.execute(
            "SELECT book_id FROM series_memberships WHERE project_id=?", (project_id,)
        ).fetchone()
        if membership is None or str(membership["book_id"]) != book_id:
            raise MemoryLedgerIntegrityError("series proposal initiating identity is invalid")
    common_fields = {
        "contract_version", "proposal_id", "project_id", "book_id", "series_id", "scope_type",
        "scope_id", "run_id", "step_id", "context_package_id", "context_hash",
        "proposed_mutations", "source", "actor_ref", "authority_ref", "policy_ref",
        "proposal_version", "proposal_hash", "status", "created_at",
    }
    artifact_fields = {
        "source_artifact_id", "source_artifact_ref", "source_artifact_hash",
        "source_artifact_version", "source_scene_id", "extraction_candidate_set_id",
        "extraction_candidate_hash", "verification_ref",
    }
    research_fields = {"source_kind", "research_evidence", "fiction_decision"}
    allowed_record_fields = {
        "proposal", "basis_hash", "challenges", "decision", "pipeline",
        "research_request_hash", "impact", "initial_guard", "final_guard", "receipt",
        "failure", "superseded", "memory_ledger_status",
    }
    statuses = {
        "AWAITING_USER_APPROVAL", "APPROVED_FOR_COMMIT", "READY_FOR_ANALYSIS",
        "COMMITTED", "STALE", "REJECTED", "FAILED",
    }
    for version in ordered:
        record = versions[version]
        if (
            not isinstance(record, dict)
            or set(record) - allowed_record_fields
            or not {"proposal", "basis_hash", "challenges", "decision"}.issubset(record)
            or not isinstance(record.get("challenges"), dict)
        ):
            raise MemoryLedgerIntegrityError("canonical proposal lifecycle record is invalid")
        proposal = record.get("proposal")
        if not isinstance(proposal, dict):
            raise MemoryLedgerIntegrityError("canonical proposal bootstrap binding is invalid")
        research = proposal.get("contract_version") == "2.0" and proposal.get("source_kind") == "RESEARCH"
        expected_fields = common_fields | (research_fields if research else artifact_fields)
        if (
            set(proposal) != expected_fields
            or proposal.get("contract_version") != ("2.0" if research else "1.0")
            or proposal.get("proposal_id") != proposal_id
            or proposal.get("proposal_version") != int(version)
            or proposal.get("scope_type") != scope_type
            or proposal.get("scope_id") != scope_id
            or proposal.get("project_id") != project_id
            or proposal.get("book_id") != book_id
            or proposal.get("status") not in statuses
            or canonical_proposal_hash(proposal) != proposal.get("proposal_hash")
            or re.fullmatch(r"[0-9a-f]{64}", str(record.get("basis_hash") or "")) is None
            or re.fullmatch(r"[0-9a-f]{64}", str(proposal.get("context_hash") or "")) is None
            or not isinstance(proposal.get("proposed_mutations"), list)
            or not proposal["proposed_mutations"]
        ):
            raise MemoryLedgerIntegrityError("canonical proposal semantic binding is invalid")
        if scope_type == "SERIES" and proposal.get("series_id") != scope_id:
            raise MemoryLedgerIntegrityError("canonical proposal series binding is invalid")
        if scope_type == "PROJECT" and proposal.get("series_id") is not None and (
            not isinstance(proposal["series_id"], str) or not proposal["series_id"]
        ):
            raise MemoryLedgerIntegrityError("canonical proposal series identity is invalid")
        text_fields = {
            "proposal_id", "project_id", "book_id", "scope_type", "scope_id",
            "run_id", "step_id", "context_package_id", "source", "actor_ref",
            "authority_ref", "policy_ref", "created_at",
        }
        if any(not isinstance(proposal.get(name), str) or not proposal[name].strip() for name in text_fields):
            raise MemoryLedgerIntegrityError("canonical proposal required text is invalid")
        if research:
            if (
                scope_type != "PROJECT"
                or proposal.get("authority_ref") != "P20_VERIFIED_RESEARCH_V1"
                or re.fullmatch(r"[0-9a-f]{64}", str(record.get("research_request_hash") or "")) is None
            ):
                raise MemoryLedgerIntegrityError("research proposal authority binding is invalid")
        else:
            for name in ("source_artifact_hash", "extraction_candidate_hash"):
                if re.fullmatch(r"[0-9a-f]{64}", str(proposal.get(name) or "")) is None:
                    raise MemoryLedgerIntegrityError("canonical proposal source hash is invalid")
            if type(proposal.get("source_artifact_version")) is not int or proposal["source_artifact_version"] < 1:
                raise MemoryLedgerIntegrityError("canonical proposal source version is invalid")
            verification_ref = proposal.get("verification_ref")
            if (
                not isinstance(verification_ref, dict)
                or set(verification_ref) != {"id", "hash"}
                or not isinstance(verification_ref["id"], str)
                or not verification_ref["id"]
                or re.fullmatch(r"[0-9a-f]{64}", str(verification_ref["hash"] or "")) is None
            ):
                raise MemoryLedgerIntegrityError("canonical proposal verification binding is invalid")
        mutation_keys = {
            "target_entity_type", "target_entity_id", "operation_type",
            "expected_current_version", "expected_current_hash", "proposed_state",
            "provenance",
        }
        identities: list[tuple[str, str, str]] = []
        for mutation in proposal["proposed_mutations"]:
            if not isinstance(mutation, dict) or set(mutation) != mutation_keys:
                raise MemoryLedgerIntegrityError("canonical proposal mutation is invalid")
            record_type = mutation.get("target_entity_type")
            identity = mutation.get("target_entity_id")
            operation = mutation.get("operation_type")
            namespace = {
                "CHARACTER_STATE": "CONTEXT", "KNOWLEDGE_EVENT": "KNOWLEDGE",
                "RELATIONSHIP_CHANGE": "RELATIONSHIP",
            }.get(record_type, record_type)
            identity_fields = {
                "FACT": "fact_id", "CHARACTER_STATE": "state_id",
                "EVENT": "event_id", "KNOWLEDGE_EVENT": "knowledge_event_id",
                "THREAD": "thread_id", "SETUP": "setup_id", "PAYOFF": "payoff_id",
                "RELATIONSHIP_CHANGE": "change_id",
            }
            try:
                parsed_id = DomainId.parse(identity)
                identity_field = identity_fields[record_type]
            except (KeyError, TypeError, ValueError) as exc:
                raise MemoryLedgerIntegrityError("canonical proposal mutation state is invalid") from exc
            canonical_state = mutation["proposed_state"]
            if (
                record_type not in SUPPORTED_MEMORY_RECORD_TYPES
                or parsed_id.namespace.name != namespace
                or operation not in {"CREATE", "UPDATE", "REPLACE"}
                or not isinstance(canonical_state, dict)
                or canonical_state.get(identity_field) != identity
                or canonical_state.get("project_id") != project_id
                or not isinstance(mutation["provenance"], dict)
                or not mutation["provenance"]
                or any(type(canonical_state.get(flag)) is not bool for flag in ("frozen", "author_locked"))
            ):
                raise MemoryLedgerIntegrityError("canonical proposal mutation binding is invalid")
            current_version = mutation["expected_current_version"]
            current_hash = mutation["expected_current_hash"]
            if operation == "CREATE":
                valid_base = current_version is None and current_hash is None and canonical_state["version"] == 1
            else:
                valid_base = (
                    type(current_version) is int
                    and current_version >= 1
                    and re.fullmatch(r"[0-9a-f]{64}", str(current_hash or "")) is not None
                    and canonical_state["version"] == current_version + 1
                )
            if not valid_base:
                raise MemoryLedgerIntegrityError("canonical proposal mutation version binding is invalid")
            identities.append((record_type, identity, operation))
        if identities != sorted(identities) or len({item[:2] for item in identities}) != len(identities):
            raise MemoryLedgerIntegrityError("canonical proposal mutations are duplicate or unsorted")
        binding = {
            name: proposal[name]
            for name in ("proposal_id", "proposal_hash", "project_id", "scope_type", "scope_id")
        }
        for evidence_name in ("impact", "initial_guard", "final_guard"):
            evidence = record.get(evidence_name)
            if evidence is not None and (
                not isinstance(evidence, dict)
                or any(evidence.get(name) != value for name, value in binding.items())
            ):
                raise MemoryLedgerIntegrityError(
                    f"canonical proposal {evidence_name} binding is invalid"
                )
        for challenge_id, challenge in record["challenges"].items():
            if (
                not isinstance(challenge, dict)
                or challenge.get("challenge_id") != challenge_id
                or challenge.get("proposal_hash") != proposal["proposal_hash"]
                or challenge.get("basis_hash") != record["basis_hash"]
                or not isinstance(challenge.get("used"), bool)
            ):
                raise MemoryLedgerIntegrityError("canonical proposal challenge binding is invalid")
        decision = record.get("decision")
        if decision is not None:
            challenge = record["challenges"].get(decision.get("challenge_id") if isinstance(decision, dict) else None)
            if (
                not isinstance(decision, dict)
                or challenge is None
                or challenge.get("used") is not True
                or decision.get("proposal_id") != proposal_id
                or decision.get("proposal_hash") != proposal["proposal_hash"]
                or decision.get("scope_type") != scope_type
                or decision.get("scope_id") != scope_id
                or decision.get("decision") not in {"APPROVE", "REJECT"}
                or not isinstance(decision.get("authorization_ref"), str)
                or not decision["authorization_ref"]
            ):
                raise MemoryLedgerIntegrityError("canonical proposal decision binding is invalid")
        if version != ordered[-1] and record.get("superseded") is not True:
            legitimate_research_history = research and proposal["status"] in {"STALE", "COMMITTED"}
            if not legitimate_research_history:
                raise MemoryLedgerIntegrityError("canonical proposal historical version is not superseded")
        if proposal["status"] == "COMMITTED" and not isinstance(record.get("receipt"), dict):
            raise MemoryLedgerIntegrityError("committed proposal receipt is missing")


def _validate_canonical_commit_binding(
    *, operation_id: str, commit: Any, parsed_rows: dict[str, Any],
) -> None:
    if not isinstance(commit, dict):
        raise MemoryLedgerIntegrityError("canonical commit bootstrap document is invalid")
    fields = {
        "status", "canonical_commit", "proposal_id", "proposal_hash", "project_id",
        "scope_type", "scope_id", "run_id", "step_id", "operation_id",
        "resulting_versions", "authorization_ref", "impact_id", "guard", "invalidation",
        "created_at",
    }
    if set(commit) != fields or commit.get("operation_id") != operation_id:
        raise MemoryLedgerIntegrityError("canonical commit bootstrap fields are invalid")
    expected_operation = hashlib.sha256(json.dumps(
        {name: commit[name] for name in (
            "project_id", "scope_type", "scope_id", "proposal_id", "proposal_hash",
        )},
        sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    proposal_document = parsed_rows.get("canonical_proposal.v1:" + str(commit.get("proposal_id") or ""))
    if not isinstance(proposal_document, dict):
        raise MemoryLedgerIntegrityError("canonical commit proposal is unavailable")
    matches = []
    for record in proposal_document.get("versions", {}).values():
        if isinstance(record, dict) and record.get("receipt") == commit:
            matches.append(record)
    if (
        commit.get("status") != "COMMITTED"
        or commit.get("canonical_commit") is not True
        or operation_id != expected_operation
        or re.fullmatch(r"[0-9a-f]{64}", str(commit.get("proposal_hash") or "")) is None
        or not isinstance(commit.get("resulting_versions"), dict)
        or not commit["resulting_versions"]
        or not isinstance(commit.get("guard"), dict)
        or commit["guard"].get("outcome") != "ALLOW"
        or not isinstance(commit.get("invalidation"), dict)
        or len(matches) != 1
        or matches[0]["proposal"].get("status") != "COMMITTED"
        or matches[0]["proposal"].get("proposal_hash") != commit["proposal_hash"]
        or matches[0].get("impact", {}).get("impact_id") != commit.get("impact_id")
        or matches[0].get("final_guard") != commit.get("guard")
    ):
        raise MemoryLedgerIntegrityError("canonical commit bootstrap binding is invalid")
    proposal = matches[0]["proposal"]
    expected_versions = {
        mutation["target_entity_id"]: mutation["proposed_state"]["version"]
        for mutation in proposal["proposed_mutations"]
    }
    decision = matches[0].get("decision")
    expected_authorization = None if decision is None else decision.get("authorization_ref")
    if (
        commit.get("proposal_id") != proposal.get("proposal_id")
        or commit.get("project_id") != proposal.get("project_id")
        or commit.get("scope_type") != proposal.get("scope_type")
        or commit.get("scope_id") != proposal.get("scope_id")
        or commit.get("run_id") != proposal.get("run_id")
        or commit.get("step_id") != proposal.get("step_id")
        or commit.get("resulting_versions") != expected_versions
        or commit.get("authorization_ref") != expected_authorization
    ):
        raise MemoryLedgerIntegrityError("canonical commit result binding is invalid")


def _validate_research_proposal_bootstrap_binding(
    *, proposal: dict[str, Any], record: dict[str, Any], research_state: Any,
) -> None:
    """Validate historical research provenance without requiring it to be latest."""
    from app.p20_core.research import (
        ResearchError,
        digest,
        validate_promotion_evidence,
        verified_evidence,
    )

    evidence_ref = proposal.get("research_evidence")
    if (
        not isinstance(research_state, dict)
        or not isinstance(evidence_ref, dict)
        or set(evidence_ref) != {"operation_id", "hash"}
        or not isinstance(evidence_ref.get("operation_id"), str)
        or re.fullmatch(r"[0-9a-f]{64}", str(evidence_ref.get("hash") or "")) is None
    ):
        raise MemoryLedgerIntegrityError("research proposal evidence reference is invalid")
    operation = research_state.get("operations", {}).get(evidence_ref["operation_id"])
    if not isinstance(operation, dict) or operation.get("status") != "COMPLETED":
        raise MemoryLedgerIntegrityError("research proposal operation is unavailable")

    # verified_evidence intentionally rejects stale evidence at runtime.  A
    # bootstrap must still preserve and prove older proposal versions, so make
    # a historical view capped at the exact versions pinned by this operation.
    historical = dict(research_state)
    historical["claims"] = dict(research_state.get("claims", {}))
    historical["sources"] = dict(research_state.get("sources", {}))
    try:
        result_claims = operation["result"]["claims"]
        for claim in result_claims:
            claim_id, version = claim["claim_id"], claim["version"]
            historical["claims"][claim_id] = [
                item for item in research_state["claims"][claim_id]
                if item["version"] <= version
            ]
        for source in operation["inputs"]["sources"]:
            source_id, version = source["source_id"], source["version"]
            historical["sources"][source_id] = [
                item for item in research_state["sources"][source_id]
                if item["version"] <= version
            ]
        validate_promotion_evidence(proposal, historical)
        evidence = verified_evidence(historical, evidence_ref["operation_id"])
    except (KeyError, TypeError, ValueError, ResearchError) as exc:
        raise MemoryLedgerIntegrityError("research proposal evidence binding is invalid") from exc

    targets = {
        mutation["provenance"]["claim_id"]: mutation["target_entity_id"]
        for mutation in proposal["proposed_mutations"]
    }
    expected_request_hash = digest({
        "operation_id": evidence_ref["operation_id"],
        "targets": targets,
        "fiction": proposal.get("fiction_decision"),
        "evidence": digest(evidence),
    })
    if (
        evidence_ref["hash"] != digest(evidence)
        or record.get("research_request_hash") != expected_request_hash
    ):
        raise MemoryLedgerIntegrityError("research proposal request binding is invalid")


def _validated_bootstrap_metadata_rows(
    conn: sqlite3.Connection, *, metadata_table: str, scope_type: str,
) -> tuple[sqlite3.Row, ...]:
    prefixes = (
        _PROJECT_LEDGER_METADATA_PREFIXES if scope_type == "PROJECT"
        else _SERIES_LEDGER_METADATA_PREFIXES
    )
    if scope_type == "PROJECT":
        identity = conn.execute(
            "SELECT project_id,book_id FROM project_identity WHERE id=1"
        ).fetchone()
        if identity is None:
            raise MemoryLedgerIntegrityError("project bootstrap identity is missing")
        scope_id = str(identity["project_id"])
        project_id, book_id = scope_id, str(identity["book_id"])
    else:
        identity = conn.execute(
            "SELECT series_id FROM series_identity WHERE id=1"
        ).fetchone()
        if identity is None:
            raise MemoryLedgerIntegrityError("series bootstrap identity is missing")
        scope_id = str(identity["series_id"])
        project_id = book_id = None

    def required_text(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise MemoryLedgerIntegrityError(f"bootstrap {label} binding is missing")
        return value

    def required_hash(value: Any, label: str) -> str:
        text = required_text(value, label)
        if re.fullmatch(r"[0-9a-f]{64}", text) is None:
            raise MemoryLedgerIntegrityError(f"bootstrap {label} binding is invalid")
        return text

    rows = conn.execute(f"SELECT key,value FROM {metadata_table} ORDER BY key").fetchall()
    parsed_rows: dict[str, Any] = {}
    selected: list[sqlite3.Row] = []
    for row in rows:
        key, raw = str(row["key"]), str(row["value"])
        if key == MEMORY_LEDGER_CONTROL_KEY:
            continue
        covered = (
            (scope_type == "PROJECT" and key == "research.v1")
            or any(key.startswith(prefix) for prefix in prefixes)
        )
        if not covered:
            if scope_type == "PROJECT" and any(
                key.startswith(prefix) and len(key) > len(prefix)
                for prefix in _PROJECT_OUT_OF_COVERAGE_METADATA_PREFIXES
            ):
                continue
            raise MemoryLedgerIntegrityError(f"bootstrap metadata type is unsupported: {key}")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MemoryLedgerIntegrityError(f"bootstrap metadata is invalid JSON: {key}") from exc
        if not isinstance(parsed, (dict, list)):
            raise MemoryLedgerIntegrityError(f"bootstrap metadata structure is unsupported: {key}")
        if _contains_secret_field(parsed):
            raise MemoryLedgerIntegrityError(f"bootstrap metadata contains a secret field: {key}")
        parsed_rows[key] = parsed
        if key == "research.v1":
            _validate_research_bootstrap_metadata(
                parsed, project_id=str(project_id), book_id=str(book_id),
            )
        elif key.startswith("canonical_versions.v1:"):
            _validate_canonical_versions_metadata(
                conn,
                key=key,
                parsed=parsed,
                scope_type=scope_type,
                scope_id=scope_id,
                project_id=project_id,
            )
        elif key.startswith("canonical_pipeline.v1:"):
            _validate_canonical_pipeline_metadata(
                conn,
                key=key,
                parsed=parsed,
                scope_type=scope_type,
                scope_id=scope_id,
                project_id=project_id,
                book_id=book_id,
            )
        elif key.startswith("canonical_proposal.v1:"):
            proposal_id = required_text(key.removeprefix("canonical_proposal.v1:"), "proposal id")
            _validate_canonical_proposal_bootstrap_metadata(
                conn,
                parsed,
                proposal_id=proposal_id,
                scope_type=scope_type,
                scope_id=scope_id,
                project_id=project_id,
                book_id=book_id,
            )
        elif key.startswith("canonical_commit.v1:"):
            operation_id = required_text(key.removeprefix("canonical_commit.v1:"), "commit operation id")
            if not isinstance(parsed, dict) or parsed.get("operation_id") != operation_id:
                raise MemoryLedgerIntegrityError("canonical commit bootstrap binding is invalid")
        elif key.startswith("cross_store_operation.v1:"):
            operation_id = required_text(key.removeprefix("cross_store_operation.v1:"), "cross-store operation id")
            if scope_type != "PROJECT":
                raise MemoryLedgerIntegrityError("cross-store metadata is PROJECT-only")
            _validate_cross_store_bootstrap_metadata(
                parsed,
                operation_id=operation_id,
                project_id=str(project_id),
                book_id=str(book_id),
            )
        selected.append(row)

    for key, parsed in parsed_rows.items():
        if key.startswith("canonical_commit.v1:"):
            _validate_canonical_commit_binding(
                operation_id=key.removeprefix("canonical_commit.v1:"),
                commit=parsed,
                parsed_rows=parsed_rows,
            )
        if not key.startswith("cross_store_audit.v1:"):
            continue
        operation_id = required_text(key.removeprefix("cross_store_audit.v1:"), "cross-store audit operation id")
        operation = parsed_rows.get("cross_store_operation.v1:" + operation_id)
        if not isinstance(operation, dict):
            raise MemoryLedgerIntegrityError("cross-store bootstrap audit is orphaned")
        _validate_cross_store_audit_binding(operation, parsed)
    for key, operation in parsed_rows.items():
        if not key.startswith("cross_store_operation.v1:"):
            continue
        operation_id = key.removeprefix("cross_store_operation.v1:")
        audit_present = "cross_store_audit.v1:" + operation_id in parsed_rows
        post_commit = operation.get("status") == "COMMITTED" or (
            operation.get("status") == "NEEDS_INTERVENTION"
            and operation.get("committed_at") is not None
        )
        if post_commit != audit_present:
            raise MemoryLedgerIntegrityError("cross-store bootstrap audit presence is invalid")
    for key, document in parsed_rows.items():
        if not key.startswith("canonical_proposal.v1:") or not isinstance(document, dict):
            continue
        for record in document.get("versions", {}).values():
            if not isinstance(record, dict):
                continue
            proposal = record.get("proposal", {})
            if proposal.get("source_kind") == "RESEARCH":
                _validate_research_proposal_bootstrap_binding(
                    proposal=proposal,
                    record=record,
                    research_state=parsed_rows.get("research.v1"),
                )
            if proposal.get("status") != "COMMITTED":
                continue
            receipt = record.get("receipt")
            operation_id = receipt.get("operation_id") if isinstance(receipt, dict) else None
            if (
                not isinstance(operation_id, str)
                or parsed_rows.get("canonical_commit.v1:" + operation_id) != receipt
            ):
                raise MemoryLedgerIntegrityError("committed proposal audit is unavailable")
    return tuple(selected)


def _bootstrap_table_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    condition: str | None = None,
    parameters: tuple[Any, ...] = (),
) -> tuple[tuple[sqlite3.Row, str, str], ...]:
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if not info:
        raise MemoryLedgerIntegrityError(f"bootstrap table is missing: {table}")
    primary_key = [
        str(row[1]) for row in sorted(info, key=lambda row: int(row[5]) if int(row[5]) else 1_000_000)
        if int(row[5]) > 0
    ]
    if not primary_key:
        raise MemoryLedgerIntegrityError(f"bootstrap table has no stable primary key: {table}")
    order_by = ",".join('"' + name.replace('"', '""') + '"' for name in primary_key)
    where = "" if condition is None else " WHERE " + condition
    rows = conn.execute(f"SELECT * FROM {table}{where} ORDER BY {order_by}", parameters).fetchall()
    result: list[tuple[sqlite3.Row, str, str]] = []
    for row in rows:
        snapshot = {key: row[key] for key in row.keys()}
        if any(value is not None and (isinstance(value, (bytes, float)) or isinstance(value, bool))
               for value in snapshot.values()):
            raise MemoryLedgerIntegrityError(f"bootstrap row has unsupported value type: {table}")
        key_data = {name: row[name] for name in primary_key}
        locator = f"table:{table}:key:" + _ledger_canonical_json(key_data)
        raw = _ledger_canonical_json(snapshot)
        result.append((row, locator, raw))
    return tuple(result)


def _memory_ledger_bootstrap_preflight(
    conn: sqlite3.Connection,
    *,
    scope_type: str,
    scope_id: str,
    project_id: str | None = None,
    book_id: str | None = None,
) -> None:
    from app.p20_core.domain_records import DomainId, DomainNamespace

    if scope_type == "PROJECT":
        _validate_project_schema_v7(conn)
        preflight_metadata_table = "project_metadata"
    elif scope_type == "SERIES":
        _validate_series_schema_v4(conn)
        preflight_metadata_table = "series_metadata"
    else:
        raise MemoryLedgerIntegrityError("bootstrap scope is invalid")
    control = _read_validated_memory_ledger_control(
        conn, metadata_table=preflight_metadata_table,
    )
    if control["state"] != "SCHEMA_READY":
        raise MemoryLedgerIntegrityError("bootstrap requires SCHEMA_READY control")
    if int(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]) != 0:
        raise MemoryLedgerIntegrityError("schema-ready ledger contains partial events")
    if int(conn.execute("SELECT COUNT(*) FROM memory_event_entities").fetchone()[0]) != 0:
        raise MemoryLedgerIntegrityError("schema-ready ledger contains partial entity indexes")

    integrity = conn.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or str(integrity[0]).lower() != "ok":
        raise MemoryLedgerIntegrityError("bootstrap source integrity check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise MemoryLedgerIntegrityError("bootstrap source foreign key check failed")
    if scope_type == "PROJECT":
        identity = conn.execute(
            "SELECT project_id,book_id,schema_version FROM project_identity WHERE id=1"
        ).fetchone()
        if (
            identity is None
            or str(identity["project_id"]) != scope_id
            or str(identity["project_id"]) != project_id
            or str(identity["book_id"]) != book_id
            or int(identity["schema_version"]) != PROJECT_DB_SCHEMA_VERSION
        ):
            raise MemoryLedgerIntegrityError("project bootstrap identity mismatch")
        try:
            DomainId.parse(scope_id).require_namespace(DomainNamespace.PROJECT, "project_id")
            DomainId.parse(str(book_id)).require_namespace(DomainNamespace.BOOK, "book_id")
        except ValueError as exc:
            raise MemoryLedgerIntegrityError("project bootstrap domain identity is invalid") from exc
        sources = (
            ("project_structured_memory_records", "scope_id = ?", (scope_id,)),
            ("project_fact_records", "scope_id = ?", (scope_id,)),
            ("project_character_states", "scope_id = ?", (scope_id,)),
            ("edges", "scope_id = ?", (scope_id,)),
        )
        for table in ("project_structured_memory_records", "project_fact_records", "project_character_states", "edges"):
            mismatch = conn.execute(
                f"SELECT 1 FROM {table} WHERE scope_type != 'PROJECT' OR scope_id != ? LIMIT 1",
                (scope_id,),
            ).fetchone()
            if mismatch is not None:
                raise MemoryLedgerIntegrityError(f"project bootstrap scope mismatch: {table}")
        metadata_table = "project_metadata"
    elif scope_type == "SERIES":
        identity = conn.execute(
            "SELECT series_id,schema_version FROM series_identity WHERE id=1"
        ).fetchone()
        if (
            identity is None
            or str(identity["series_id"]) != scope_id
            or int(identity["schema_version"]) != SERIES_DB_SCHEMA_VERSION
        ):
            raise MemoryLedgerIntegrityError("series bootstrap identity mismatch")
        try:
            DomainId.parse(scope_id).require_namespace(DomainNamespace.SERIES, "series_id")
        except ValueError as exc:
            raise MemoryLedgerIntegrityError("series bootstrap domain identity is invalid") from exc
        sources = tuple((table, None, ()) for table in (
            "series_state_records", "edges", "volume_closing_snapshots",
            "series_memberships", "series_operations",
        ))
        for table in ("series_state_records", "edges", "volume_closing_snapshots"):
            mismatch = conn.execute(
                f"SELECT 1 FROM {table} WHERE scope_type != 'SERIES' OR scope_id != ? LIMIT 1",
                (scope_id,),
            ).fetchone()
            if mismatch is not None:
                raise MemoryLedgerIntegrityError(f"series bootstrap scope mismatch: {table}")
        memberships = conn.execute(
            "SELECT project_id,book_id,payload_json FROM series_memberships ORDER BY project_id"
        ).fetchall()
        for membership in memberships:
            try:
                DomainId.parse(str(membership["project_id"])).require_namespace(
                    DomainNamespace.PROJECT, "project_id"
                )
                DomainId.parse(str(membership["book_id"])).require_namespace(
                    DomainNamespace.BOOK, "book_id"
                )
                payload = json.loads(str(membership["payload_json"]))
            except (ValueError, json.JSONDecodeError) as exc:
                raise MemoryLedgerIntegrityError("series bootstrap membership is invalid") from exc
            if (
                not isinstance(payload, dict)
                or payload.get("series_id") != scope_id
                or payload.get("project_id") != membership["project_id"]
                or payload.get("book_id") != membership["book_id"]
            ):
                raise MemoryLedgerIntegrityError("series bootstrap membership binding is invalid")
        metadata_table = "series_metadata"
    else:
        raise MemoryLedgerIntegrityError("bootstrap scope is invalid")
    for table, condition, parameters in sources:
        _bootstrap_table_rows(
            conn, table=table, condition=condition, parameters=parameters,
        )
    _validated_bootstrap_metadata_rows(
        conn, metadata_table=metadata_table, scope_type=scope_type,
    )


class ScopeType(str, Enum):
    PROJECT = "PROJECT"
    SERIES = "SERIES"


@dataclass(frozen=True)
class ProjectStorageContext:
    project_id: str
    book_id: str
    storage_root: Path
    projects_root: Path
    project_root: Path
    book_root: Path
    project_db_path: Path

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        return {key: str(value) for key, value in data.items()}

    @property
    def scope(self) -> StorageScope:
        return StorageScope.project(self.project_id)


@dataclass(frozen=True)
class SeriesStorageContext:
    series_id: str
    storage_root: Path
    series_collection_root: Path
    series_root: Path
    database_path: Path

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        return {key: str(value) for key, value in data.items()}

    @property
    def scope(self) -> StorageScope:
        return StorageScope.series(self.series_id)


@dataclass(frozen=True)
class SystemStorageContext:
    storage_root: Path
    database_path: Path

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        return {key: str(value) for key, value in data.items()}


@dataclass(frozen=True)
class SchemaStatus:
    current_version: int | None
    required_version: int
    initialized: bool
    migration_needed: bool
    newer_than_supported: bool


@dataclass(frozen=True)
class SchemaMigration:
    source_version: int
    target_version: int
    apply: Callable[[sqlite3.Connection], None]
    validate: Callable[[sqlite3.Connection], None] | None = None
    backup: Callable[[sqlite3.Connection], None] | None = None
    validate_reached: Callable[[sqlite3.Connection], None] | None = None


def _normalize_identifier(
    value: str,
    field_name: str,
    error_type: type[ValueError] = ProjectStorageError,
) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise error_type(f"{field_name} is required")
    if not _SAFE_IDENTIFIER.fullmatch(normalized):
        raise error_type(f"{field_name} contains unsafe path characters")
    if normalized in {".", ".."}:
        raise error_type(f"{field_name} must not be a relative path marker")
    return normalized


def _without_windows_extended_path_prefix(path: Path | str) -> Path:
    value = os.fspath(path)
    if os.name == "nt":
        folded = value.casefold()
        if folded.startswith("\\\\?\\unc\\"):
            value = "\\\\" + value[8:]
        elif folded.startswith("\\\\?\\"):
            value = value[4:]
    return Path(value)


def _assert_relative_to(
    path: Path,
    root: Path,
    field_name: str,
    error_type: type[ValueError] = ProjectStorageError,
) -> Path:
    try:
        candidate_path = _without_windows_extended_path_prefix(
            os.path.abspath(os.fspath(path))
        )
        boundary_path = _without_windows_extended_path_prefix(
            os.path.abspath(os.fspath(root))
        )
        candidate = os.path.normcase(os.fspath(candidate_path))
        boundary = os.path.normcase(os.fspath(boundary_path))
        if os.path.commonpath((candidate, boundary)) != boundary:
            raise ValueError(field_name)
        relative = candidate_path.relative_to(boundary_path)
        current = boundary_path
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        for part in relative.parts:
            current = current / part
            if not os.path.lexists(current):
                break
            attributes = current.lstat()
            if stat.S_ISLNK(attributes.st_mode) or (
                int(getattr(attributes, "st_file_attributes", 0)) & reparse_flag
            ):
                raise ValueError(field_name)
        physical = _without_windows_extended_path_prefix(
            candidate_path.resolve(strict=False)
        )
        physical_name = os.path.normcase(os.fspath(physical))
        if os.path.commonpath((physical_name, boundary)) != boundary:
            raise ValueError(field_name)
        return physical
    except (OSError, ValueError, RuntimeError) as exc:
        raise error_type(f"{field_name} escapes storage root") from exc


def _validated_repository_database_path(
    *,
    storage_root: Path,
    database_parent: Path,
    database_path: Path,
    error_type: type[ValueError],
    create_parent: bool,
) -> Path:
    parent = _assert_relative_to(
        database_parent, storage_root, "database_parent", error_type,
    )
    if create_parent:
        parent.mkdir(parents=True, exist_ok=True)
    parent = _assert_relative_to(
        database_parent, storage_root, "database_parent", error_type,
    )
    return _assert_relative_to(
        database_path, storage_root, "database_path", error_type,
    )


@contextmanager
def _physical_database_path_fence(
    *,
    storage_root: Path,
    database_parent: Path,
    database_path: Path,
    error_type: type[ValueError],
    create_parent: bool,
    create_leaf: bool,
) -> Iterator[Path]:
    """Hold Windows handles that prevent path replacement until SQLite closes.

    Lexical/physical validation alone leaves a check-to-open window.  Opening
    every existing component without FILE_SHARE_DELETE pins the validated
    directory chain and database file while sqlite3 opens and uses it.
    """
    validated = _validated_repository_database_path(
        storage_root=storage_root,
        database_parent=database_parent,
        database_path=database_path,
        error_type=error_type,
        create_parent=create_parent,
    )
    if os.name != "nt":
        yield validated
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    get_attributes = kernel32.GetFileAttributesW
    get_attributes.argtypes = (wintypes.LPCWSTR,)
    get_attributes.restype = wintypes.DWORD
    final_name = kernel32.GetFinalPathNameByHandleW
    final_name.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    final_name.restype = wintypes.DWORD

    share_read_write = 0x00000001 | 0x00000002
    open_existing, open_always = 3, 4
    generic_read, generic_write = 0x80000000, 0x40000000
    backup_semantics, open_reparse = 0x02000000, 0x00200000
    reparse_attribute, invalid_attributes = 0x00000400, 0xFFFFFFFF
    invalid_handle = ctypes.c_void_p(-1).value
    handles: list[int] = []

    def display_final(handle: int) -> Path:
        size = final_name(handle, None, 0, 0)
        if size == 0:
            raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW failed")
        buffer = ctypes.create_unicode_buffer(size + 1)
        written = final_name(handle, buffer, len(buffer), 0)
        if written == 0 or written >= len(buffer):
            raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW failed")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return Path(value)

    try:
        root = _assert_relative_to(storage_root, storage_root, "storage_root", error_type)
        relative = validated.relative_to(root)
        candidates = [root]
        current = root
        for part in relative.parts:
            current = current / part
            candidates.append(current)
        for candidate in candidates:
            is_leaf = candidate == validated
            disposition = open_always if is_leaf and create_leaf else open_existing
            desired_access = generic_read | generic_write if is_leaf and create_leaf else 0
            handle = create_file(
                str(candidate), desired_access, share_read_write, None, disposition,
                backup_semantics | open_reparse, None,
            )
            if handle == invalid_handle:
                raise OSError(ctypes.get_last_error(), f"cannot pin storage path: {candidate}")
            handles.append(handle)
            attributes = get_attributes(str(candidate))
            if attributes == invalid_attributes or attributes & reparse_attribute:
                raise OSError(ctypes.get_last_error(), f"reparse storage path rejected: {candidate}")
        physical_root = os.path.normcase(os.fspath(display_final(handles[0])))
        physical_leaf = os.path.normcase(os.fspath(display_final(handles[-1])))
        if os.path.commonpath((physical_leaf, physical_root)) != physical_root:
            raise OSError("database handle resolved outside storage root")
        yield validated
    except (OSError, ValueError, RuntimeError) as exc:
        raise error_type("database_path physical containment could not be secured") from exc
    finally:
        for handle in reversed(handles):
            close_handle(handle)


class _FencedSQLiteConnection(sqlite3.Connection):
    _physical_fence: Any = None

    def close(self) -> None:
        fence = self._physical_fence
        self._physical_fence = None
        try:
            super().close()
        finally:
            if fence is not None:
                fence.__exit__(None, None, None)


def _connect_repository_database(
    *,
    storage_root: Path,
    database_parent: Path,
    database_path: Path,
    error_type: type[ValueError],
    create_parent: bool,
    readonly: bool = False,
    timeout: float = _DOMAIN_DB_BUSY_TIMEOUT_MS / 1000,
) -> sqlite3.Connection:
    fence = _physical_database_path_fence(
        storage_root=storage_root,
        database_parent=database_parent,
        database_path=database_path,
        error_type=error_type,
        create_parent=create_parent,
        create_leaf=not readonly,
    )
    validated = fence.__enter__()
    try:
        target = validated.as_uri() + "?mode=ro" if readonly else str(validated)
        conn = sqlite3.connect(
            target,
            uri=readonly,
            timeout=timeout,
            factory=_FencedSQLiteConnection,
        )
    except BaseException as exc:
        fence.__exit__(type(exc), exc, exc.__traceback__)
        raise
    conn._physical_fence = fence
    return conn


@dataclass(frozen=True)
class StorageScope:
    scope_type: ScopeType
    scope_id: str

    def __post_init__(self) -> None:
        if isinstance(self.scope_type, ScopeType):
            scope_type = self.scope_type
        else:
            try:
                scope_type = ScopeType(str(self.scope_type))
            except ValueError as exc:
                raise ScopeValidationError("scope_type must be PROJECT or SERIES") from exc
        scope_id = _normalize_identifier(
            self.scope_id,
            "scope_id",
            ScopeValidationError,
        )
        object.__setattr__(self, "scope_type", scope_type)
        object.__setattr__(self, "scope_id", scope_id)

    @classmethod
    def project(cls, project_id: str) -> StorageScope:
        return cls(ScopeType.PROJECT, project_id)

    @classmethod
    def series(cls, series_id: str) -> StorageScope:
        return cls(ScopeType.SERIES, series_id)

    def to_dict(self) -> dict[str, str]:
        return {
            "scope_type": self.scope_type.value,
            "scope_id": self.scope_id,
        }


@dataclass(frozen=True)
class SeriesAccessContext:
    project_scope: StorageScope
    series_scope: StorageScope

    def __post_init__(self) -> None:
        project_scope = self.project_scope
        series_scope = self.series_scope
        if not isinstance(project_scope, StorageScope):
            raise SeriesAccessError("project_scope must be a StorageScope")
        if not isinstance(series_scope, StorageScope):
            raise SeriesAccessError("series_scope must be a StorageScope")
        if project_scope.scope_type != ScopeType.PROJECT:
            raise SeriesAccessError("project_scope must be PROJECT")
        if series_scope.scope_type != ScopeType.SERIES:
            raise SeriesAccessError("series_scope must be SERIES")

    @classmethod
    def bind(cls, project_id: str, series_id: str) -> SeriesAccessContext:
        return cls(
            project_scope=StorageScope.project(project_id),
            series_scope=StorageScope.series(series_id),
        )

    @property
    def project_id(self) -> str:
        return self.project_scope.scope_id

    @property
    def series_id(self) -> str:
        return self.series_scope.scope_id

    def require_project(self, project_id: str) -> None:
        if self.project_scope != StorageScope.project(project_id):
            raise SeriesAccessError("project scope is not bound to requested project")

    def require_series(self, series_id: str) -> None:
        if self.series_scope != StorageScope.series(series_id):
            raise SeriesAccessError("project is not bound to requested series scope")

    def to_dict(self) -> dict[str, dict[str, str]]:
        return {
            "project_scope": self.project_scope.to_dict(),
            "series_scope": self.series_scope.to_dict(),
        }


def _normalize_domain_id(
    value: str,
    field_name: str,
    error_type: type[ValueError] = ProjectStorageError,
) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise error_type(f"{field_name} is required")
    return normalized


def _coerce_storage_scope(
    scope: StorageScope,
    error_type: type[ValueError],
) -> StorageScope:
    if not isinstance(scope, StorageScope):
        raise error_type("scope must be a StorageScope")
    return scope


def _require_matching_scope(
    repository_scope: StorageScope,
    record_scope: StorageScope,
    error_type: type[ValueError],
) -> StorageScope:
    resolved_scope = _coerce_storage_scope(record_scope, error_type)
    if resolved_scope != repository_scope:
        raise error_type("record scope does not match repository scope")
    return resolved_scope


def _schema_status(current_version: int | None, required_version: int) -> SchemaStatus:
    return SchemaStatus(
        current_version=current_version,
        required_version=required_version,
        initialized=current_version is not None,
        migration_needed=current_version is not None and current_version < required_version,
        newer_than_supported=current_version is not None and current_version > required_version,
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _has_user_tables(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        """
    ).fetchall()
    return any(not str(row[0]).lower().startswith("sqlite_") for row in rows)


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        )
        """
    )


def _read_schema_version(conn: sqlite3.Connection) -> int | None:
    if not _table_exists(conn, "schema_version"):
        return None
    rows = conn.execute(
        "SELECT id,version,typeof(id),typeof(version) FROM schema_version ORDER BY id"
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise SchemaMigrationError("schema_version singleton cardinality is invalid")
    row = rows[0]
    if (
        row[0] != 1
        or str(row[2]).lower() != "integer"
        or str(row[3]).lower() != "integer"
        or not isinstance(row[1], int)
        or isinstance(row[1], bool)
    ):
        raise SchemaMigrationError("schema_version row is invalid")
    return row[1]


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    _ensure_schema_version_table(conn)
    conn.execute(
        """
        INSERT INTO schema_version (id, version)
        VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET version = excluded.version
        """,
        (int(version),),
    )


def _initialize_schema_version(
    conn: sqlite3.Connection,
    *,
    required_version: int,
    database_name: str,
    error_type: type[ValueError],
) -> None:
    current_version = _read_schema_version(conn)
    if current_version is None:
        if _has_user_tables(conn):
            raise error_type(f"{database_name} schema_version is missing")
        _set_schema_version(conn, required_version)
        return

    if current_version < required_version:
        raise error_type(f"{database_name} schema_version requires controlled migration")
    if current_version > required_version:
        raise error_type(f"{database_name} schema_version is newer than supported")


def _inspect_database_schema(
    *,
    storage_root: Path,
    database_parent: Path,
    database_path: Path,
    error_type: type[ValueError],
    required_version: int,
) -> SchemaStatus:
    validated = _validated_repository_database_path(
        storage_root=storage_root,
        database_parent=database_parent,
        database_path=database_path,
        error_type=error_type,
        create_parent=False,
    )
    if not validated.exists():
        return _schema_status(None, required_version)
    conn = _connect_repository_database(
        storage_root=storage_root,
        database_parent=database_parent,
        database_path=validated,
        error_type=error_type,
        create_parent=False,
        readonly=True,
    )
    conn.row_factory = sqlite3.Row
    try:
        return _schema_status(_read_schema_version(conn), required_version)
    finally:
        conn.close()


def _create_verified_sqlite_backup(
    source_path: Path,
    destination_path: Path,
    *,
    expected_version: int,
    scope_type: str,
    scope_id: str,
    expected_identity: dict[str, str | int],
    release_head: str,
    deadline_seconds: float,
) -> None:
    """Create a non-overwriting SQLite backup and validate the copied DB.

    The caller holds BEGIN IMMEDIATE on a separate migration connection.  This
    helper opens the source read-only and copies R -> D without using the
    migration connection as the backup source.
    """
    if not isinstance(deadline_seconds, (int, float)) or deadline_seconds <= 0:
        raise SchemaMigrationError("memory ledger backup deadline must be positive")
    release_head = str(release_head).strip()
    if not release_head:
        raise SchemaMigrationError("memory ledger backup release/HEAD is required")
    manifest_path = destination_path.with_name(destination_path.name + ".manifest.json")
    partial_manifest_path = manifest_path.with_name(manifest_path.name + ".partial")
    if manifest_path.exists() or partial_manifest_path.exists():
        raise SchemaMigrationError("memory ledger backup path already exists")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    timeout = min(float(deadline_seconds), _DOMAIN_DB_BUSY_TIMEOUT_MS / 1000)
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    validation: sqlite3.Connection | None = None
    reservation_fd: int | None = None
    target_owned = False
    partial_manifest_owned = False
    manifest_reserved = False
    backup_complete = False
    # perf_counter has finer resolution than the Windows monotonic clock.
    deadline = time.perf_counter() + float(deadline_seconds)

    def check_deadline(_status: int, _remaining: int, _total: int) -> None:
        if time.perf_counter() >= deadline:
            raise SchemaMigrationError("memory ledger backup deadline exceeded")

    try:
        try:
            reservation_fd = os.open(
                destination_path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0),
                0o600,
            )
        except FileExistsError as exc:
            raise SchemaMigrationError("memory ledger backup path already exists") from exc
        target_owned = True
        source = sqlite3.connect(
            source_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout,
        )
        target = sqlite3.connect(str(destination_path), timeout=timeout)
        source.execute("PRAGMA query_only = ON")
        source.execute(f"PRAGMA busy_timeout = {max(1, int(timeout * 1000))}")
        target.execute(f"PRAGMA busy_timeout = {max(1, int(timeout * 1000))}")
        source.backup(target, pages=256, progress=check_deadline, sleep=0.01)
        # Enforce the deadline after the copy as well as during progress.
        if time.perf_counter() >= deadline:
            raise SchemaMigrationError("memory ledger backup deadline exceeded")
        target.commit()
        target.close()
        target = None
        validation = sqlite3.connect(
            destination_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout,
        )
        validation.row_factory = sqlite3.Row
        validation.execute("PRAGMA query_only = ON")
        validation.execute(f"PRAGMA busy_timeout = {max(1, int(timeout * 1000))}")
        row = validation.execute("PRAGMA integrity_check").fetchone()
        if row is None or str(row[0]).lower() != "ok":
            raise SchemaMigrationError("memory ledger backup integrity check failed")
        if validation.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SchemaMigrationError("memory ledger backup foreign key check failed")
        version = _read_schema_version(validation)
        if version != expected_version:
            raise SchemaMigrationError("memory ledger backup schema version mismatch")
        if scope_type == "PROJECT":
            identity_row = validation.execute(
                "SELECT project_id,book_id,schema_version FROM project_identity WHERE id=1"
            ).fetchone()
            identity_keys = ("project_id", "book_id", "schema_version")
        elif scope_type == "SERIES":
            identity_row = validation.execute(
                "SELECT series_id,schema_version FROM series_identity WHERE id=1"
            ).fetchone()
            identity_keys = ("series_id", "schema_version")
        else:
            raise SchemaMigrationError("memory ledger backup scope is invalid")
        if identity_row is None or {
            key: identity_row[key] for key in identity_keys
        } != expected_identity:
            raise SchemaMigrationError("memory ledger backup identity mismatch")
        validation.close()
        validation = None
        source.close()
        source = None
        backup_hash = hashlib.sha256(destination_path.read_bytes()).hexdigest()
        manifest = _ledger_canonical_json({
            "format": "memory-ledger-backup-manifest.v1",
            "scope_type": scope_type,
            "scope_id": scope_id,
            "source_schema_version": expected_version,
            "release": "MEMORY_LEDGER_V1",
            "head": release_head,
            "backup_sha256": backup_hash,
            "validation": {
                "integrity_check": "ok",
                "foreign_key_check": "ok",
                "identity": "ok",
                "schema": "ok",
            },
        })
        with partial_manifest_path.open("x", encoding="utf-8", newline="") as handle:
            partial_manifest_owned = True
            handle.write(manifest)
        os.link(partial_manifest_path, manifest_path)
        manifest_reserved = True
        partial_manifest_path.unlink()
        partial_manifest_owned = False
        backup_complete = True
    except Exception:
        raise
    finally:
        for connection in (validation, target, source):
            if connection is not None:
                connection.close()
        if reservation_fd is not None:
            os.close(reservation_fd)
        if target_owned and not backup_complete:
            if destination_path.exists():
                destination_path.unlink()
            if partial_manifest_owned and partial_manifest_path.exists():
                partial_manifest_path.unlink()
            if manifest_reserved and manifest_path.exists():
                manifest_path.unlink()


def _is_sqlite_busy_or_locked(exc: BaseException) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    return isinstance(code, int) and (code & 0xFF) in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    }


def _migration_inspect(runner: "SchemaMigrationRunner", conn: sqlite3.Connection) -> SchemaStatus:
    try:
        return runner.inspect(conn)
    except sqlite3.OperationalError as exc:
        if _is_sqlite_busy_or_locked(exc):
            raise SchemaMigrationError("MAINTENANCE_BUSY") from exc
        raise


class SchemaMigrationRunner:
    def __init__(
        self,
        *,
        target_version: int,
        migrations: Iterable[SchemaMigration] = (),
    ) -> None:
        if int(target_version) < 1:
            raise SchemaMigrationError("target schema version must be positive")
        self.target_version = int(target_version)
        self._migrations: dict[int, SchemaMigration] = {}
        for migration in migrations:
            source_version = int(migration.source_version)
            target = int(migration.target_version)
            if target != source_version + 1:
                raise SchemaMigrationError("migrations must advance exactly one schema version")
            if source_version in self._migrations:
                raise SchemaMigrationError(f"duplicate migration from schema version {source_version}")
            self._migrations[source_version] = migration

    def inspect(self, conn: sqlite3.Connection) -> SchemaStatus:
        return _schema_status(_read_schema_version(conn), self.target_version)

    def migrate(
        self,
        conn: sqlite3.Connection,
        *,
        before_migrate: Callable[[sqlite3.Connection], None] | None = None,
    ) -> SchemaStatus:
        if conn.in_transaction:
            raise SchemaMigrationError("migration requires a clean connection")
        try:
            pre_status = self.inspect(conn)
            if pre_status.current_version is None:
                raise SchemaMigrationError("schema_version is missing")
            if pre_status.newer_than_supported:
                raise SchemaMigrationError("database schema is newer than supported")
            conn.execute("BEGIN IMMEDIATE")
            # Inspect before the lock is advisory only. The version used to
            # decide DDL is always reread after the maintenance write lock.
            status = self.inspect(conn)
            if status.current_version is None:
                raise SchemaMigrationError("schema_version is missing")
            if status.newer_than_supported:
                raise SchemaMigrationError("database schema is newer than supported")
            if not status.migration_needed:
                self._validate_target(conn)
                conn.commit()
                return self.inspect(conn)
            if before_migrate is not None:
                before_migrate(conn)
            version = int(status.current_version)
            while version < self.target_version:
                migration = self._migrations.get(version)
                if migration is None:
                    raise SchemaMigrationError(
                        f"missing migration from schema version {version} to {version + 1}"
                    )
                if migration.backup is not None:
                    migration.backup(conn)
                migration.apply(conn)
                if migration.validate is not None:
                    migration.validate(conn)
                _set_schema_version(conn, migration.target_version)
                version = int(migration.target_version)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if isinstance(exc, sqlite3.OperationalError):
                if _is_sqlite_busy_or_locked(exc):
                    raise SchemaMigrationError("MAINTENANCE_BUSY") from exc
            raise

        return self.inspect(conn)

    def _validate_target(self, conn: sqlite3.Connection) -> None:
        migration = self._migrations.get(self.target_version - 1)
        if migration is not None and migration.validate_reached is not None:
            migration.validate_reached(conn)


def _preflight_memory_ledger_migration(
    conn: sqlite3.Connection,
    *,
    scope_type: str,
    expected_version: int,
    expected_identity: dict[str, str | int],
) -> None:
    if not conn.in_transaction:
        raise SchemaMigrationError("memory ledger migration requires a write reservation")
    if int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise SchemaMigrationError("memory ledger migration requires foreign_keys=ON")
    if int(conn.execute("PRAGMA busy_timeout").fetchone()[0]) < _DOMAIN_DB_BUSY_TIMEOUT_MS:
        raise SchemaMigrationError("memory ledger migration busy timeout is too short")
    row = conn.execute("PRAGMA integrity_check").fetchone()
    if row is None or str(row[0]).lower() != "ok":
        raise SchemaMigrationError("memory ledger source integrity check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise SchemaMigrationError("memory ledger source foreign key check failed")
    if _read_schema_version(conn) != expected_version:
        raise SchemaMigrationError("memory ledger migration entry schema changed")
    if scope_type == "PROJECT":
        row = conn.execute(
            "SELECT project_id,book_id,schema_version FROM project_identity WHERE id=1"
        ).fetchone()
        keys = ("project_id", "book_id", "schema_version")
        _validate_project_schema_v5(conn)
        metadata_table = "project_metadata"
    elif scope_type == "SERIES":
        row = conn.execute(
            "SELECT series_id,schema_version FROM series_identity WHERE id=1"
        ).fetchone()
        keys = ("series_id", "schema_version")
        _validate_series_schema_v3(conn)
        metadata_table = "series_metadata"
    else:
        raise SchemaMigrationError("memory ledger migration scope is invalid")
    if row is None or {key: row[index] for index, key in enumerate(keys)} != expected_identity:
        raise SchemaMigrationError("memory ledger migration identity mismatch")
    _require_absent_memory_ledger_schema(conn, metadata_table=metadata_table)


def _normalized_schema_sql(sql: str | None) -> str:
    try:
        return _canonical_schema_sql(sql)
    except MemoryLedgerIntegrityError as exc:
        raise SchemaMigrationError("schema SQL is malformed") from exc


def _schema_check_expressions(sql: str | None) -> tuple[str, ...]:
    """Return comment-aware, literal-preserving CHECK signatures."""
    try:
        return _schema_check_signatures(sql)
    except MemoryLedgerIntegrityError as exc:
        raise SchemaMigrationError("table CHECK constraint is malformed") from exc


def _expected_index_key_signature(sql: str) -> tuple[tuple[str, int, str], ...]:
    """Parse the deliberately small frozen CREATE INDEX grammar."""
    try:
        tokens = _schema_sql_tokens(sql)
    except MemoryLedgerIntegrityError as exc:
        raise SchemaMigrationError("frozen index SQL is malformed") from exc
    try:
        start = tokens.index("(") + 1
    except ValueError as exc:
        raise SchemaMigrationError("frozen index SQL has no key list") from exc
    terms: list[list[str]] = [[]]
    depth = 0
    cursor = start
    while cursor < len(tokens):
        token = tokens[cursor]
        if token == "(" :
            depth += 1
        elif token == ")":
            if depth == 0:
                break
            depth -= 1
        if token == "," and depth == 0:
            terms.append([])
        else:
            terms[-1].append(token)
        cursor += 1
    else:
        raise SchemaMigrationError("frozen index key list is malformed")

    result: list[tuple[str, int, str]] = []
    for term in terms:
        if not term:
            raise SchemaMigrationError("frozen index key is empty")
        column = term.pop(0)
        collation = "BINARY"
        descending = 0
        while term:
            modifier = term.pop(0)
            if modifier == "collate" and term:
                collation = term.pop(0).upper()
            elif modifier in {"asc", "desc"}:
                descending = int(modifier == "desc")
            else:
                raise SchemaMigrationError("frozen index key uses an unsupported expression")
        result.append((column, descending, collation))
    return tuple(result)


def _validate_table_contract(
    conn: sqlite3.Connection,
    *,
    table: str,
    columns: tuple[tuple[str, str, int, int], ...],
    sql_fragments: tuple[str, ...] = (),
) -> None:
    expected_columns = tuple(
        (name, data_type, not_null, None, primary_key_rank, 0)
        for name, data_type, not_null, primary_key_rank in columns
    )
    actual_columns = _sqlite_table_xinfo_signature(conn, table)
    if actual_columns != expected_columns:
        raise SchemaMigrationError(f"{table} column definition is invalid")
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    raw_sql = None if row is None else row[0]
    sql = _normalized_schema_sql(raw_sql)
    try:
        tokens = _schema_sql_tokens(raw_sql)
    except MemoryLedgerIntegrityError as exc:
        raise SchemaMigrationError(f"{table} SQL is malformed") from exc
    if _schema_has_token_sequence(raw_sql, ("on", "conflict")) or "collate" in tokens:
        raise SchemaMigrationError(f"{table} SQL modifiers are outside the frozen schema")
    non_check_fragments = tuple(
        fragment for fragment in sql_fragments
        if "check" not in _schema_sql_tokens(fragment)
    )
    if any(_normalized_schema_sql(fragment) not in sql for fragment in non_check_fragments):
        raise SchemaMigrationError(f"{table} table constraints are invalid")
    frozen_fragments = " ".join(sql_fragments)
    if _schema_check_expressions(raw_sql) != _schema_check_expressions(frozen_fragments):
        raise SchemaMigrationError(f"{table} has CHECK constraints outside the frozen schema")

    expected_automatic_indexes: set[
        tuple[str, int, int, tuple[tuple[str, int, str], ...]]
    ] = set()
    primary_key = tuple(
        name for name, _data_type, _not_null, rank in sorted(columns, key=lambda item: item[3])
        if rank > 0
    )
    column_types = {name: data_type for name, data_type, _not_null, _rank in columns}
    if primary_key and not (
        len(primary_key) == 1 and column_types[primary_key[0]].upper() == "INTEGER"
    ):
        expected_automatic_indexes.add((
            "pk", 1, 0, tuple((name, 0, "BINARY") for name in primary_key),
        ))
    for match in re.finditer(r"unique\(([^()]*)\)", _normalized_schema_sql(frozen_fragments)):
        unique_columns = tuple(
            value.strip('"`[]') for value in match.group(1).split(",") if value
        )
        expected_automatic_indexes.add((
            "u", 1, 0, tuple((name, 0, "BINARY") for name in unique_columns),
        ))
    actual_automatic_indexes: set[
        tuple[str, int, int, tuple[tuple[str | None, int, str], ...]]
    ] = set()
    for index in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
        name, unique, origin, partial = str(index[1]), int(index[2]), str(index[3]), int(index[4])
        if origin == "c":
            continue
        keys = _sqlite_index_key_signature(conn, name)
        actual_automatic_indexes.add((origin, unique, partial, keys))
    if actual_automatic_indexes != expected_automatic_indexes:
        raise SchemaMigrationError(f"{table} UNIQUE/PRIMARY KEY constraints are invalid")

    if conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchone() is not None:
        raise SchemaMigrationError(f"{table} has foreign keys outside the frozen schema")


def _validate_explicit_index_contracts(
    conn: sqlite3.Connection,
    expected: dict[str, str],
    *,
    tables: Iterable[str],
) -> None:
    table_names = tuple(sorted(set(tables)))
    placeholders = ",".join("?" for _ in table_names)
    rows = conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL "
        f"AND tbl_name IN ({placeholders}) ORDER BY name",
        table_names,
    ).fetchall()
    actual = {str(row[0]): (str(row[1]), _normalized_schema_sql(row[1])) for row in rows}
    if set(actual) != set(expected):
        raise SchemaMigrationError("explicit index set is outside the frozen schema")
    for name, expected_sql in expected.items():
        raw_sql, canonical_sql = actual[name]
        if canonical_sql != _normalized_schema_sql(expected_sql):
            raise SchemaMigrationError(f"required index is invalid: {name}")
        index_row = conn.execute(
            "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?", (name,)
        ).fetchone()
        if index_row is None:
            raise SchemaMigrationError(f"required index is missing: {name}")
        listed = conn.execute(
            f'PRAGMA index_list("{str(index_row[0]).replace(chr(34), chr(34) * 2)}")'
        ).fetchall()
        metadata = next((row for row in listed if str(row[1]) == name), None)
        if metadata is None:
            raise SchemaMigrationError(f"required index metadata is missing: {name}")
        expected_unique = int(_schema_has_token_sequence(expected_sql, ("create", "unique", "index")))
        expected_partial = int("where" in _schema_sql_tokens(expected_sql))
        if int(metadata[2]) != expected_unique or int(metadata[4]) != expected_partial:
            raise SchemaMigrationError(f"required index is invalid: {name}")
        if _sqlite_index_key_signature(conn, name) != _expected_index_key_signature(expected_sql):
            raise SchemaMigrationError(f"required index key definition is invalid: {name}")


_PROJECT_BASE_TABLE_CONTRACTS = {
    "schema_version": (
        (("id", "INTEGER", 0, 1), ("version", "INTEGER", 1, 0)),
        ("id INTEGER PRIMARY KEY CHECK (id = 1)",),
    ),
    "project_identity": (
        (("id", "INTEGER", 0, 1), ("project_id", "TEXT", 1, 0),
         ("book_id", "TEXT", 1, 0), ("schema_version", "INTEGER", 1, 0)),
        ("id INTEGER PRIMARY KEY CHECK (id = 1)",),
    ),
    "project_metadata": (
        (("key", "TEXT", 0, 1), ("value", "TEXT", 1, 0)),
        ("key TEXT PRIMARY KEY",),
    ),
    "project_fact_records": (
        (("scope_type", "TEXT", 1, 0), ("scope_id", "TEXT", 1, 0),
         ("fact_id", "TEXT", 0, 1), ("payload_json", "TEXT", 1, 0)),
        ("fact_id TEXT PRIMARY KEY",),
    ),
    "project_character_states": (
        (("scope_type", "TEXT", 1, 0), ("scope_id", "TEXT", 1, 0),
         ("state_id", "TEXT", 0, 1), ("character_id", "TEXT", 1, 0),
         ("payload_json", "TEXT", 1, 0)),
        ("state_id TEXT PRIMARY KEY",),
    ),
    "project_structured_memory_records": (
        (("scope_type", "TEXT", 1, 1), ("scope_id", "TEXT", 1, 2),
         ("record_type", "TEXT", 1, 3), ("record_id", "TEXT", 1, 4),
         ("payload_json", "TEXT", 1, 0)),
        ("PRIMARY KEY (scope_type, scope_id, record_type, record_id)",),
    ),
    "edges": (
        (("scope_type", "TEXT", 1, 1), ("scope_id", "TEXT", 1, 2),
         ("edge_id", "TEXT", 1, 3), ("source_id", "TEXT", 1, 0),
         ("target_id", "TEXT", 1, 0), ("relation_type", "TEXT", 1, 0),
         ("payload_json", "TEXT", 1, 0)),
        ("CHECK (scope_type = 'PROJECT')", "PRIMARY KEY (scope_type, scope_id, edge_id)"),
    ),
    "context_packages": (
        (("scope_type", "TEXT", 1, 1), ("scope_id", "TEXT", 1, 2),
         ("context_package_id", "TEXT", 1, 3), ("operation_id", "TEXT", 1, 0),
         ("run_id", "TEXT", 1, 0), ("step_id", "TEXT", 1, 0),
         ("context_hash", "TEXT", 1, 0), ("payload_json", "TEXT", 1, 0)),
        ("CHECK (scope_type = 'PROJECT')",
         "PRIMARY KEY (scope_type, scope_id, context_package_id)",
         "UNIQUE (scope_type, scope_id, operation_id)"),
    ),
    "style_library_profiles": (
        (("scope_type", "TEXT", 1, 1), ("scope_id", "TEXT", 1, 2),
         ("profile_id", "TEXT", 1, 3), ("version", "INTEGER", 1, 4),
         ("payload_json", "TEXT", 1, 0)),
        ("CHECK (scope_type = 'PROJECT')",
         "PRIMARY KEY (scope_type, scope_id, profile_id, version)"),
    ),
    "book_style_dna": (
        (("scope_type", "TEXT", 1, 1), ("scope_id", "TEXT", 1, 2),
         ("book_id", "TEXT", 1, 3), ("version", "INTEGER", 1, 4),
         ("is_active", "INTEGER", 1, 0), ("payload_json", "TEXT", 1, 0)),
        ("CHECK (scope_type = 'PROJECT')", "CHECK (is_active IN (0, 1))",
         "PRIMARY KEY (scope_type, scope_id, book_id, version)"),
    ),
    "scene_style_recipes": (
        (("scope_type", "TEXT", 1, 1), ("scope_id", "TEXT", 1, 2),
         ("recipe_id", "TEXT", 1, 3), ("payload_json", "TEXT", 1, 0)),
        ("CHECK (scope_type = 'PROJECT')", "PRIMARY KEY (scope_type, scope_id, recipe_id)"),
    ),
    "style_evaluations": (
        (("scope_type", "TEXT", 1, 1), ("scope_id", "TEXT", 1, 2),
         ("style_evaluation_id", "TEXT", 1, 3), ("payload_json", "TEXT", 1, 0)),
        ("CHECK (scope_type = 'PROJECT')",
         "PRIMARY KEY (scope_type, scope_id, style_evaluation_id)"),
    ),
    "style_performance_records": (
        (("scope_type", "TEXT", 1, 1), ("scope_id", "TEXT", 1, 2),
         ("performance_id", "TEXT", 1, 3), ("payload_json", "TEXT", 1, 0)),
        ("CHECK (scope_type = 'PROJECT')",
         "PRIMARY KEY (scope_type, scope_id, performance_id)"),
    ),
}

_GAP018_RECORD_KINDS = frozenset({
    "CHALLENGE", "CHALLENGE_USE", "REQUEST", "APPROVAL", "PROMOTION",
    "PROMOTION_TERMINAL", "SOURCE", "CANDIDATE", "CANDIDATE_STATE", "MANUSCRIPT",
    "MANUSCRIPT_REQUEST_RESULT", "QA_RUN_RESULT",
})

_GAP018_TABLE_CONTRACTS = {
    "gap018_records": (
        (("project_id", "TEXT", 1, 1), ("book_id", "TEXT", 1, 2),
         ("record_kind", "TEXT", 1, 3), ("record_id", "TEXT", 1, 4),
         ("payload_hash", "TEXT", 1, 0), ("payload_json", "TEXT", 1, 0)),
        ("PRIMARY KEY (project_id, book_id, record_kind, record_id)",),
    ),
    "gap018_source_head": (
        (("project_id", "TEXT", 1, 1), ("book_id", "TEXT", 1, 2),
         ("source_master_id", "TEXT", 1, 0), ("version", "INTEGER", 1, 0),
         ("head_hash", "TEXT", 1, 0)),
        ("PRIMARY KEY (project_id, book_id)",),
    ),
    "gap018_manuscript_head": (
        (("project_id", "TEXT", 1, 1), ("book_id", "TEXT", 1, 2),
         ("manuscript_id", "TEXT", 1, 0), ("version", "INTEGER", 1, 0),
         ("manifest_hash", "TEXT", 1, 0)),
        ("PRIMARY KEY (project_id, book_id)",),
    ),
}

_PROJECT_EXPLICIT_INDEX_CONTRACTS = {
    "edges_source": "CREATE INDEX edges_source ON edges(scope_type, scope_id, source_id, edge_id)",
    "edges_target": "CREATE INDEX edges_target ON edges(scope_type, scope_id, target_id, edge_id)",
    "context_packages_run_step": (
        "CREATE INDEX context_packages_run_step ON context_packages(scope_type, scope_id, run_id, step_id)"
    ),
    "book_style_dna_active": (
        "CREATE UNIQUE INDEX book_style_dna_active ON book_style_dna(scope_type, scope_id, book_id) "
        "WHERE is_active = 1"
    ),
}

_SERIES_BASE_TABLE_CONTRACTS = {
    "schema_version": _PROJECT_BASE_TABLE_CONTRACTS["schema_version"],
    "series_identity": (
        (("id", "INTEGER", 0, 1), ("series_id", "TEXT", 1, 0),
         ("schema_version", "INTEGER", 1, 0)),
        ("id INTEGER PRIMARY KEY CHECK (id = 1)",),
    ),
    "series_metadata": (
        (("key", "TEXT", 0, 1), ("value", "TEXT", 1, 0)),
        ("key TEXT PRIMARY KEY",),
    ),
    "series_memberships": (
        (("project_id", "TEXT", 0, 1), ("book_id", "TEXT", 1, 0),
         ("payload_json", "TEXT", 1, 0)),
        ("project_id TEXT PRIMARY KEY",),
    ),
    "series_state_records": (
        (("scope_type", "TEXT", 1, 1), ("scope_id", "TEXT", 1, 2),
         ("state_kind", "TEXT", 1, 3), ("record_type", "TEXT", 1, 4),
         ("record_id", "TEXT", 1, 5), ("source_project_id", "TEXT", 1, 0),
         ("payload_json", "TEXT", 1, 0)),
        ("CHECK (scope_type = 'SERIES')",
         "PRIMARY KEY (scope_type, scope_id, state_kind, record_type, record_id)"),
    ),
    "volume_closing_snapshots": (
        (("snapshot_id", "TEXT", 0, 1), ("scope_type", "TEXT", 1, 0),
         ("scope_id", "TEXT", 1, 0), ("project_id", "TEXT", 1, 0),
         ("book_id", "TEXT", 1, 0), ("source_state_version", "INTEGER", 1, 0),
         ("semantic_hash", "TEXT", 1, 0), ("payload_json", "TEXT", 1, 0)),
        ("snapshot_id TEXT PRIMARY KEY", "CHECK (scope_type = 'SERIES')",
         "UNIQUE (scope_type, scope_id, project_id, book_id, semantic_hash)"),
    ),
    "series_operations": (
        (("operation_id", "TEXT", 0, 1), ("semantic_hash", "TEXT", 1, 0),
         ("result_type", "TEXT", 1, 0), ("result_id", "TEXT", 1, 0),
         ("result_payload_json", "TEXT", 1, 0)),
        ("operation_id TEXT PRIMARY KEY",),
    ),
    "edges": (
        (("scope_type", "TEXT", 1, 1), ("scope_id", "TEXT", 1, 2),
         ("edge_id", "TEXT", 1, 3), ("source_id", "TEXT", 1, 0),
         ("target_id", "TEXT", 1, 0), ("relation_type", "TEXT", 1, 0),
         ("payload_json", "TEXT", 1, 0)),
        ("CHECK (scope_type = 'SERIES')", "PRIMARY KEY (scope_type, scope_id, edge_id)"),
    ),
}

_SERIES_EXPLICIT_INDEX_CONTRACTS = {
    "series_snapshots_lookup": (
        "CREATE INDEX series_snapshots_lookup ON volume_closing_snapshots "
        "(scope_type, scope_id, source_state_version DESC, snapshot_id)"
    ),
    "series_edges_source": (
        "CREATE INDEX series_edges_source ON edges(scope_type, scope_id, source_id, edge_id)"
    ),
    "series_edges_target": (
        "CREATE INDEX series_edges_target ON edges(scope_type, scope_id, target_id, edge_id)"
    ),
}


def _validate_project_base_schema(conn: sqlite3.Connection) -> None:
    for table, (columns, fragments) in _PROJECT_BASE_TABLE_CONTRACTS.items():
        _validate_table_contract(conn, table=table, columns=columns, sql_fragments=fragments)
    _validate_explicit_index_contracts(
        conn, _PROJECT_EXPLICIT_INDEX_CONTRACTS, tables=_PROJECT_BASE_TABLE_CONTRACTS,
    )


def _validate_series_base_schema(conn: sqlite3.Connection) -> None:
    for table, (columns, fragments) in _SERIES_BASE_TABLE_CONTRACTS.items():
        _validate_table_contract(conn, table=table, columns=columns, sql_fragments=fragments)
    _validate_explicit_index_contracts(
        conn, _SERIES_EXPLICIT_INDEX_CONTRACTS, tables=_SERIES_BASE_TABLE_CONTRACTS,
    )


def _validate_no_authoritative_base_triggers(
    conn: sqlite3.Connection, *, tables: Iterable[str],
) -> None:
    """The frozen P6/S4 base tables permit no application-defined triggers."""
    table_names = {
        _canonical_sqlite_identifier(str(table)) for table in tables
    }
    rows = tuple(
        row for row in conn.execute(
            "SELECT name,tbl_name FROM sqlite_master "
            "WHERE type='trigger' ORDER BY tbl_name,name"
        ).fetchall()
        if _canonical_sqlite_identifier(str(row[1])) in table_names
    )
    if rows:
        raise SchemaMigrationError(
            "authoritative base table trigger set is outside the frozen schema"
        )


def _validate_exact_user_table_set(conn: sqlite3.Connection, expected: Iterable[str]) -> None:
    actual = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        if not str(row[0]).lower().startswith("sqlite_")
    }
    if actual != set(expected):
        raise SchemaMigrationError("database table set is outside the frozen schema")


def _require_absent_memory_ledger_schema(
    conn: sqlite3.Connection, *, metadata_table: str,
) -> None:
    names = {
        "memory_events", "memory_event_entities",
        "memory_events_operation_lookup", "memory_events_type_lookup",
        "memory_event_entities_lookup", "memory_events_reject_insert",
        "memory_events_reject_update", "memory_events_reject_delete",
        "memory_event_entities_reject_insert", "memory_event_entities_reject_update",
        "memory_event_entities_reject_delete",
    }
    placeholders = ",".join("?" for _ in names)
    if conn.execute(
        f"SELECT 1 FROM sqlite_master WHERE name IN ({placeholders}) LIMIT 1",
        tuple(sorted(names)),
    ).fetchone() is not None:
        raise SchemaMigrationError("memory ledger migration target objects already exist")
    if conn.execute(
        f"SELECT 1 FROM {metadata_table} WHERE key=?", (MEMORY_LEDGER_CONTROL_KEY,)
    ).fetchone() is not None:
        raise SchemaMigrationError("memory ledger migration control already exists")


def _create_project_structured_memory_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_structured_memory_records (
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            record_type TEXT NOT NULL,
            record_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, record_type, record_id)
        )
        """
    )


def _apply_project_schema_v1_to_v2(conn: sqlite3.Connection) -> None:
    _create_project_structured_memory_table(conn)
    if _table_exists(conn, "project_identity"):
        conn.execute(
            """
            UPDATE project_identity
            SET schema_version = ?
            WHERE id = 1
            """,
            (2,),
        )


def _validate_project_schema_v2(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "project_structured_memory_records"):
        raise SchemaMigrationError("project structured memory table is missing")


def _create_project_edges_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS edges (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            edge_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, edge_id)
        )
        """
    )
    for side in ("source", "target"):
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS edges_{side} "
            f"ON edges(scope_type, scope_id, {side}_id, edge_id)"
        )


def _apply_project_schema_v2_to_v3(conn: sqlite3.Connection) -> None:
    _create_project_edges_table(conn)
    if _table_exists(conn, "project_identity"):
        conn.execute("UPDATE project_identity SET schema_version = 3 WHERE id = 1")


def _validate_project_schema_v3(conn: sqlite3.Connection) -> None:
    conn.execute(
        "SELECT scope_type, scope_id, edge_id, source_id, target_id, relation_type, "
        "payload_json FROM edges LIMIT 0"
    )


def _create_context_packages_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS context_packages (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            context_package_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            context_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, context_package_id),
            UNIQUE (scope_type, scope_id, operation_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS context_packages_run_step "
        "ON context_packages(scope_type, scope_id, run_id, step_id)"
    )


def _apply_project_schema_v3_to_v4(conn: sqlite3.Connection) -> None:
    _create_context_packages_table(conn)
    if _table_exists(conn, "project_identity"):
        conn.execute("UPDATE project_identity SET schema_version = 4 WHERE id = 1")


def _validate_project_schema_v4(conn: sqlite3.Connection) -> None:
    conn.execute(
        "SELECT scope_type, scope_id, context_package_id, operation_id, run_id, "
        "step_id, context_hash, payload_json FROM context_packages LIMIT 0"
    )


def _create_adaptive_style_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS style_library_profiles (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, profile_id, version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_style_dna (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            book_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, book_id, version)
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS book_style_dna_active "
        "ON book_style_dna(scope_type, scope_id, book_id) WHERE is_active = 1"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_style_recipes (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            recipe_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, recipe_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS style_evaluations (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            style_evaluation_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, style_evaluation_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS style_performance_records (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            performance_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, performance_id)
        )
        """
    )


def _apply_project_schema_v4_to_v5(conn: sqlite3.Connection) -> None:
    _create_adaptive_style_tables(conn)
    if _table_exists(conn, "project_identity"):
        conn.execute("UPDATE project_identity SET schema_version = 5 WHERE id = 1")


def _validated_identity_singleton(
    conn: sqlite3.Connection,
    *,
    table: str,
    identity_columns: tuple[str, ...],
    expected_version: int,
    error_message: str,
) -> sqlite3.Row:
    selected = ",".join(("id", *identity_columns, "schema_version"))
    type_columns = ",".join(
        f"typeof({column}) AS type_{column}"
        for column in ("id", *identity_columns, "schema_version")
    )
    rows = conn.execute(
        f"SELECT {selected},{type_columns} FROM {table} ORDER BY id"
    ).fetchall()
    if len(rows) != 1:
        raise SchemaMigrationError(error_message)
    row = rows[0]
    if (
        row["id"] != 1
        or str(row["type_id"]).lower() != "integer"
        or not isinstance(row["schema_version"], int)
        or isinstance(row["schema_version"], bool)
        or str(row["type_schema_version"]).lower() != "integer"
        or row["schema_version"] != expected_version
    ):
        raise SchemaMigrationError(error_message)
    for column in identity_columns:
        if (
            not isinstance(row[column], str)
            or not row[column]
            or str(row[f"type_{column}"]).lower() != "text"
        ):
            raise SchemaMigrationError(error_message)
    return row


def _validate_project_schema_v5(conn: sqlite3.Connection) -> None:
    _validate_project_base_schema(conn)
    _validated_identity_singleton(
        conn,
        table="project_identity",
        identity_columns=("project_id", "book_id"),
        expected_version=5,
        error_message="project v5 identity is invalid",
    )


def _set_memory_ledger_control(
    conn: sqlite3.Connection,
    *,
    metadata_table: str,
    state: str,
    bootstrap_operation_id: str | None = None,
    activation_event_id: str | None = None,
    baseline_count: int | None = None,
    manifest_hash: str | None = None,
) -> None:
    value = _ledger_canonical_json({
        "state": state,
        "coverage": MEMORY_LEDGER_COVERAGE,
        "bootstrap_operation_id": bootstrap_operation_id,
        "activation_event_id": activation_event_id,
        "baseline_count": baseline_count,
        "manifest_hash": manifest_hash,
    })
    conn.execute(
        f"INSERT INTO {metadata_table}(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (MEMORY_LEDGER_CONTROL_KEY, value),
    )


def _create_project_memory_ledger_tables(conn: sqlite3.Connection, *, replace_triggers: bool = False) -> None:
    create_memory_ledger_schema(
        conn,
        scope_type=ScopeType.PROJECT.value,
        identity_table="project_identity",
        identity_column="project_id",
        replace_triggers=replace_triggers,
    )


def _apply_project_schema_v5_to_v6(conn: sqlite3.Connection) -> None:
    _create_project_memory_ledger_tables(conn)
    _set_memory_ledger_control(conn, metadata_table="project_metadata", state="SCHEMA_READY")
    if _table_exists(conn, "project_identity"):
        conn.execute("UPDATE project_identity SET schema_version = 6 WHERE id = 1")


def _validate_project_schema_v6_step(
    conn: sqlite3.Connection, *, expected_version: int = 6,
    extra_tables: tuple[str, ...] = (),
) -> None:
    _validate_project_base_schema(conn)
    _validate_no_authoritative_base_triggers(
        conn, tables=_PROJECT_BASE_TABLE_CONTRACTS,
    )
    validate_memory_ledger_schema(conn, scope_type=ScopeType.PROJECT.value)
    _validate_exact_user_table_set(
        conn, (*_PROJECT_BASE_TABLE_CONTRACTS, "memory_events", "memory_event_entities", *extra_tables),
    )
    identity = _validated_identity_singleton(
        conn,
        table="project_identity",
        identity_columns=("project_id", "book_id"),
        expected_version=expected_version,
        error_message="project identity schema version is invalid",
    )
    try:
        control = _read_validated_memory_ledger_control(conn, metadata_table="project_metadata")
        if control["state"] == "ACTIVE":
            _validate_active_memory_ledger(
                conn,
                control=control,
                scope_type=ScopeType.PROJECT.value,
                scope_id=str(identity["project_id"]),
            )
        elif (
            int(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]) != 0
            or int(conn.execute("SELECT COUNT(*) FROM memory_event_entities").fetchone()[0]) != 0
        ):
            raise MemoryLedgerIntegrityError("SCHEMA_READY project ledger is not empty")
    except (MemoryLedgerError, sqlite3.Error) as exc:
        raise SchemaMigrationError("invalid project memory ledger control") from exc


def _validate_project_schema_v6(conn: sqlite3.Connection) -> None:
    _validate_project_schema_v6_step(conn)
    if _read_schema_version(conn) != 6:
        raise SchemaMigrationError("project schema version is invalid")


def _create_gap018_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gap018_records ("
        "project_id TEXT NOT NULL, book_id TEXT NOT NULL, "
        "record_kind TEXT NOT NULL, record_id TEXT NOT NULL, "
        "payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL, "
        "PRIMARY KEY (project_id, book_id, record_kind, record_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gap018_source_head ("
        "project_id TEXT NOT NULL, book_id TEXT NOT NULL, "
        "source_master_id TEXT NOT NULL, version INTEGER NOT NULL, "
        "head_hash TEXT NOT NULL, PRIMARY KEY (project_id, book_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gap018_manuscript_head ("
        "project_id TEXT NOT NULL, book_id TEXT NOT NULL, "
        "manuscript_id TEXT NOT NULL, version INTEGER NOT NULL, "
        "manifest_hash TEXT NOT NULL, PRIMARY KEY (project_id, book_id))"
    )


def _apply_project_schema_v6_to_v7(conn: sqlite3.Connection) -> None:
    _validate_project_schema_v6(conn)
    _create_gap018_tables(conn)
    conn.execute("UPDATE project_identity SET schema_version = 7 WHERE id = 1")


def _validate_project_schema_v7_step(conn: sqlite3.Connection) -> None:
    _validate_project_schema_v6_step(
        conn, expected_version=7, extra_tables=tuple(_GAP018_TABLE_CONTRACTS),
    )
    for table, (columns, fragments) in _GAP018_TABLE_CONTRACTS.items():
        _validate_table_contract(conn, table=table, columns=columns, sql_fragments=fragments)
    identity = conn.execute("SELECT project_id,book_id FROM project_identity WHERE id=1").fetchone()
    for row in conn.execute(
        "SELECT project_id,book_id,record_kind,record_id,payload_hash,payload_json FROM gap018_records"
    ).fetchall():
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise SchemaMigrationError("GAP-018 record JSON is invalid") from exc
        if ((row["project_id"], row["book_id"]) != (identity["project_id"], identity["book_id"])
                or hashlib.sha256(str(row["payload_json"]).encode("utf-8")).hexdigest()
                != row["payload_hash"]
                or not isinstance(payload, dict)
                or payload.get("project_id") != identity["project_id"]
                or payload.get("book_id") != identity["book_id"]
                or payload.get("schema_version") != 1
                or payload.get("record_kind") != row["record_kind"]
                or payload.get("record_id") != row["record_id"]
                or row["record_kind"] not in _GAP018_RECORD_KINDS
                or not isinstance(payload.get("value"), dict)):
            raise SchemaMigrationError("GAP-018 record scope or hash is invalid")
    for row in conn.execute(
        "SELECT project_id,book_id,source_master_id,version,head_hash FROM gap018_source_head"
    ).fetchall():
        if ((row["project_id"], row["book_id"]) != (identity["project_id"], identity["book_id"])
                or not row["source_master_id"] or type(row["version"]) is not int
                or row["version"] < 1 or len(str(row["head_hash"])) != 64):
            raise SchemaMigrationError("GAP-018 current Source head is invalid")
        source = conn.execute(
            "SELECT payload_json FROM gap018_records WHERE project_id=? AND book_id=? "
            "AND record_kind='SOURCE' AND record_id=?",
            (row["project_id"], row["book_id"], row["source_master_id"]),
        ).fetchone()
        source_value = (None if source is None else
                        json.loads(str(source["payload_json"])).get("value", {}))
        if (not isinstance(source_value, dict)
                or source_value.get("source_master_id") != row["source_master_id"]
                or source_value.get("version") != row["version"]
                or source_value.get("artifact_hash") != row["head_hash"]
                or source_value.get("status") != "SOURCE_COMMITTED"
                or source_value.get("approved_by_user") is not True):
            raise SchemaMigrationError("GAP-018 current Source has no matching committed record")
    for row in conn.execute(
        "SELECT project_id,book_id,manuscript_id,version,manifest_hash FROM gap018_manuscript_head"
    ).fetchall():
        if ((row["project_id"], row["book_id"]) != (identity["project_id"], identity["book_id"])
                or not row["manuscript_id"] or type(row["version"]) is not int
                or row["version"] < 1 or len(str(row["manifest_hash"])) != 64):
            raise SchemaMigrationError("GAP-018 current Manuscript head is invalid")
        manuscript = conn.execute(
            "SELECT payload_json FROM gap018_records WHERE project_id=? AND book_id=? "
            "AND record_kind='MANUSCRIPT' AND record_id=?",
            (row["project_id"], row["book_id"], row["manuscript_id"]),
        ).fetchone()
        value = (None if manuscript is None else
                 json.loads(str(manuscript["payload_json"])).get("value", {}))
        if (not isinstance(value, dict)
                or value.get("manuscript_id") != row["manuscript_id"]
                or value.get("version") != row["version"]
                or value.get("manifest_hash") != row["manifest_hash"]
                or value.get("status") != "SEALED"):
            raise SchemaMigrationError("GAP-018 current Manuscript has no matching sealed record")


def _validate_project_schema_v7(conn: sqlite3.Connection) -> None:
    _validate_project_schema_v7_step(conn)
    if _read_schema_version(conn) != 7:
        raise SchemaMigrationError("project schema version is invalid")


PROJECT_DB_MIGRATIONS = (
    SchemaMigration(
        source_version=1,
        target_version=2,
        apply=_apply_project_schema_v1_to_v2,
        validate=_validate_project_schema_v2,
    ),
    SchemaMigration(
        source_version=2,
        target_version=3,
        apply=_apply_project_schema_v2_to_v3,
        validate=_validate_project_schema_v3,
    ),
    SchemaMigration(
        source_version=3,
        target_version=4,
        apply=_apply_project_schema_v3_to_v4,
        validate=_validate_project_schema_v4,
    ),
    SchemaMigration(
        source_version=4,
        target_version=5,
        apply=_apply_project_schema_v4_to_v5,
        validate=_validate_project_schema_v5,
    ),
    SchemaMigration(
        source_version=5,
        target_version=6,
        apply=_apply_project_schema_v5_to_v6,
        validate=_validate_project_schema_v6_step,
        validate_reached=_validate_project_schema_v6,
    ),
    SchemaMigration(
        source_version=6,
        target_version=7,
        apply=_apply_project_schema_v6_to_v7,
        validate=_validate_project_schema_v7_step,
        validate_reached=_validate_project_schema_v7,
    ),
)


def _create_series_memory_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_memberships (
            project_id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_state_records (
            scope_type TEXT NOT NULL CHECK (scope_type = 'SERIES'),
            scope_id TEXT NOT NULL,
            state_kind TEXT NOT NULL,
            record_type TEXT NOT NULL,
            record_id TEXT NOT NULL,
            source_project_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, state_kind, record_type, record_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS volume_closing_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL CHECK (scope_type = 'SERIES'),
            scope_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            book_id TEXT NOT NULL,
            source_state_version INTEGER NOT NULL,
            semantic_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE (scope_type, scope_id, project_id, book_id, semantic_hash)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_operations (
            operation_id TEXT PRIMARY KEY,
            semantic_hash TEXT NOT NULL,
            result_type TEXT NOT NULL,
            result_id TEXT NOT NULL,
            result_payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS series_snapshots_lookup
        ON volume_closing_snapshots (
            scope_type, scope_id, source_state_version DESC, snapshot_id
        )
        """
    )


def _apply_series_schema_v1_to_v2(conn: sqlite3.Connection) -> None:
    _create_series_memory_tables(conn)
    if _table_exists(conn, "series_identity"):
        conn.execute("UPDATE series_identity SET schema_version = 2 WHERE id = 1")


def _validate_series_schema_v2(conn: sqlite3.Connection) -> None:
    conn.execute("SELECT project_id, book_id, payload_json FROM series_memberships LIMIT 0")
    conn.execute(
        "SELECT scope_type, scope_id, state_kind, record_type, record_id, "
        "source_project_id, payload_json FROM series_state_records LIMIT 0"
    )
    conn.execute(
        "SELECT snapshot_id, scope_type, scope_id, project_id, book_id, "
        "source_state_version, semantic_hash, payload_json "
        "FROM volume_closing_snapshots LIMIT 0"
    )
    conn.execute(
        "SELECT operation_id, semantic_hash, result_type, result_id, "
        "result_payload_json FROM series_operations LIMIT 0"
    )


def _create_series_edges_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS edges (
            scope_type TEXT NOT NULL CHECK (scope_type = 'SERIES'),
            scope_id TEXT NOT NULL,
            edge_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, edge_id)
        )
        """
    )
    for side in ("source", "target"):
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS series_edges_{side} "
            f"ON edges(scope_type, scope_id, {side}_id, edge_id)"
        )


def _apply_series_schema_v2_to_v3(conn: sqlite3.Connection) -> None:
    _create_series_edges_table(conn)
    if _table_exists(conn, "series_identity"):
        conn.execute("UPDATE series_identity SET schema_version = 3 WHERE id = 1")


def _validate_series_schema_v3(conn: sqlite3.Connection) -> None:
    _validate_series_base_schema(conn)
    _validated_identity_singleton(
        conn,
        table="series_identity",
        identity_columns=("series_id",),
        expected_version=3,
        error_message="series v3 identity is invalid",
    )


def _create_series_memory_ledger_tables(conn: sqlite3.Connection, *, replace_triggers: bool = False) -> None:
    create_memory_ledger_schema(
        conn,
        scope_type=ScopeType.SERIES.value,
        identity_table="series_identity",
        identity_column="series_id",
        replace_triggers=replace_triggers,
    )


def _apply_series_schema_v3_to_v4(conn: sqlite3.Connection) -> None:
    _create_series_memory_ledger_tables(conn)
    _set_memory_ledger_control(conn, metadata_table="series_metadata", state="SCHEMA_READY")
    if _table_exists(conn, "series_identity"):
        conn.execute("UPDATE series_identity SET schema_version = 4 WHERE id = 1")


def _validate_series_schema_v4_step(conn: sqlite3.Connection) -> None:
    _validate_series_base_schema(conn)
    _validate_no_authoritative_base_triggers(
        conn, tables=_SERIES_BASE_TABLE_CONTRACTS,
    )
    validate_memory_ledger_schema(conn, scope_type=ScopeType.SERIES.value)
    _validate_exact_user_table_set(
        conn, (*_SERIES_BASE_TABLE_CONTRACTS, "memory_events", "memory_event_entities"),
    )
    identity = _validated_identity_singleton(
        conn,
        table="series_identity",
        identity_columns=("series_id",),
        expected_version=SERIES_DB_SCHEMA_VERSION,
        error_message="series identity schema version is invalid",
    )
    try:
        control = _read_validated_memory_ledger_control(conn, metadata_table="series_metadata")
        if control["state"] == "ACTIVE":
            _validate_active_memory_ledger(
                conn,
                control=control,
                scope_type=ScopeType.SERIES.value,
                scope_id=str(identity["series_id"]),
            )
        elif (
            int(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]) != 0
            or int(conn.execute("SELECT COUNT(*) FROM memory_event_entities").fetchone()[0]) != 0
        ):
            raise MemoryLedgerIntegrityError("SCHEMA_READY series ledger is not empty")
    except (MemoryLedgerError, sqlite3.Error) as exc:
        raise SchemaMigrationError("invalid series memory ledger control") from exc


def _validate_series_schema_v4(conn: sqlite3.Connection) -> None:
    _validate_series_schema_v4_step(conn)
    if _read_schema_version(conn) != SERIES_DB_SCHEMA_VERSION:
        raise SchemaMigrationError("series schema version is invalid")


SERIES_DB_MIGRATIONS = (
    SchemaMigration(
        source_version=1,
        target_version=2,
        apply=_apply_series_schema_v1_to_v2,
        validate=_validate_series_schema_v2,
    ),
    SchemaMigration(
        source_version=2,
        target_version=3,
        apply=_apply_series_schema_v2_to_v3,
        validate=_validate_series_schema_v3,
    ),
    SchemaMigration(
        source_version=3,
        target_version=4,
        apply=_apply_series_schema_v3_to_v4,
        validate=_validate_series_schema_v4_step,
        validate_reached=_validate_series_schema_v4,
    ),
)


def _insert_scoped_edge(
    conn: sqlite3.Connection,
    repository_scope: StorageScope,
    record: EdgeRecord,
    *,
    source_scope: StorageScope,
    target_scope: StorageScope,
    error_type: type[ValueError],
) -> None:
    from app.p20_core.domain_records import EdgeRecord

    if not isinstance(record, EdgeRecord):
        raise error_type("graph write requires an EdgeRecord")
    record = EdgeRecord(**record.to_dict())
    for scope in (record.scope, source_scope, target_scope):
        _require_matching_scope(repository_scope, scope, error_type)
    conn.execute(
        "INSERT INTO edges (scope_type, scope_id, edge_id, source_id, target_id, "
        "relation_type, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (record.scope_type, record.scope_id, record.edge_id, str(record.source_id),
         str(record.target_id), record.relation_type.value, record.to_json()),
    )


def _decode_scoped_edge(repository: Any, row: sqlite3.Row, error_type: type[ValueError]) -> EdgeRecord:
    from app.p20_core.domain_records import EdgeRecord

    try:
        record = EdgeRecord(**json.loads(row["payload_json"]))
        repository.require_scope(record.scope)
        serialized = record.to_dict()
        for key in ("scope_type", "scope_id", "edge_id", "source_id", "target_id", "relation_type"):
            if serialized[key] != row[key]:
                raise error_type(f"edge index/payload mismatch: {key}")
        return record
    except (TypeError, ValueError, KeyError) as exc:
        raise error_type("malformed scoped edge") from exc


def _traverse_scoped_dependencies(
    repository: Any,
    start: GraphNodeRef,
    policy: DependencyTraversalPolicy | None,
    *,
    error_type: type[ValueError],
    read_connection: Callable[[], Any],
) -> tuple[DependencyTraversalStep, ...]:
    from app.p20_core.project_graph import (
        DependencyTraversalPolicy, DependencyTraversalStep, GraphNodeRef,
        GraphTraversalLimitError, TraversalDirection,
    )

    if not isinstance(start, GraphNodeRef):
        raise error_type("traversal requires a scoped GraphNodeRef")
    repository.require_scope(start.scope)
    policy = DependencyTraversalPolicy() if policy is None else policy
    if not isinstance(policy, DependencyTraversalPolicy):
        raise error_type("traversal requires a DependencyTraversalPolicy")
    if not policy.read_only:
        repository.initialize()
    pending = deque([(start.node_id, 0)])
    visited_nodes = {str(start.node_id)}
    visited_edges: set[str] = set()
    examined_edges: set[str] = set()
    result = []
    connection = read_connection() if policy.read_only else repository.connect()
    directions = None if policy.relation_directions is None else dict(policy.relation_directions)
    with connection as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN")
        while pending:
            node, depth = pending.popleft()
            if depth >= policy.max_depth or policy.relation_types == ():
                continue
            params: list[Any] = [repository.scope.scope_type.value, repository.scope.scope_id]
            if policy.direction == TraversalDirection.BOTH:
                predicate = "(source_id = ? OR target_id = ?)"
                params.extend([str(node), str(node)])
            else:
                side = "target" if policy.direction == TraversalDirection.INCOMING else "source"
                predicate = f"{side}_id = ?"
                params.append(str(node))
            if policy.relation_types is not None:
                predicate += " AND relation_type IN (" + ",".join("?" for _ in policy.relation_types) + ")"
                params.extend(relation.value for relation in policy.relation_types)
            params.append(policy.max_edges + 1)
            rows = conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? AND "
                + predicate + " ORDER BY edge_id LIMIT ?", params,
            )
            for row in rows:
                edge = repository._decode_edge(row)
                if edge.edge_id in visited_edges:
                    continue
                if edge.edge_id not in examined_edges:
                    if len(examined_edges) >= policy.max_edges:
                        raise GraphTraversalLimitError("dependency traversal max_edges exceeded")
                    examined_edges.add(edge.edge_id)
                if directions is not None:
                    direction = directions.get(edge.relation_type)
                    outgoing = str(edge.source_id) == str(node)
                    if (direction is None
                            or (direction == TraversalDirection.OUTGOING and not outgoing)
                            or (direction == TraversalDirection.INCOMING and outgoing)):
                        continue
                if len(visited_edges) >= policy.max_edges:
                    raise GraphTraversalLimitError("dependency traversal max_edges exceeded")
                visited_edges.add(edge.edge_id)
                target = edge.target_id if str(edge.source_id) == str(node) else edge.source_id
                result.append(DependencyTraversalStep(edge, depth + 1, node, target))
                if str(target) not in visited_nodes:
                    visited_nodes.add(str(target))
                    pending.append((target, depth + 1))
    return tuple(result)


class ProjectDomainTransaction:
    def __init__(self, conn: sqlite3.Connection, scope: StorageScope) -> None:
        self._conn = conn
        self._scope = _coerce_storage_scope(scope, ProjectStorageError)

    @property
    def scope(self) -> StorageScope:
        return self._scope

    def add_edge(
        self, record: EdgeRecord, *, source_scope: StorageScope, target_scope: StorageScope,
    ) -> None:
        _insert_scoped_edge(
            self._conn, self.scope, record, source_scope=source_scope,
            target_scope=target_scope, error_type=ProjectStorageError,
        )

    def add_fact_record(
        self,
        fact_id: str,
        payload_json: str,
        *,
        scope: StorageScope | None = None,
    ) -> None:
        record_scope = self._scope if scope is None else _require_matching_scope(
            self._scope,
            scope,
            ProjectStorageError,
        )
        self._conn.execute(
            """
            INSERT INTO project_fact_records (scope_type, scope_id, fact_id, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                record_scope.scope_type.value,
                record_scope.scope_id,
                _normalize_domain_id(fact_id, "fact_id"),
                str(payload_json),
            ),
        )

    def add_character_state(
        self,
        state_id: str,
        character_id: str,
        payload_json: str,
        *,
        scope: StorageScope | None = None,
    ) -> None:
        record_scope = self._scope if scope is None else _require_matching_scope(
            self._scope,
            scope,
            ProjectStorageError,
        )
        self._conn.execute(
            """
            INSERT INTO project_character_states (scope_type, scope_id, state_id, character_id, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                record_scope.scope_type.value,
                record_scope.scope_id,
                _normalize_domain_id(state_id, "state_id"),
                _normalize_domain_id(character_id, "character_id"),
                str(payload_json),
            ),
        )

    def add_structured_memory_record(
        self,
        record: Any,
        *,
        scope: StorageScope | None = None,
        mutation_source: MutationSource | str = MutationSource.AUTOMATION,
        actor_id: str | None = None,
        mutation_policy: MutationPolicy = DEFAULT_MUTATION_POLICY,
    ) -> None:
        record_scope = self._scope if scope is None else _require_matching_scope(
            self._scope,
            scope,
            ProjectStorageError,
        )
        declared_scope = getattr(record, "scope", None)
        if declared_scope is not None:
            _require_matching_scope(record_scope, declared_scope, ProjectStorageError)
        if hasattr(record, "require_project_scope"):
            record.require_project_scope(record_scope)

        record_type = _normalize_domain_id(
            getattr(record, "memory_record_type", ""),
            "record_type",
        )
        record_id = _normalize_domain_id(
            str(getattr(record, "record_id", "")),
            "record_id",
        )
        to_json = getattr(record, "to_json", None)
        if not callable(to_json):
            raise ProjectStorageError("structured memory record must provide to_json")
        payload_json = str(to_json())
        existing = self._conn.execute(
            """
            SELECT payload_json
            FROM project_structured_memory_records
            WHERE scope_type = ?
              AND scope_id = ?
              AND record_type = ?
              AND record_id = ?
            """,
            (
                record_scope.scope_type.value,
                record_scope.scope_id,
                record_type,
                record_id,
            ),
        ).fetchone()
        mutation_type = MutationType.CREATE if existing is None else MutationType.UPDATE
        decision = DomainMutationGuard(mutation_policy).evaluate(
            current_payload=None if existing is None else str(existing["payload_json"]),
            proposed_payload=payload_json,
            context=MutationContext(
                project_id=record_scope.scope_id,
                record_type=record_type,
                record_id=record_id,
                mutation_type=mutation_type,
                source=mutation_source,
                actor_id=actor_id,
            ),
        )
        if decision.denied:
            raise ProjectStorageError(
                f"domain mutation denied: {decision.reason_code.value}"
            )

        self._conn.execute(
            """
            INSERT INTO project_structured_memory_records (
                scope_type,
                scope_id,
                record_type,
                record_id,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope_type, scope_id, record_type, record_id)
            DO UPDATE SET payload_json = excluded.payload_json
            """,
            (
                record_scope.scope_type.value,
                record_scope.scope_id,
                record_type,
                record_id,
                payload_json,
            ),
        )


class StorageResolver:
    def __init__(self, storage_root: Path | None = None) -> None:
        selected = storage_root if storage_root is not None else get_storage_root()
        self._storage_root = selected.expanduser().resolve(strict=False)

    @property
    def storage_root(self) -> Path:
        return self._storage_root

    def resolve_project(
        self,
        project_id: str,
        *,
        book_id: str | None = None,
    ) -> ProjectStorageContext:
        resolved_project_id = _normalize_identifier(project_id, "project_id")
        resolved_book_id = _normalize_identifier(book_id or resolved_project_id, "book_id")
        storage_root = self.storage_root
        projects_root = storage_root / "projects"
        book_root = storage_root / "books" / resolved_book_id
        project_root = projects_root / resolved_project_id
        project_db_path = project_root / PROJECT_DB_FILENAME

        _assert_relative_to(projects_root, storage_root, "projects_root")
        _assert_relative_to(project_root, storage_root, "project_root")
        _assert_relative_to(book_root, storage_root, "book_root")
        _assert_relative_to(project_db_path, storage_root, "project_db_path")

        return ProjectStorageContext(
            project_id=resolved_project_id,
            book_id=resolved_book_id,
            storage_root=storage_root,
            projects_root=projects_root,
            project_root=project_root,
            book_root=book_root,
            project_db_path=project_db_path,
        )

    def resolve_book(
        self,
        book_id: str,
        *,
        project_id: str | None = None,
    ) -> ProjectStorageContext:
        return self.resolve_project(project_id or book_id, book_id=book_id)

    def resolve_series(self, series_id: str) -> SeriesStorageContext:
        resolved_series_id = _normalize_identifier(series_id, "series_id", SeriesStorageError)
        storage_root = self.storage_root
        series_collection_root = storage_root / "series"
        series_root = series_collection_root / resolved_series_id
        database_path = series_root / SERIES_DB_FILENAME

        _assert_relative_to(series_collection_root, storage_root, "series_collection_root", SeriesStorageError)
        _assert_relative_to(series_root, storage_root, "series_root", SeriesStorageError)
        _assert_relative_to(database_path, storage_root, "database_path", SeriesStorageError)

        return SeriesStorageContext(
            series_id=resolved_series_id,
            storage_root=storage_root,
            series_collection_root=series_collection_root,
            series_root=series_root,
            database_path=database_path,
        )

    def resolve_optional_series(self, series_id: str | None) -> SeriesStorageContext | None:
        if series_id is None or not str(series_id).strip():
            return None
        return self.resolve_series(series_id)

    def resolve_series_access(
        self,
        project_id: str,
        series_id: str,
    ) -> SeriesAccessContext:
        project_context = self.resolve_project(project_id)
        series_context = self.resolve_series(series_id)
        return SeriesAccessContext(
            project_scope=project_context.scope,
            series_scope=series_context.scope,
        )

    def resolve_optional_series_access(
        self,
        project_id: str,
        series_id: str | None,
    ) -> SeriesAccessContext | None:
        if series_id is None or not str(series_id).strip():
            return None
        return self.resolve_series_access(project_id, series_id)

    def resolve_system(self) -> SystemStorageContext:
        storage_root = self.storage_root
        database_path = storage_root / SYSTEM_DB_FILENAME

        _assert_relative_to(database_path, storage_root, "database_path", SystemStorageError)

        return SystemStorageContext(
            storage_root=storage_root,
            database_path=database_path,
        )


class ProjectRepository:
    def __init__(self, context: ProjectStorageContext) -> None:
        self.context = context

    @property
    def db_path(self) -> Path:
        return self.context.project_db_path

    def _validated_database_path(self, *, create_parent: bool) -> Path:
        return _validated_repository_database_path(
            storage_root=self.context.storage_root,
            database_parent=self.context.project_root,
            database_path=self.context.project_db_path,
            error_type=ProjectStorageError,
            create_parent=create_parent,
        )

    @property
    def scope(self) -> StorageScope:
        return self.context.scope

    def require_scope(self, scope: StorageScope) -> None:
        _require_matching_scope(self.scope, scope, ProjectStorageError)

    @contextmanager
    def connect(self, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        if read_only:
            conn = self._memory_ledger_read_connection()
            try:
                conn.execute("BEGIN")
                _validate_project_schema_v7(conn)
                identity = conn.execute("SELECT project_id, book_id FROM project_identity WHERE id=1").fetchone()
                if identity is None or (identity["project_id"], identity["book_id"]) != (
                    self.context.project_id, self.context.book_id,
                ):
                    raise ProjectStorageError("project.db identity does not match repository context")
                self._require_memory_ledger_active(conn)
                yield conn
            finally:
                conn.close()
            return
        conn = _connect_repository_database(
            storage_root=self.context.storage_root,
            database_parent=self.context.project_root,
            database_path=self.context.project_db_path,
            error_type=ProjectStorageError,
            create_parent=True,
            timeout=_DOMAIN_DB_BUSY_TIMEOUT_MS / 1000,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {_DOMAIN_DB_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys = ON")
            # Re-applying journal_mode requires a write lock. Concurrent retry
            # owners only need to verify the already-established mode.
            current_journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            if current_journal_mode != "wal":
                # Concurrent first opens can both observe the pre-WAL mode. SQLite
                # may then return SQLITE_BUSY immediately while one connection
                # changes the journal, despite the configured busy_timeout. No
                # domain transaction has started, so retry only this idempotent
                # connection initialization step.
                import time
                deadline = time.monotonic() + _DOMAIN_DB_BUSY_TIMEOUT_MS / 1000
                while True:
                    try:
                        conn.execute("PRAGMA journal_mode = WAL")
                        break
                    except sqlite3.OperationalError as exc:
                        if (
                            getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_BUSY
                            or time.monotonic() >= deadline
                        ):
                            raise
                        time.sleep(0.01)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            # Schema creation and its version marker are one initialization
            # transaction. Concurrent first opens must not observe the user
            # tables before schema_version is committed.
            conn.execute("BEGIN IMMEDIATE")
            new_store = _read_schema_version(conn) is None
            _initialize_schema_version(
                conn,
                required_version=PROJECT_DB_SCHEMA_VERSION,
                database_name="project.db",
                error_type=ProjectStorageError,
            )
            if not new_store:
                _validate_project_schema_v7(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_identity (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    project_id TEXT NOT NULL,
                    book_id TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_fact_records (
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    fact_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_character_states (
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    state_id TEXT PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            _create_project_structured_memory_table(conn)
            _create_project_edges_table(conn)
            _create_context_packages_table(conn)
            _create_adaptive_style_tables(conn)
            self._ensure_identity(conn)
            _create_project_memory_ledger_tables(conn)
            _create_gap018_tables(conn)
            if new_store:
                self._activate_empty_memory_ledger(conn)
            _validate_project_schema_v7(conn)

    def _ensure_identity(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT project_id, book_id FROM project_identity WHERE id = 1"
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO project_identity (id, project_id, book_id, schema_version)
                VALUES (1, ?, ?, ?)
                """,
                (
                    self.context.project_id,
                    self.context.book_id,
                    PROJECT_DB_SCHEMA_VERSION,
                ),
            )
            return

        if row["project_id"] != self.context.project_id or row["book_id"] != self.context.book_id:
            raise ProjectStorageError("project.db identity does not match repository context")

    def _memory_ledger_control(self, conn: sqlite3.Connection) -> dict[str, Any]:
        return _read_validated_memory_ledger_control(conn, metadata_table="project_metadata")

    def _require_memory_ledger_active(self, conn: sqlite3.Connection) -> dict[str, Any]:
        control = self._memory_ledger_control(conn)
        if control.get("state") != "ACTIVE":
            raise MemoryLedgerNotActive("memory ledger is not active")
        _validate_active_memory_ledger(
            conn,
            control=control,
            scope_type=ScopeType.PROJECT.value,
            scope_id=self.context.project_id,
        )
        return control

    def _activate_empty_memory_ledger(self, conn: sqlite3.Connection) -> None:
        manifest_hash = _ledger_sha256_text(_ledger_canonical_json([]))
        event = build_memory_event(
            sequence=_ledger_next_sequence(conn), scope_type=ScopeType.PROJECT.value,
            scope_id=self.context.project_id,
            operation={"namespace": "LEDGER_BOOTSTRAP", "id": f"bootstrap-v1:PROJECT:{self.context.project_id}"},
            event_slot=("activated",), event_type="LEDGER_ACTIVATED",
            actor={"kind": "SYSTEM", "id": "MEMORY_LEDGER_BOOTSTRAP_V1", "evidence_ref": None},
            project_id=self.context.project_id, book_id=self.context.book_id, series_id=None,
            structured_payload={"baseline_count": 0, "manifest_hash": manifest_hash,
                                "coverage": MEMORY_LEDGER_COVERAGE, "origin": "EMPTY_STORE"},
        )
        _append_memory_event(conn, event)
        _set_memory_ledger_control(
            conn, metadata_table="project_metadata", state="ACTIVE",
            bootstrap_operation_id=event.operation["id"], activation_event_id=event.memory_event_id,
            baseline_count=0, manifest_hash=manifest_hash,
        )

    def append_memory_event(self, connection: sqlite3.Connection, event: MemoryEventRecord) -> MemoryEventRecord:
        if not isinstance(connection, sqlite3.Connection):
            raise ProjectStorageError("memory ledger append requires owner connection")
        self._require_memory_ledger_active(connection)
        if event.scope_type != ScopeType.PROJECT.value or event.scope_id != self.context.project_id:
            raise ProjectStorageError("memory event scope does not match project repository")
        if event.project_id != self.context.project_id or event.book_id != self.context.book_id:
            raise ProjectStorageError("memory event project identity does not match repository")
        if event.operation["namespace"] == "LEDGER_BOOTSTRAP":
            raise ProjectStorageError("ledger bootstrap is internal maintenance only")
        try:
            return _append_memory_event(connection, event)
        except MemoryLedgerError:
            raise
        except sqlite3.Error as exc:
            raise ProjectStorageError("memory ledger append failed") from exc

    def record_memory_event(
        self,
        connection: sqlite3.Connection,
        *,
        operation_namespace: str,
        operation_id: str,
        event_slot: Iterable[str],
        event_type: str,
        actor: dict[str, str | None],
        structured_payload: dict[str, Any],
        run_id: str | None = None,
        step_id: str | None = None,
        entity_refs: Iterable[dict[str, Any]] = (),
        parent_refs: Iterable[dict[str, Any]] = (),
        artifact_refs: Iterable[dict[str, Any]] = (),
        source_refs: Iterable[dict[str, Any]] = (),
    ) -> MemoryEventRecord:
        """Build and append one PROJECT event on the owner's write connection."""
        event = build_memory_event(
            sequence=_ledger_next_sequence(connection),
            scope_type=ScopeType.PROJECT.value,
            scope_id=self.context.project_id,
            operation={"namespace": operation_namespace, "id": operation_id},
            event_slot=event_slot,
            event_type=event_type,
            actor=actor,
            project_id=self.context.project_id,
            book_id=self.context.book_id,
            series_id=None,
            run_id=run_id,
            step_id=step_id,
            entity_refs=entity_refs,
            parent_refs=parent_refs,
            artifact_refs=artifact_refs,
            source_refs=source_refs,
            structured_payload=structured_payload,
        )
        return self.append_memory_event(connection, event)

    def classify_required_memory_event(
        self,
        *,
        operation_namespace: str,
        operation_id: str,
        event_slot: Iterable[str],
        baseline_locator: str | None = None,
        baseline_category: str | None = None,
        baseline_identity: tuple[str, str] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        """Classify a missing required event without inventing historical events."""
        owns_connection = connection is None
        conn = self._memory_ledger_read_connection() if owns_connection else connection
        try:
            self._require_memory_ledger_active(conn)
            return _classify_required_memory_event(
                conn,
                scope_type=ScopeType.PROJECT.value,
                scope_id=self.context.project_id,
                operation_namespace=operation_namespace,
                operation_id=operation_id,
                event_slot=event_slot,
                baseline_locator=baseline_locator,
                baseline_category=baseline_category,
                baseline_identity=baseline_identity,
            )
        finally:
            if owns_connection:
                conn.close()

    def _memory_ledger_read_connection(self) -> sqlite3.Connection:
        database_path = self._validated_database_path(create_parent=False)
        if not database_path.exists():
            raise MemoryLedgerMigrationRequired("project ledger database is absent")
        conn = _connect_repository_database(
            storage_root=self.context.storage_root,
            database_parent=self.context.project_root,
            database_path=self.context.project_db_path,
            error_type=ProjectStorageError,
            create_parent=False,
            readonly=True,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            version = _read_schema_version(conn)
            if version is None or version < PROJECT_DB_SCHEMA_VERSION:
                raise MemoryLedgerMigrationRequired("project ledger migration is required")
            if version > PROJECT_DB_SCHEMA_VERSION:
                raise MemoryLedgerMigrationRequired("project ledger schema is newer than supported")
            validate_memory_ledger_schema(conn, scope_type=ScopeType.PROJECT.value)
            return conn
        except BaseException:
            # A failed read preflight must release SQLite and Windows path pins.
            conn.close()
            raise

    def get_memory_event(self, memory_event_id: str) -> MemoryEventRecord | None:
        conn = self._memory_ledger_read_connection()
        try:
            self._require_memory_ledger_active(conn)
            return _read_memory_event(conn, memory_event_id)
        finally:
            conn.close()

    def validate_memory_event_references(self, event: MemoryEventRecord) -> tuple[str, ...]:
        """Resolve only local ledger parents; foreign scopes stay non-enumerable."""
        statuses: list[str] = []
        for reference in (*event.parent_refs, *event.artifact_refs, *event.source_refs):
            if reference["owner_scope_type"] != ScopeType.PROJECT.value or reference["owner_scope_id"] != self.context.project_id:
                statuses.append("NOT_AUTHORIZED")
                continue
            if reference["kind"] != "MEMORY_EVENT":
                statuses.append("LEGACY_UNVERIFIABLE")
                continue
            target = self.get_memory_event(reference["locator"])
            if target is None:
                statuses.append("MISSING")
            elif reference["hash"] is not None and reference["hash"] != target.content_hash:
                statuses.append("HASH_MISMATCH")
            else:
                statuses.append("VERIFIED")
        return tuple(statuses)

    def list_memory_events(
        self, *, cursor: str | None = None, limit: int = 100,
        operation: tuple[str, str] | None = None, entity: tuple[str, str] | None = None,
    ) -> tuple[tuple[MemoryEventRecord, ...], str | None]:
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ProjectStorageError("memory ledger limit must be between 1 and 200")
        conn = self._memory_ledger_read_connection()
        try:
            conn.execute("BEGIN")
            control = self._require_memory_ledger_active(conn)
            filter_data = {"operation": list(operation) if operation else None, "entity": list(entity) if entity else None}
            if cursor is None:
                high_row = conn.execute(
                    "SELECT sequence,content_hash FROM memory_events ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                if high_row is None:
                    raise MemoryLedgerIntegrityError("active ledger has no activation event")
                after, high_watermark = 0, int(high_row["sequence"])
                high_watermark_content_hash = str(high_row["content_hash"])
            else:
                after, high_watermark = _decode_memory_ledger_cursor(
                    cursor,
                    conn=conn,
                    scope_type=ScopeType.PROJECT.value,
                    scope_id=self.context.project_id,
                    activation_event_id=str(control.get("activation_event_id") or ""),
                    filter_data=filter_data,
                    error_type=ProjectStorageError,
                )
                high_row = conn.execute(
                    "SELECT content_hash FROM memory_events WHERE sequence = ?", (high_watermark,)
                ).fetchone()
                high_watermark_content_hash = str(high_row["content_hash"])
            where, params = ["sequence > ?", "sequence <= ?"], [after, high_watermark]
            if operation is not None:
                where.extend(["operation_namespace = ?", "operation_id = ?"])
                params.extend(operation)
            if entity is not None:
                where.append("EXISTS (SELECT 1 FROM memory_event_entities e WHERE e.sequence = memory_events.sequence AND e.record_type = ? AND e.entity_id = ?)")
                params.extend(entity)
            rows = conn.execute("SELECT memory_event_id FROM memory_events WHERE " + " AND ".join(where) + " ORDER BY sequence LIMIT ?", (*params, limit + 1)).fetchall()
            events = tuple(
                _read_memory_event(conn, str(row["memory_event_id"])) for row in rows[:limit]
            )
            if any(event is None for event in events):
                raise MemoryLedgerIntegrityError("memory ledger page contains a missing event")
            if len(rows) <= limit:
                return events, None
            next_cursor = _encode_memory_ledger_cursor(
                scope_type=ScopeType.PROJECT.value,
                scope_id=self.context.project_id,
                activation_event_id=str(control.get("activation_event_id") or ""),
                filter_data=filter_data,
                after_sequence=events[-1].sequence,
                high_watermark=high_watermark,
                high_watermark_content_hash=high_watermark_content_hash,
            )
            return events, next_cursor
        finally:
            conn.close()

    def bootstrap_memory_ledger(self, *, maintenance_confirmed: bool = False) -> MemoryEventRecord:
        """Maintenance-only activation for an already migrated PROJECT store."""
        if maintenance_confirmed is not True:
            raise MemoryLedgerNotActive("memory ledger bootstrap requires confirmed maintenance exclusivity")
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            control = self._memory_ledger_control(conn)
            if control.get("state") == "ACTIVE":
                return _validate_active_memory_ledger(
                    conn,
                    control=control,
                    scope_type=ScopeType.PROJECT.value,
                    scope_id=self.context.project_id,
                )
            if control.get("state") != "SCHEMA_READY":
                raise MemoryLedgerNotActive("project ledger is not ready for bootstrap")
            _memory_ledger_bootstrap_preflight(
                conn,
                scope_type=ScopeType.PROJECT.value,
                scope_id=self.context.project_id,
                project_id=self.context.project_id,
                book_id=self.context.book_id,
            )
            operation_id = f"bootstrap-v1:PROJECT:{self.context.project_id}"
            manifest: list[dict[str, str]] = []
            sequence = _ledger_next_sequence(conn)
            sources: tuple[tuple[str, str, str | None], ...] = (
                ("project_structured_memory_records", "PROJECT_STRUCTURED_MEMORY", "scope_id = ?"),
                ("project_fact_records", "PROJECT_FACT", "scope_id = ?"),
                ("project_character_states", "PROJECT_CHARACTER_STATE", "scope_id = ?"),
                ("edges", "PROJECT_EDGE", "scope_id = ?"),
            )
            for table, category, condition in sources:
                rows = _bootstrap_table_rows(
                    conn,
                    table=table,
                    condition=condition,
                    parameters=(self.context.project_id,),
                )
                for _row, locator, raw in rows:
                    digest = _ledger_sha256_text(raw)
                    event = build_memory_event(
                        sequence=sequence, scope_type=ScopeType.PROJECT.value, scope_id=self.context.project_id,
                        operation={"namespace": "LEDGER_BOOTSTRAP", "id": operation_id},
                        event_slot=("object", category, locator), event_type="LEDGER_BASELINE_OBJECT",
                        actor={"kind": "SYSTEM", "id": "MEMORY_LEDGER_BOOTSTRAP_V1", "evidence_ref": None},
                        project_id=self.context.project_id, book_id=self.context.book_id, series_id=None,
                        structured_payload={"category": category, "locator": locator, "bytes_hash": digest,
                                            "snapshot_text": raw, "observed_schema_version": PROJECT_DB_SCHEMA_VERSION,
                                            "history_completeness": "UNKNOWN_BEFORE_BOUNDARY"},
                    )
                    _append_memory_event(conn, event)
                    manifest.append({"category": category, "locator": locator, "bytes_hash": digest})
                    sequence += 1
            metadata_rows = _validated_bootstrap_metadata_rows(
                conn, metadata_table="project_metadata", scope_type="PROJECT",
            )
            for row in metadata_rows:
                raw, locator = str(row["value"]), f"metadata:{row['key']}"
                digest = _ledger_sha256_text(raw)
                event = build_memory_event(
                    sequence=sequence, scope_type=ScopeType.PROJECT.value, scope_id=self.context.project_id,
                    operation={"namespace": "LEDGER_BOOTSTRAP", "id": operation_id},
                    event_slot=("object", "PROJECT_METADATA", locator), event_type="LEDGER_BASELINE_OBJECT",
                    actor={"kind": "SYSTEM", "id": "MEMORY_LEDGER_BOOTSTRAP_V1", "evidence_ref": None},
                    project_id=self.context.project_id, book_id=self.context.book_id, series_id=None,
                    structured_payload={"category": "PROJECT_METADATA", "locator": locator, "bytes_hash": digest,
                                        "snapshot_text": raw, "observed_schema_version": PROJECT_DB_SCHEMA_VERSION,
                                        "history_completeness": "UNKNOWN_BEFORE_BOUNDARY"},
                )
                _append_memory_event(conn, event)
                manifest.append({"category": "PROJECT_METADATA", "locator": locator, "bytes_hash": digest})
                sequence += 1
            manifest_hash = _ledger_sha256_text(_ledger_canonical_json(manifest))
            activation = build_memory_event(
                sequence=sequence, scope_type=ScopeType.PROJECT.value, scope_id=self.context.project_id,
                operation={"namespace": "LEDGER_BOOTSTRAP", "id": operation_id}, event_slot=("activated",),
                event_type="LEDGER_ACTIVATED",
                actor={"kind": "SYSTEM", "id": "MEMORY_LEDGER_BOOTSTRAP_V1", "evidence_ref": None},
                project_id=self.context.project_id, book_id=self.context.book_id, series_id=None,
                structured_payload={"baseline_count": len(manifest), "manifest_hash": manifest_hash,
                                    "coverage": MEMORY_LEDGER_COVERAGE, "origin": "LEGACY_CUTOVER"},
            )
            _append_memory_event(conn, activation)
            _set_memory_ledger_control(conn, metadata_table="project_metadata", state="ACTIVE",
                bootstrap_operation_id=operation_id, activation_event_id=activation.memory_event_id,
                baseline_count=len(manifest), manifest_hash=manifest_hash)
            return activation

    def get_schema_version(self) -> int:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            return int(row["version"])

    def get_project_identity(self) -> dict[str, str | int]:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT project_id, book_id, schema_version
                FROM project_identity
                WHERE id = 1
                """
            ).fetchone()
            return {
                "project_id": row["project_id"],
                "book_id": row["book_id"],
                "schema_version": int(row["schema_version"]),
            }

    def set_metadata(self, key: str, value: str) -> None:
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO project_metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(key), str(value)),
            )

    @contextmanager
    def model_invocation_transaction(self, operation_id: str, *, connection=None):
        """Short local audit transaction, or participation in an owner transaction."""
        if connection is None:
            self.initialize()
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                with self.model_invocation_transaction(operation_id, connection=conn) as state:
                    yield state
            return
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ProjectStorageError("model operation_id required")
        key = "model_invocation.v1:" + operation_id
        row = connection.execute("SELECT value FROM project_metadata WHERE key=?", (key,)).fetchone()
        state = {} if row is None else json.loads(row["value"])
        def validate_scope():
            if state and (state.get("project_id") != self.scope.scope_id
                          or state.get("book_id") != self.context.book_id):
                raise ProjectStorageError("model invocation scope mismatch")
        validate_scope()
        yield state
        validate_scope()
        if state:
            connection.execute("INSERT INTO project_metadata(key,value) VALUES (?,?) "
                               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (key, json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)))

    def list_model_invocations(self, *, run_id: str | None = None, connection=None) -> list[dict]:
        if connection is None:
            self.initialize()
            with self.connect() as conn:
                return self.list_model_invocations(run_id=run_id, connection=conn)
        rows = connection.execute("SELECT value FROM project_metadata "
                                "WHERE substr(key,1,20)='model_invocation.v1:' ORDER BY key").fetchall()
        result = []
        for row in rows:
            item = json.loads(row["value"])
            if (item.get("project_id") != self.scope.scope_id
                    or item.get("book_id") != self.context.book_id):
                raise ProjectStorageError("model invocation scope mismatch")
            if run_id is None or item["run_id"] == run_id:
                result.append(item)
        return result

    @contextmanager
    def evaluation_transaction(self, operation_id: str, *, connection=None):
        """Serialize one project-owned GAP-017 evaluation envelope."""
        if connection is None:
            self.initialize()
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                with self.evaluation_transaction(operation_id, connection=conn) as state:
                    yield state
            return
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ProjectStorageError("evaluation operation_id required")
        key = "evaluation.v1:" + operation_id
        row = connection.execute(
            "SELECT value FROM project_metadata WHERE key=?", (key,)
        ).fetchone()
        try:
            state = {} if row is None else json.loads(row["value"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProjectStorageError("evaluation envelope is not valid JSON") from exc

        def validate_scope() -> None:
            if state.get("project_id") not in {None, self.scope.scope_id}:
                raise ProjectStorageError("evaluation project scope mismatch")
            if state.get("book_id") not in {None, self.context.book_id}:
                raise ProjectStorageError("evaluation book scope mismatch")

        validate_scope()
        yield state
        validate_scope()
        if state:
            connection.execute(
                "INSERT INTO project_metadata(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (
                    key,
                    json.dumps(
                        state,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                        allow_nan=False,
                    ),
                ),
            )

            # START and its recoverable envelope become durable together. A
            # request reservation is not an Evaluation execution_status.
            self.checkpoint_reevaluation_request(
                connection, operation_id, "evaluations", state.get("evaluation_id")
            )

    def get_evaluation_envelope(self, operation_id: str) -> dict | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM project_metadata WHERE key=?",
                ("evaluation.v1:" + str(operation_id),),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["value"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProjectStorageError("evaluation envelope is not valid JSON") from exc
        if value.get("project_id") != self.scope.scope_id:
            raise ProjectStorageError("evaluation project scope mismatch")
        if value.get("book_id") != self.context.book_id:
            raise ProjectStorageError("evaluation book scope mismatch")
        return value

    def list_evaluation_envelopes(self, *, run_id: str | None = None) -> list[dict]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT value FROM project_metadata WHERE key LIKE ? ORDER BY key",
                ("evaluation.v1:%",),
            ).fetchall()
        result = []
        for row in rows:
            try:
                item = json.loads(row["value"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ProjectStorageError("evaluation envelope is not valid JSON") from exc
            if item.get("project_id") != self.scope.scope_id:
                raise ProjectStorageError("evaluation project scope mismatch")
            if item.get("book_id") != self.context.book_id:
                raise ProjectStorageError("evaluation book scope mismatch")
            if run_id is None or item.get("run_id") == run_id:
                result.append(item)
        return result

    def claim_reevaluation_request(
        self,
        *,
        request_identity: str,
        run_id: str,
        series_id: str | None,
        reevaluation_of: str,
        request_hash: str,
        assigned_step_id: str,
    ) -> dict[str, Any]:
        """Atomically bind a stable REEVALUATE request to one new operation."""
        identity = str(request_identity).strip()
        if not identity:
            raise ProjectStorageError("reevaluation request identity required")
        run = str(run_id).strip()
        source = str(reevaluation_of).strip()
        fingerprint = str(request_hash).strip()
        step_id = str(assigned_step_id).strip()
        if not all((run, source, fingerprint, step_id)):
            raise ProjectStorageError("reevaluation request binding is incomplete")

        scope_preimage = json.dumps(
            {
                "request_identity": identity,
                "run_id": run,
                "series_id": series_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        key = "reevaluation_request.v1:" + hashlib.sha256(
            scope_preimage.encode("utf-8")
        ).hexdigest()
        expected = {
            "schema_version": "GAP017_REEVALUATION_REQUEST_V1",
            "project_id": self.scope.scope_id,
            "book_id": self.context.book_id,
            "series_id": series_id,
            "run_id": run,
            "request_identity": identity,
            "reevaluation_of": source,
            "request_hash": fingerprint,
        }

        self.initialize()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT value FROM project_metadata WHERE key=?", (key,)
            ).fetchone()
            if row is not None:
                try:
                    stored = json.loads(row["value"])
                except (TypeError, json.JSONDecodeError):
                    stored = None
                if isinstance(stored, dict) and stored.get("reservation_status") == "NEEDS_INTERVENTION":
                    return {**stored, "request_reused": True}
                reason = None
                if not isinstance(stored, dict):
                    stored = {"unreadable_binding": row["value"]}
                    reason = "request binding is not a valid object"
                elif any(name not in stored for name in expected):
                    reason = "request binding is incomplete"
                elif (not stored.get("assigned_step_id") or
                      stored.get("assigned_operation_id") !=
                      f"context:{self.scope.scope_id}:{run}:{stored['assigned_step_id']}"):
                    reason = "assigned identifiers are unprovable"
                if reason is not None:
                    stored.update(reservation_status="NEEDS_INTERVENTION",
                                  intervention_reason=reason)
                    stored.setdefault("audit", []).append({
                        "event": "NEEDS_INTERVENTION", "reason": reason,
                    })
                    self._write_reevaluation_request(connection, key, stored)
                    return {**stored, "request_reused": True}
                if any(stored.get(name) != value for name, value in expected.items()):
                    raise ProjectStorageError(
                        "reevaluation request identity is already bound to different inputs"
                    )
                return {**stored, "request_reused": True}

            assigned_operation_id = (
                f"context:{self.scope.scope_id}:{run}:{step_id}"
            )
            stored = {
                **expected,
                "assigned_step_id": step_id,
                "assigned_operation_id": assigned_operation_id,
                "reservation_status": "RESERVED",
                "input_hash": None,
                "contexts": {},
                "evaluations": {},
                "audit": [{"event": "RESERVED"}],
            }
            connection.execute(
                "INSERT INTO project_metadata(key,value) VALUES (?,?)",
                (
                    key,
                    json.dumps(
                        stored,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                        allow_nan=False,
                    ),
                ),
            )
            return {**stored, "request_reused": False}

    def _reevaluation_request_row(self, connection, operation_id: str):
        """Find the reservation owning this root or child operation."""
        rows = connection.execute(
            "SELECT key,value FROM project_metadata WHERE key LIKE ?",
            ("reevaluation_request.v1:%",),
        ).fetchall()
        for row in rows:
            try:
                value = json.loads(row["value"])
            except (TypeError, json.JSONDecodeError):
                continue  # The keyed claim path records malformed bindings.
            if not isinstance(value, dict):
                continue
            root = value.get("assigned_operation_id")
            if isinstance(root, str) and root and (operation_id == root or operation_id.startswith(root + ":")):
                return row["key"], value
        return None

    @staticmethod
    def _write_reevaluation_request(connection, key: str, value: dict) -> None:
        connection.execute(
            "UPDATE project_metadata SET value=? WHERE key=?",
            (json.dumps(value, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=True, allow_nan=False), key),
        )

    def checkpoint_reevaluation_request(
        self, connection, operation_id: str, checkpoint: str, value: str,
    ) -> None:
        """Checkpoint in the SAME short transaction as the owned data write."""
        found = self._reevaluation_request_row(connection, operation_id)
        if found is None:
            return
        key, request = found
        # A checkpoint write is not a migration. In particular, completed
        # reads and operator recovery must not partially upgrade a legacy
        # reservation to STARTED without its existing context bindings.
        # The runtime validates/adopts the complete legacy state on re-entry.
        if "reservation_status" not in request:
            return
        if not isinstance(value, str) or not value:
            raise ProjectStorageError("reevaluation initialization checkpoint is incomplete")
        if request.get("reservation_status") == "NEEDS_INTERVENTION":
            from app.p20_core.evaluation import EvaluationNeedsIntervention
            raise EvaluationNeedsIntervention("REEVALUATION_REQUEST_NEEDS_INTERVENTION")
        if checkpoint == "input_hash":
            previous = request.get(checkpoint)
            request[checkpoint] = value
        else:
            previous = request.setdefault(checkpoint, {}).get(operation_id)
            request[checkpoint][operation_id] = value
        if previous is not None and previous != value:
            raise ProjectStorageError("reevaluation initialization checkpoint changed")
        if checkpoint == "evaluations":
            request["reservation_status"] = "STARTED"
        if previous is None:
            request.setdefault("audit", []).append({
                "event": "BOUND_" + checkpoint.upper(), "operation_id": operation_id,
            })
        self._write_reevaluation_request(connection, key, request)

    def reevaluation_request_requires_retry(
        self, operation_id: str, *, allow_unreserved_retry: bool = False,
    ) -> bool:
        """Validate durable initialization under the caller's book/run locks.

        No execution right is granted here: Evaluation START still atomically
        claims the attempt with its complete binding and fencing token.
        """
        from app.p20_core.evaluation import EvaluationNeedsIntervention, EvaluationIntegrityError
        self.initialize()
        reason = None
        started = False
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            found = self._reevaluation_request_row(connection, operation_id)
            if found is None:
                if allow_unreserved_retry:
                    # Ordinary P20 operations and unkeyed REEVALUATE have no
                    # request reservation; retain their existing retry path.
                    return True
                raise EvaluationNeedsIntervention("REEVALUATION_REQUEST_BINDING_NOT_RECORDED")
            key, request = found
            input_row = connection.execute(
                "SELECT value FROM project_metadata WHERE key=?",
                ("p20_execution_input.v1:" + operation_id,),
            ).fetchone()
            input_hash = (hashlib.sha256(input_row["value"].encode("utf-8")).hexdigest()
                          if input_row is not None else None)
            packages = connection.execute(
                "SELECT operation_id,context_hash,payload_json FROM context_packages "
                "WHERE substr(operation_id,1,?)=?",
                (len(operation_id) + 1, operation_id + ":"),
            ).fetchall()
            evaluations = connection.execute(
                "SELECT value FROM project_metadata WHERE substr(key,1,?)=?",
                (len("evaluation.v1:" + operation_id + ":"),
                 "evaluation.v1:" + operation_id + ":"),
            ).fetchall()
            try:
                contexts = {}
                for row in packages:
                    package = self._decode_context_package(row["payload_json"])
                    if (package.project_id != self.context.project_id
                            or package.book_id != self.context.book_id
                            or package.run_id != request["run_id"]
                            or package.context_hash != row["context_hash"]):
                        raise ValueError("context identity mismatch")
                    contexts[row["operation_id"]] = package.context_hash
                envelopes = [json.loads(row["value"]) for row in evaluations]
                actual_evaluations = {item["operation_id"]: item["evaluation_id"]
                                      for item in envelopes}
                # Older reservations had no initialization checkpoints. Adopt
                # only a provable started envelope; absence cannot prove that
                # the former implementation never started an attempt.
                if request.get("reservation_status") is None:
                    from app.p20_core.evaluation import _binding_from_envelope
                    if input_hash is None or not envelopes:
                        raise ValueError("legacy reservation has no execution proof")
                    for envelope in envelopes:
                        binding = _binding_from_envelope(envelope)
                        if (binding.project_id != self.context.project_id
                                or binding.book_id != self.context.book_id
                                or binding.run_id != request["run_id"]
                                or binding.reevaluation_of != request["reevaluation_of"]
                                or contexts.get(binding.operation_id) != binding.context_hash):
                            raise ValueError("legacy binding mismatch")
                    request.update(reservation_status="STARTED", input_hash=input_hash,
                                   contexts=contexts, evaluations=actual_evaluations)
                    request.setdefault("audit", []).append({"event": "LEGACY_STARTED_VERIFIED"})
                status = request.get("reservation_status")
                if status not in {"RESERVED", "STARTED"}:
                    raise ValueError("reservation state unknown or intervention required")
                if (request.get("input_hash") != input_hash
                        or request.get("contexts") != contexts
                        or request.get("evaluations") != actual_evaluations
                        or (contexts and input_hash is None)
                        or (status == "STARTED") != bool(envelopes)):
                    raise ValueError("initialization checkpoint does not match durable data")
                started = status == "STARTED"
            except (ValueError, TypeError, KeyError, EvaluationIntegrityError) as exc:
                reason = str(exc)
                if request.get("reservation_status") != "NEEDS_INTERVENTION":
                    request["reservation_status"] = "NEEDS_INTERVENTION"
                    request["intervention_reason"] = reason
                    request.setdefault("audit", []).append({
                        "event": "NEEDS_INTERVENTION", "reason": reason,
                    })
            self._write_reevaluation_request(connection, key, request)
        # Raise after commit, so operators can read the reason without an
        # EvaluationRecord. Never fabricate an evaluation to enable recovery.
        if reason is not None:
            raise EvaluationNeedsIntervention("REEVALUATION_REQUEST_NEEDS_INTERVENTION")
        # An explicit technical retry must never become an unbound first
        # start. RESERVED first entry still requires the keyed request/hash.
        return started or allow_unreserved_retry

    def set_scoped_metadata(self, scope: StorageScope, key: str, value: str) -> None:
        self.require_scope(scope)
        self.set_metadata(key, value)

    @contextmanager
    def canonical_proposal_transaction(self, proposal_id: str, *, include_writer: bool = False,
                                       read_only: bool = False):
        """Serialize proposal/decision writes with the current project state.

        Process records live in existing project metadata, never system storage.
        The snapshot includes edges so changed dependencies invalidate a review.
        """
        proposal_id = _normalize_identifier(proposal_id, "proposal_id")
        if not read_only:
            self.initialize()
        with self.connect(read_only=read_only) as conn:
            if not read_only:
                conn.execute("BEGIN IMMEDIATE")
            key = "canonical_proposal.v1:" + proposal_id
            row = conn.execute("SELECT value FROM project_metadata WHERE key = ?", (key,)).fetchone()
            document = {} if row is None else json.loads(row["value"])
            records = [dict(r) for r in conn.execute(
                "SELECT record_type, record_id, payload_json FROM project_structured_memory_records "
                "WHERE scope_type = ? AND scope_id = ? ORDER BY record_type, record_id",
                (self.scope.scope_type.value, self.scope.scope_id),
            )]
            edges = [dict(r) for r in conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? ORDER BY edge_id",
                (self.scope.scope_type.value, self.scope.scope_id),
            )]
            snapshot = {"records": records, "edges": edges}
            # Only v2 research proposals bind research state. Original extraction
            # snapshots and hashes remain byte-for-byte compatible.
            if document and document["versions"][document["current_version"]]["proposal"].get("source_kind") == "RESEARCH":
                snapshot["research"] = self.read_research_state(conn)
            if include_writer:
                yield document, snapshot, conn
            else:
                yield document, snapshot
            if document and not read_only:
                conn.execute(
                    "INSERT INTO project_metadata (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)),
                )

    def read_research_state(self, connection=None) -> dict:
        if connection is None:
            self.initialize()
            with self.connect() as conn:
                return self.read_research_state(conn)
        row = connection.execute("SELECT value FROM project_metadata WHERE key='research.v1'").fetchone()
        state = json.loads(row["value"]) if row else {
            "schema_version": 1, "project_id": self.scope.scope_id,
            "book_id": self.context.book_id, "records": {}, "sources": {},
            "claims": {}, "operations": {}, "conflicts": {}, "decisions": {},
        }
        if (state.get("schema_version") != 1 or state.get("project_id") != self.scope.scope_id
                or state.get("book_id") != self.context.book_id
                or any(item.get("project_id") != self.scope.scope_id
                       for table in ("records", "sources") for versions in state[table].values() for item in versions)
                or any(claim.get("research_id") not in state["records"]
                       for versions in state["claims"].values() for claim in versions)):
            raise ProjectStorageError("research evidence scope mismatch")
        return state

    @contextmanager
    def research_transaction(self, *, include_writer: bool = False):
        """Project-local research, immutable evidence and operation receipts."""
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self.read_research_state(conn)
            yield (state, conn) if include_writer else state
            self.write_research_state(conn, state)

    def write_research_state(self, connection, state):
        if state["project_id"] != self.scope.scope_id or state["book_id"] != self.context.book_id:
            raise ProjectStorageError("research scope mismatch")
        connection.execute(
            "INSERT INTO project_metadata(key,value) VALUES('research.v1',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False),),
        )

    @contextmanager
    def canonical_pipeline_operation(self, operation_id: str, *, include_writer: bool = False):
        """Serialize one accepted-artifact operation, including its durable result."""
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            key = "canonical_pipeline.v1:" + operation_id
            row = conn.execute("SELECT value FROM project_metadata WHERE key = ?", (key,)).fetchone()
            state = {} if row is None else json.loads(row["value"])
            yield (state, conn) if include_writer else state
            conn.execute("INSERT INTO project_metadata (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, json.dumps(state, sort_keys=True, allow_nan=False)))

    @contextmanager
    def cross_store_operation_transaction(self, operation_id: str):
        """Serialize one durable cross-store operation owned by project.db."""
        operation_id = _normalize_identifier(operation_id, "operation_id")
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            key = "cross_store_operation.v1:" + operation_id
            row = conn.execute(
                "SELECT value FROM project_metadata WHERE key = ?",
                (key,),
            ).fetchone()
            state = {} if row is None else json.loads(str(row["value"]))
            yield state, conn
            if state:
                conn.execute(
                    "INSERT INTO project_metadata (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (
                        key,
                        json.dumps(
                            state,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                    ),
                )

    def is_finalized_chapter_artifact(
        self, *, relative_path: str, artifact_sha256: str,
        chapter_id: str, versions: tuple[int, ...],
    ) -> bool:
        """Read the F4 owner, final audit and ledger in one read-only snapshot.

        A prepared file is not publication authority. Missing or inconsistent
        finalization evidence keeps it outside accepted chapter projections.
        """
        from app.p20_core.cross_store_recovery import CrossStoreOperationPlan, CrossStoreRecoveryError

        try:
            with self.connect(read_only=True) as conn:
                rows = conn.execute(
                    "SELECT key,value FROM project_metadata "
                    "WHERE key LIKE 'cross_store_operation.v1:%'"
                ).fetchall()
                matching = []
                for row in rows:
                    record = json.loads(str(row["value"]))
                    payload = record.get("payload") if isinstance(record, dict) else None
                    if not isinstance(payload, dict):
                        continue
                    if (
                        record.get("operation_type") == "CHAPTER_ARTIFACT_LINEAGE_V2"
                        and payload.get("artifact_root") == "BOOKS"
                        and payload.get("artifact_relative_path") == relative_path
                    ):
                        matching.append((str(row["key"]), record))
                if len(matching) != 1:
                    return False
                key, record = matching[0]
                operation_id = record.get("operation_id")
                if (
                    key != "cross_store_operation.v1:" + str(operation_id)
                    or record.get("status") != "COMMITTED"
                    or (not isinstance(record.get("scope"), dict)
                        or record["scope"].get("type") != "PROJECT"
                        or record["scope"].get("project_id") != self.context.project_id
                        or record["scope"].get("book_id") != self.context.book_id)
                    or record.get("owner_store") != "project.db"
                    or record.get("completed_writes") != record.get("planned_writes")
                    or set(record.get("completed_writes") or ()) != {
                        "PROJECT_CHECKPOINT", "FILE_ARTIFACT", "LOGICAL_COMMIT", "FINAL_AUDIT",
                    }
                ):
                    return False
                plan = CrossStoreOperationPlan.from_record(record)
                if (
                    plan.input_hash != record.get("input_hash")
                    or plan.project_id != self.context.project_id
                    or plan.book_id != self.context.book_id
                    or plan.artifact_sha256 != artifact_sha256
                    or plan.artifact_relative_path != relative_path
                ):
                    return False
                audit_row = conn.execute(
                    "SELECT value FROM project_metadata WHERE key=?",
                    ("cross_store_audit.v1:" + operation_id,),
                ).fetchone()
                if audit_row is None:
                    return False
                audit = json.loads(str(audit_row["value"]))
                if (
                    not isinstance(audit, dict)
                    or audit.get("operation_id") != operation_id
                    or audit.get("input_hash") != plan.input_hash
                    or audit.get("status") != "COMMITTED"
                    or audit.get("completed_writes") != record["completed_writes"]
                ):
                    return False
                slots = (
                    (("intent",), "ARTIFACT_WRITE_INTENDED"),
                    (("confirmed",), "ARTIFACT_WRITE_CONFIRMED"),
                ) + tuple(
                    (("chapter-version", chapter_id, str(version)), "CHAPTER_VERSION_RECORDED")
                    for version in versions
                )
                for slot, event_type in slots:
                    event_id = _ledger_memory_event_id(
                        "PROJECT", self.context.project_id,
                        {"namespace": "CROSS_STORE_OPERATION", "id": operation_id}, slot,
                    )
                    event = _read_memory_event(conn, event_id)
                    if event is not None:
                        payload = event.structured_payload
                        if (
                            event.event_type != event_type
                            or payload.get("input_hash") != plan.input_hash
                            or payload.get("artifact_type") != plan.operation_type
                            or payload.get("confined_path") != relative_path
                        ):
                            return False
                        if event_type == "ARTIFACT_WRITE_INTENDED" and payload.get("expected_hash") != artifact_sha256:
                            return False
                        if event_type == "ARTIFACT_WRITE_CONFIRMED" and (
                            payload.get("expected_hash") != artifact_sha256
                            or payload.get("observed_hash") != artifact_sha256
                        ):
                            return False
                        if event_type == "CHAPTER_VERSION_RECORDED" and payload.get("observed_hash") != artifact_sha256:
                            return False
                        continue
                    if _classify_required_memory_event(
                        conn, scope_type="PROJECT", scope_id=self.context.project_id,
                        operation_namespace="CROSS_STORE_OPERATION",
                        operation_id=operation_id, event_slot=slot,
                        baseline_locator="metadata:cross_store_operation.v1:" + operation_id,
                    ) != "LEGACY_BEFORE_LEDGER":
                        return False
                return True
        except (ValueError, TypeError, KeyError, sqlite3.Error, CrossStoreRecoveryError):
            return False

    def get_cross_store_operation(self, operation_id: str) -> dict[str, Any] | None:
        operation_id = _normalize_identifier(operation_id, "operation_id")
        value = self.get_metadata("cross_store_operation.v1:" + operation_id)
        return None if value is None else dict(json.loads(value))

    def get_cross_store_operation_readonly(self, operation_id: str) -> dict[str, Any] | None:
        operation_id = _normalize_identifier(operation_id, "operation_id")
        value = self.get_metadata_readonly("cross_store_operation.v1:" + operation_id)
        return None if value is None else dict(json.loads(value))

    def list_cross_store_operations(self) -> tuple[dict[str, Any], ...]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT value FROM project_metadata "
                "WHERE key LIKE 'cross_store_operation.v1:%' ORDER BY key"
            ).fetchall()
        return tuple(dict(json.loads(str(row["value"]))) for row in rows)

    def apply_canonical_record_set(self, connection, proposal: dict, snapshot: dict,
                                   *, impact: dict, approval: dict | None) -> dict:
        """Called only under the proposal transaction after final guard validation.

        Version history, current records, invalidation and commit receipt share
        this connection. No filesystem or Series writes participate.
        """
        self.require_scope(StorageScope(proposal["scope_type"], proposal["scope_id"]))
        if proposal.get("source_kind") == "RESEARCH" or proposal.get("authority_ref") == "P20_VERIFIED_RESEARCH_V1":
            from app.p20_core.research import validate_promotion_evidence, verified_evidence, digest
            from app.p20_core.canon_service import canonical_proposal_hash, _validate_review_proposal
            if not connection.in_transaction:
                raise ProjectStorageError("research mutation requires local transaction")
            _validate_review_proposal(self, proposal)
            row = connection.execute("SELECT value FROM project_metadata WHERE key=?",
                                     ("canonical_proposal.v1:" + proposal["proposal_id"],)).fetchone()
            saved = {} if row is None else json.loads(row["value"])
            record = saved.get("versions", {}).get(saved.get("current_version"), {})
            if (record.get("proposal", {}).get("proposal_hash") != proposal["proposal_hash"]
                    or canonical_proposal_hash(proposal) != proposal["proposal_hash"]
                    or approval is None or record.get("decision") != approval
                    or record.get("impact") != impact):
                raise ProjectStorageError("research requires persisted bound operator approval")
            actual = {"records": [dict(r) for r in connection.execute(
                "SELECT record_type,record_id,payload_json FROM project_structured_memory_records "
                "WHERE scope_type=? AND scope_id=? ORDER BY record_type,record_id",
                ("PROJECT", self.scope.scope_id))],
                "edges": [dict(r) for r in connection.execute(
                    "SELECT * FROM edges WHERE scope_type=? AND scope_id=? ORDER BY edge_id",
                    ("PROJECT", self.scope.scope_id))], "research": self.read_research_state(connection)}
            if actual != snapshot or digest(actual) != record["basis_hash"]:
                raise ProjectStorageError("stale physical research write basis")
            challenge = record.get("challenges", {}).get(approval.get("challenge_id"), {})
            if (not challenge.get("used") or challenge.get("proposal_hash") != proposal["proposal_hash"]
                    or challenge.get("basis_hash") != record["basis_hash"]
                    or challenge.get("operator_id") != approval.get("operator_id")):
                raise ProjectStorageError("research approval challenge unavailable")
            validate_promotion_evidence(proposal, actual["research"])
            evidence = verified_evidence(actual["research"], proposal["research_evidence"]["operation_id"])
            for operation in [evidence["operation"], *evidence["extractions"].values()]:
                call = operation["invocation"]
                package_row = connection.execute("SELECT payload_json FROM context_packages WHERE operation_id=?",
                                                 (call["call_id"],)).fetchone()
                if package_row is None:
                    raise ProjectStorageError("research context unavailable")
                package = self._decode_context_package(package_row["payload_json"])
                if package.context_hash != call["context_hash"] or package.context_package_id != call["context_package_id"]:
                    raise ProjectStorageError("research context binding mismatch")
        decision = DomainMutationGuard().evaluate_canonical(
            proposal=proposal, snapshot=snapshot, impact=impact, approval=approval)
        if decision["outcome"] != "ALLOW":
            raise ProjectStorageError("canonical mutation denied: " + decision["reason"])
        current = {(r["record_type"], r["record_id"]): r["payload_json"] for r in snapshot["records"]}
        for mutation in proposal["proposed_mutations"]:
            kind, identity = mutation["target_entity_type"], mutation["target_entity_id"]
            payload = json.dumps(mutation["proposed_state"], sort_keys=True, separators=(",", ":"), allow_nan=False)
            history_key = "canonical_versions.v1:" + kind + ":" + identity
            previous = connection.execute("SELECT value FROM project_metadata WHERE key=?", (history_key,)).fetchone()
            history = [] if previous is None else json.loads(previous["value"])
            if not history and (kind, identity) in current:
                history.append(json.loads(current[(kind, identity)]))
            history.append(mutation["proposed_state"])
            connection.execute("INSERT INTO project_metadata(key,value) VALUES (?,?) "
                               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (history_key, json.dumps(history)))
            connection.execute("INSERT INTO project_structured_memory_records "
                "(scope_type,scope_id,record_type,record_id,payload_json) VALUES (?,?,?,?,?) "
                "ON CONFLICT(scope_type,scope_id,record_type,record_id) DO UPDATE SET payload_json=excluded.payload_json",
                ("PROJECT", self.scope.scope_id, kind, identity, payload))
            entity_refs = [{"record_type": kind, "entity_id": identity,
                            "version": mutation["proposed_state"]["version"]}]
            commit_operation_ref = hashlib.sha256(json.dumps(
                {key: proposal[key] for key in
                 ("project_id", "scope_type", "scope_id", "proposal_id", "proposal_hash")},
                sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")).hexdigest()
            self.record_memory_event(
                connection,
                operation_namespace="CANONICAL_PROPOSAL",
                operation_id=proposal["proposal_id"],
                event_slot=("entity", str(proposal["proposal_version"]), kind, identity),
                event_type="CANONICAL_ENTITY_CHANGED",
                actor={"kind": "SYSTEM", "id": "CANONICAL_CHANGE_V1", "evidence_ref": None},
                run_id=proposal["run_id"], step_id=proposal["step_id"],
                entity_refs=entity_refs,
                structured_payload={
                    "proposal_id": proposal["proposal_id"],
                    "proposal_version": proposal["proposal_version"],
                    "proposal_hash": proposal["proposal_hash"],
                    "entity_type": kind, "entity_id": identity,
                    "old_version": mutation["expected_current_version"],
                    "old_hash": mutation["expected_current_hash"],
                    "new_version": mutation["proposed_state"]["version"],
                    "new_hash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                    "commit_operation_ref": commit_operation_ref,
                    "outcome": mutation["operation_type"],
                },
            )
        return decision

    @contextmanager
    def gap018_transaction(self):
        """Short project-local transaction for request receipts and Source CAS."""
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_memory_ledger_active(conn)
            yield conn

    def get_gap018_record(self, kind: str, record_id: str, *, connection=None) -> dict[str, Any] | None:
        if kind not in _GAP018_RECORD_KINDS:
            raise ProjectStorageError("GAP-018 record kind is invalid")
        record_id = _normalize_identifier(record_id, "GAP-018 record ID")
        if connection is None:
            with self.connect(read_only=True) as conn:
                return self.get_gap018_record(kind, record_id, connection=conn)
        row = connection.execute(
            "SELECT payload_json FROM gap018_records WHERE project_id=? AND book_id=? "
            "AND record_kind=? AND record_id=?",
            (self.context.project_id, self.context.book_id, kind, record_id),
        ).fetchone()
        return None if row is None else dict(json.loads(str(row["payload_json"]))["value"])

    def gap018_candidate_rejected(self, candidate_id: str, *, connection=None) -> bool:
        if connection is None:
            with self.connect(read_only=True) as conn:
                return self.gap018_candidate_rejected(candidate_id, connection=conn)
        rows = connection.execute(
            "SELECT payload_json FROM gap018_records WHERE project_id=? AND book_id=? "
            "AND record_kind='CANDIDATE_STATE'",
            (self.context.project_id, self.context.book_id),
        ).fetchall()
        return any(
            (value := json.loads(str(row["payload_json"]))["value"]).get("candidate_id") == candidate_id
            and value.get("status") == "REJECTED_BY_AUTHOR"
            for row in rows
        )

    def put_gap018_record(self, kind: str, record_id: str, value: Mapping[str, Any], *, connection) -> None:
        if kind not in _GAP018_RECORD_KINDS:
            raise ProjectStorageError("GAP-018 record kind is invalid")
        record_id = _normalize_identifier(record_id, "GAP-018 record ID")
        if not isinstance(value, Mapping):
            raise ProjectStorageError("GAP-018 record must be a mapping")
        envelope = {
            "schema_version": 1,
            "project_id": self.context.project_id,
            "book_id": self.context.book_id,
            "record_kind": kind,
            "record_id": record_id,
            "value": dict(value),
        }
        raw = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                         allow_nan=False)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        existing = connection.execute(
            "SELECT payload_hash FROM gap018_records WHERE project_id=? AND book_id=? "
            "AND record_kind=? AND record_id=?",
            (self.context.project_id, self.context.book_id, kind, record_id),
        ).fetchone()
        if existing is not None:
            if existing["payload_hash"] != digest:
                raise ProjectStorageError("GAP-018 record ID conflict")
            return
        connection.execute(
            "INSERT INTO gap018_records(project_id,book_id,record_kind,record_id,payload_hash,payload_json) "
            "VALUES(?,?,?,?,?,?)",
            (self.context.project_id, self.context.book_id, kind, record_id, digest, raw),
        )

    def update_gap018_promotion(
        self, operation_id: str, expected: Mapping[str, Any], value: Mapping[str, Any], *, connection,
    ) -> None:
        """CAS the mutable technical state; approval and other records remain immutable."""
        operation_id = _normalize_identifier(operation_id, "GAP-018 operation ID")
        row = connection.execute(
            "SELECT payload_hash,payload_json FROM gap018_records WHERE project_id=? AND book_id=? "
            "AND record_kind='PROMOTION' AND record_id=?",
            (self.context.project_id, self.context.book_id, operation_id),
        ).fetchone()
        if row is None or json.loads(str(row["payload_json"])).get("value") != dict(expected):
            raise ProjectStorageError("GAP-018 promotion state changed")
        envelope = {
            "schema_version": 1, "project_id": self.context.project_id,
            "book_id": self.context.book_id, "record_kind": "PROMOTION",
            "record_id": operation_id, "value": dict(value),
        }
        raw = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                         allow_nan=False)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        changed = connection.execute(
            "UPDATE gap018_records SET payload_hash=?,payload_json=? WHERE project_id=? "
            "AND book_id=? AND record_kind='PROMOTION' AND record_id=? AND payload_hash=?",
            (digest, raw, self.context.project_id, self.context.book_id, operation_id,
             row["payload_hash"]),
        ).rowcount
        if changed != 1:
            raise ProjectStorageError("GAP-018 promotion state CAS failed")

    def get_gap018_source_head(self, *, connection=None) -> tuple[str | None, int | None, str | None]:
        if connection is None:
            with self.connect(read_only=True) as conn:
                return self.get_gap018_source_head(connection=conn)
        row = connection.execute(
            "SELECT source_master_id,version,head_hash FROM gap018_source_head "
            "WHERE project_id=? AND book_id=?",
            (self.context.project_id, self.context.book_id),
        ).fetchone()
        return (None, None, None) if row is None else (
            str(row["source_master_id"]), int(row["version"]), str(row["head_hash"]),
        )

    def cas_gap018_source_head(
        self, expected: tuple[str | None, int | None, str | None],
        new: tuple[str, int, str], *, connection,
    ) -> bool:
        """Call only inside gap018_transaction after the Source F-004 commit."""
        if self.get_gap018_source_head(connection=connection) != expected:
            return False
        source = self.get_gap018_record("SOURCE", new[0], connection=connection)
        if source is None or source.get("artifact_hash") != new[2] or source.get("version") != new[1]:
            raise ProjectStorageError("Source head requires a matching durable Source record")
        if expected == (None, None, None):
            connection.execute(
                "INSERT INTO gap018_source_head(project_id,book_id,source_master_id,version,head_hash) "
                "VALUES(?,?,?,?,?)",
                (self.context.project_id, self.context.book_id, *new),
            )
        else:
            connection.execute(
                "UPDATE gap018_source_head SET source_master_id=?,version=?,head_hash=? "
                "WHERE project_id=? AND book_id=?",
                (*new, self.context.project_id, self.context.book_id),
            )
        return True

    def get_gap018_manuscript_head(self, *, connection=None) -> tuple[str | None, int | None, str | None]:
        if connection is None:
            with self.connect(read_only=True) as conn:
                return self.get_gap018_manuscript_head(connection=conn)
        row = connection.execute(
            "SELECT manuscript_id,version,manifest_hash FROM gap018_manuscript_head "
            "WHERE project_id=? AND book_id=?",
            (self.context.project_id, self.context.book_id),
        ).fetchone()
        return (None, None, None) if row is None else (
            str(row["manuscript_id"]), int(row["version"]), str(row["manifest_hash"]),
        )

    def cas_gap018_manuscript_head(
        self, expected: tuple[str | None, int | None, str | None],
        new: tuple[str, int, str], *, connection,
    ) -> bool:
        if self.get_gap018_manuscript_head(connection=connection) != expected:
            return False
        record = self.get_gap018_record("MANUSCRIPT", new[0], connection=connection)
        if (record is None or record.get("manifest_hash") != new[2]
                or record.get("version") != new[1] or record.get("status") != "SEALED"):
            raise ProjectStorageError("Manuscript head requires a durable sealed record")
        if expected == (None, None, None):
            connection.execute(
                "INSERT INTO gap018_manuscript_head(project_id,book_id,manuscript_id,version,manifest_hash) "
                "VALUES(?,?,?,?,?)",
                (self.context.project_id, self.context.book_id, *new),
            )
        else:
            connection.execute(
                "UPDATE gap018_manuscript_head SET manuscript_id=?,version=?,manifest_hash=? "
                "WHERE project_id=? AND book_id=?",
                (*new, self.context.project_id, self.context.book_id),
            )
        return True

    def get_metadata_readonly(self, key: str) -> str | None:
        with self.connect(read_only=True) as conn:
            row = conn.execute("SELECT value FROM project_metadata WHERE key=?", (str(key),)).fetchone()
            return None if row is None else str(row["value"])

    def get_metadata(self, key: str) -> str | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM project_metadata WHERE key = ?",
                (str(key),),
            ).fetchone()
            return None if row is None else str(row["value"])

    def list_metadata(self) -> dict[str, str]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM project_metadata ORDER BY key").fetchall()
            return {str(row["key"]): str(row["value"]) for row in rows}

    def get_pragma(self, name: str) -> str | int:
        with self.connect() as conn:
            row = conn.execute(f"PRAGMA {name}").fetchone()
            return row[0]

    def inspect_schema(self) -> SchemaStatus:
        return _inspect_database_schema(
            storage_root=self.context.storage_root,
            database_parent=self.context.project_root,
            database_path=self.context.project_db_path,
            error_type=ProjectStorageError,
            required_version=PROJECT_DB_SCHEMA_VERSION,
        )

    def migrate_schema(
        self,
        migrations: Iterable[SchemaMigration] | None = None,
        *,
        target_version: int | None = None,
        backup_path: Path | None = None,
        maintenance_confirmed: bool = False,
        release_head: str | None = None,
        backup_deadline_seconds: float = _DOMAIN_DB_BUSY_TIMEOUT_MS / 1000,
    ) -> SchemaStatus:
        database_path = self._validated_database_path(create_parent=True)
        conn = _connect_repository_database(
            storage_root=self.context.storage_root,
            database_parent=self.context.project_root,
            database_path=self.context.project_db_path,
            error_type=ProjectStorageError,
            create_parent=True,
            timeout=_DOMAIN_DB_BUSY_TIMEOUT_MS / 1000,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {_DOMAIN_DB_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys = ON")
            runner = SchemaMigrationRunner(
                target_version=target_version or PROJECT_DB_SCHEMA_VERSION,
                migrations=PROJECT_DB_MIGRATIONS if migrations is None else migrations,
            )
            status = _migration_inspect(runner, conn)
            identity = conn.execute(
                "SELECT project_id,book_id FROM project_identity WHERE id=1"
            ).fetchone()
            if (
                identity is None
                or str(identity["project_id"]) != self.context.project_id
                or str(identity["book_id"]) != self.context.book_id
            ):
                raise SchemaMigrationError("project migration identity does not match repository context")
            before_migrate = None
            if (
                status.current_version is not None
                and status.current_version < 6 <= runner.target_version
            ):
                if status.current_version != 5:
                    raise SchemaMigrationError("memory ledger migration requires project schema P5 entry")
                if maintenance_confirmed is not True:
                    raise SchemaMigrationError("memory ledger migration requires confirmed maintenance exclusivity")
                if not isinstance(release_head, str) or not release_head.strip():
                    raise SchemaMigrationError("memory ledger migration requires release/HEAD")
                selected_backup = backup_path or database_path.with_name(database_path.name + ".memory-ledger-v1.backup")
                expected_identity = {
                    "project_id": self.context.project_id,
                    "book_id": self.context.book_id,
                    "schema_version": 5,
                }

                def before_migrate(migration_conn: sqlite3.Connection) -> None:
                    _preflight_memory_ledger_migration(
                        migration_conn,
                        scope_type="PROJECT",
                        expected_version=5,
                        expected_identity=expected_identity,
                    )
                    _create_verified_sqlite_backup(
                        database_path,
                        selected_backup,
                        expected_version=5,
                        scope_type="PROJECT",
                        scope_id=self.context.project_id,
                        expected_identity=expected_identity,
                        release_head=release_head,
                        deadline_seconds=backup_deadline_seconds,
                    )
            result = runner.migrate(conn, before_migrate=before_migrate)
            identity = conn.execute(
                "SELECT project_id,book_id FROM project_identity WHERE id=1"
            ).fetchone()
            if (
                identity is None
                or str(identity["project_id"]) != self.context.project_id
                or str(identity["book_id"]) != self.context.book_id
            ):
                raise SchemaMigrationError("project migration identity does not match repository context")
            return result
        except sqlite3.OperationalError as exc:
            if _is_sqlite_busy_or_locked(exc):
                raise SchemaMigrationError("MAINTENANCE_BUSY") from exc
            raise
        finally:
            conn.close()

    @contextmanager
    def domain_transaction(self) -> Iterator[ProjectDomainTransaction]:
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN")
            tx = ProjectDomainTransaction(conn, self.scope)
            try:
                yield tx
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _decode_edge(self, row: sqlite3.Row) -> EdgeRecord:
        return _decode_scoped_edge(self, row, ProjectStorageError)

    def get_edge(self, edge_id: str) -> EdgeRecord | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? AND edge_id = ?",
                (self.scope.scope_type.value, self.scope.scope_id, edge_id),
            ).fetchone()
            return None if row is None else self._decode_edge(row)

    def list_edges(self) -> tuple[EdgeRecord, ...]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? ORDER BY edge_id",
                (self.scope.scope_type.value, self.scope.scope_id),
            ).fetchall()
            return tuple(self._decode_edge(row) for row in rows)

    @contextmanager
    def _graph_read_connection(self) -> Iterator[sqlite3.Connection]:
        # Analysis must not initialize a database or run a schema migration.
        database_path = self._validated_database_path(create_parent=False)
        if not database_path.exists():
            raise sqlite3.OperationalError("unable to open database file")
        conn = _connect_repository_database(
            storage_root=self.context.storage_root,
            database_parent=self.context.project_root,
            database_path=self.context.project_db_path,
            error_type=ProjectStorageError,
            create_parent=False,
            readonly=True,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            if _read_schema_version(conn) != PROJECT_DB_SCHEMA_VERSION:
                raise ProjectStorageError("graph analysis requires current schema; use controlled migration")
            identity = conn.execute("SELECT project_id, book_id FROM project_identity WHERE id = 1").fetchone()
            if identity is None or identity["project_id"] != self.context.project_id or identity["book_id"] != self.context.book_id:
                raise ProjectStorageError("project.db identity does not match repository context")
            yield conn
        finally:
            conn.close()

    def traverse_dependencies(
        self, start: GraphNodeRef, policy: DependencyTraversalPolicy | None = None,
    ) -> tuple[DependencyTraversalStep, ...]:
        return _traverse_scoped_dependencies(
            self, start, policy, error_type=ProjectStorageError,
            read_connection=self._graph_read_connection,
        )

    def list_fact_records(self) -> dict[str, str]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT fact_id, payload_json FROM project_fact_records ORDER BY fact_id"
            ).fetchall()
            return {str(row["fact_id"]): str(row["payload_json"]) for row in rows}

    def list_fact_record_scopes(self) -> dict[str, dict[str, str]]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT fact_id, scope_type, scope_id
                FROM project_fact_records
                ORDER BY fact_id
                """
            ).fetchall()
            return {
                str(row["fact_id"]): {
                    "scope_type": str(row["scope_type"]),
                    "scope_id": str(row["scope_id"]),
                }
                for row in rows
            }

    def list_character_states(self) -> dict[str, dict[str, str]]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT state_id, character_id, payload_json
                FROM project_character_states
                ORDER BY state_id
                """
            ).fetchall()
            return {
                str(row["state_id"]): {
                    "character_id": str(row["character_id"]),
                    "payload_json": str(row["payload_json"]),
                }
                for row in rows
            }

    def list_character_state_scopes(self) -> dict[str, dict[str, str]]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT state_id, scope_type, scope_id
                FROM project_character_states
                ORDER BY state_id
                """
            ).fetchall()
            return {
                str(row["state_id"]): {
                    "scope_type": str(row["scope_type"]),
                    "scope_id": str(row["scope_id"]),
                }
                for row in rows
            }

    def list_structured_memory_records(
        self,
        record_type: str | None = None,
    ) -> dict[str, str]:
        self.initialize()
        with self.connect() as conn:
            if record_type is None:
                rows = conn.execute(
                    """
                    SELECT record_id, payload_json
                    FROM project_structured_memory_records
                    ORDER BY record_type, record_id
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT record_id, payload_json
                    FROM project_structured_memory_records
                    WHERE record_type = ?
                    ORDER BY record_id
                    """,
                    (_normalize_domain_id(record_type, "record_type"),),
                ).fetchall()
            return {str(row["record_id"]): str(row["payload_json"]) for row in rows}

    def list_structured_memory_record_scopes(self) -> dict[str, dict[str, str]]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT record_id, record_type, scope_type, scope_id
                FROM project_structured_memory_records
                ORDER BY record_type, record_id
                """
            ).fetchall()
            return {
                str(row["record_id"]): {
                    "record_type": str(row["record_type"]),
                    "scope_type": str(row["scope_type"]),
                    "scope_id": str(row["scope_id"]),
                }
                for row in rows
            }

    @staticmethod
    def _decode_context_package(payload_json: str) -> Any:
        from app.p20_core.context_builder import ContextPackage

        try:
            return ContextPackage.from_dict(json.loads(payload_json))
        except (TypeError, ValueError, KeyError) as exc:
            raise ProjectStorageError("malformed persisted context package") from exc

    def save_context_package(self, package: Any, *, operation_id: str) -> Any:
        from app.p20_core.context_builder import ContextPackage

        if not isinstance(package, ContextPackage):
            raise ProjectStorageError("context package write requires ContextPackage")
        if package.project_id != self.context.project_id:
            raise ProjectStorageError("context package project scope does not match repository")
        if package.book_id != self.context.book_id:
            raise ProjectStorageError("context package book scope does not match repository")
        operation_id = _normalize_domain_id(operation_id, "operation_id")
        self.initialize()
        payload_json = package.to_json()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT payload_json FROM context_packages "
                "WHERE scope_type = ? AND scope_id = ? AND operation_id = ?",
                (ScopeType.PROJECT.value, self.context.project_id, operation_id),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_json"]) != payload_json:
                    raise ProjectStorageError(
                        "context operation identity was already used for a different package"
                    )
                return self._decode_context_package(str(existing["payload_json"]))
            conn.execute(
                """
                INSERT INTO context_packages (
                    scope_type, scope_id, context_package_id, operation_id,
                    run_id, step_id, context_hash, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ScopeType.PROJECT.value,
                    self.context.project_id,
                    package.context_package_id,
                    operation_id,
                    package.run_id,
                    package.step_id,
                    package.context_hash,
                    payload_json,
                ),
            )
            self.checkpoint_reevaluation_request(
                conn, operation_id, "contexts", package.context_hash
            )
        return package

    def get_context_package(self, context_package_id: str) -> Any | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM context_packages "
                "WHERE scope_type = ? AND scope_id = ? AND context_package_id = ?",
                (
                    ScopeType.PROJECT.value,
                    self.context.project_id,
                    _normalize_domain_id(context_package_id, "context_package_id"),
                ),
            ).fetchone()
        return None if row is None else self._decode_context_package(str(row["payload_json"]))

    def get_context_package_for_operation(self, operation_id: str) -> Any | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM context_packages "
                "WHERE scope_type = ? AND scope_id = ? AND operation_id = ?",
                (
                    ScopeType.PROJECT.value,
                    self.context.project_id,
                    _normalize_domain_id(operation_id, "operation_id"),
                ),
            ).fetchone()
        return None if row is None else self._decode_context_package(str(row["payload_json"]))

    def list_context_packages(self) -> tuple[Any, ...]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM context_packages "
                "WHERE scope_type = ? AND scope_id = ? "
                "ORDER BY run_id, step_id, context_package_id",
                (ScopeType.PROJECT.value, self.context.project_id),
            ).fetchall()
        return tuple(
            self._decode_context_package(str(row["payload_json"]))
            for row in rows
        )


class SeriesDomainTransaction:
    def __init__(self, conn: sqlite3.Connection, scope: StorageScope) -> None:
        self._conn = conn
        self._scope = _coerce_storage_scope(scope, SeriesStorageError)

    @property
    def scope(self) -> StorageScope:
        return self._scope

    def add_edge(
        self, record: EdgeRecord, *, source_scope: StorageScope, target_scope: StorageScope,
    ) -> None:
        _insert_scoped_edge(
            self._conn, self.scope, record, source_scope=source_scope,
            target_scope=target_scope, error_type=SeriesStorageError,
        )


class SeriesRepository:
    def __init__(self, context: SeriesStorageContext) -> None:
        self.context = context

    @property
    def db_path(self) -> Path:
        return self.context.database_path

    def _validated_database_path(self, *, create_parent: bool) -> Path:
        return _validated_repository_database_path(
            storage_root=self.context.storage_root,
            database_parent=self.context.series_root,
            database_path=self.context.database_path,
            error_type=SeriesStorageError,
            create_parent=create_parent,
        )

    @property
    def scope(self) -> StorageScope:
        return self.context.scope

    def require_scope(self, scope: StorageScope) -> None:
        _require_matching_scope(self.scope, scope, SeriesStorageError)

    @contextmanager
    def connect(self, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        if read_only:
            conn = self._memory_ledger_read_connection()
            try:
                conn.execute("BEGIN")
                _validate_series_schema_v4(conn)
                identity = conn.execute("SELECT series_id FROM series_identity WHERE id=1").fetchone()
                if identity is None or identity["series_id"] != self.context.series_id:
                    raise SeriesStorageError("series.db identity does not match repository context")
                self._require_memory_ledger_active(conn)
                yield conn
            finally:
                conn.close()
            return
        conn = _connect_repository_database(
            storage_root=self.context.storage_root,
            database_parent=self.context.series_root,
            database_path=self.context.database_path,
            error_type=SeriesStorageError,
            create_parent=True,
            timeout=_DOMAIN_DB_BUSY_TIMEOUT_MS / 1000,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {_DOMAIN_DB_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            new_store = _read_schema_version(conn) is None
            _initialize_schema_version(
                conn,
                required_version=SERIES_DB_SCHEMA_VERSION,
                database_name="series.db",
                error_type=SeriesStorageError,
            )
            if not new_store:
                _validate_series_schema_v4(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS series_identity (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    series_id TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS series_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            _create_series_memory_tables(conn)
            _create_series_edges_table(conn)
            self._ensure_identity(conn)
            _create_series_memory_ledger_tables(conn)
            if new_store:
                self._activate_empty_memory_ledger(conn)
            _validate_series_schema_v4(conn)

    def _ensure_identity(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT series_id FROM series_identity WHERE id = 1"
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO series_identity (id, series_id, schema_version)
                VALUES (1, ?, ?)
                """,
                (
                    self.context.series_id,
                    SERIES_DB_SCHEMA_VERSION,
                ),
            )
            return

        if row["series_id"] != self.context.series_id:
            raise SeriesStorageError("series.db identity does not match repository context")

    def _memory_ledger_control(self, conn: sqlite3.Connection) -> dict[str, Any]:
        return _read_validated_memory_ledger_control(conn, metadata_table="series_metadata")

    def _require_memory_ledger_active(self, conn: sqlite3.Connection) -> dict[str, Any]:
        control = self._memory_ledger_control(conn)
        if control.get("state") != "ACTIVE":
            raise MemoryLedgerNotActive("memory ledger is not active")
        _validate_active_memory_ledger(
            conn,
            control=control,
            scope_type=ScopeType.SERIES.value,
            scope_id=self.context.series_id,
        )
        return control

    def _activate_empty_memory_ledger(self, conn: sqlite3.Connection) -> None:
        manifest_hash = _ledger_sha256_text(_ledger_canonical_json([]))
        event = build_memory_event(
            sequence=_ledger_next_sequence(conn), scope_type=ScopeType.SERIES.value,
            scope_id=self.context.series_id,
            operation={"namespace": "LEDGER_BOOTSTRAP", "id": f"bootstrap-v1:SERIES:{self.context.series_id}"},
            event_slot=("activated",), event_type="LEDGER_ACTIVATED",
            actor={"kind": "SYSTEM", "id": "MEMORY_LEDGER_BOOTSTRAP_V1", "evidence_ref": None},
            project_id=None, book_id=None, series_id=self.context.series_id,
            structured_payload={"baseline_count": 0, "manifest_hash": manifest_hash,
                                "coverage": MEMORY_LEDGER_COVERAGE, "origin": "EMPTY_STORE"},
        )
        _append_memory_event(conn, event)
        _set_memory_ledger_control(
            conn, metadata_table="series_metadata", state="ACTIVE",
            bootstrap_operation_id=event.operation["id"], activation_event_id=event.memory_event_id,
            baseline_count=0, manifest_hash=manifest_hash,
        )

    def append_memory_event(
        self, connection: sqlite3.Connection, event: MemoryEventRecord, *, access: SeriesAccessContext,
    ) -> MemoryEventRecord:
        if not isinstance(connection, sqlite3.Connection):
            raise SeriesStorageError("memory ledger append requires owner connection")
        self._require_access_context(access)
        self._require_registered_member(connection, access)
        self._require_memory_ledger_active(connection)
        if event.scope_type != ScopeType.SERIES.value or event.scope_id != self.context.series_id:
            raise SeriesStorageError("memory event scope does not match series repository")
        if event.series_id != self.context.series_id or event.project_id != access.project_id:
            raise SeriesStorageError("memory event series identity does not match access")
        if event.operation["namespace"] == "LEDGER_BOOTSTRAP":
            raise SeriesAccessError("ledger bootstrap is internal maintenance only")
        return _append_memory_event(connection, event)

    def record_memory_event(
        self,
        connection: sqlite3.Connection,
        *,
        access: SeriesAccessContext,
        operation_namespace: str,
        operation_id: str,
        event_slot: Iterable[str],
        event_type: str,
        actor: dict[str, str | None],
        structured_payload: dict[str, Any],
        book_id: str | None,
        run_id: str | None = None,
        step_id: str | None = None,
        entity_refs: Iterable[dict[str, Any]] = (),
        parent_refs: Iterable[dict[str, Any]] = (),
        artifact_refs: Iterable[dict[str, Any]] = (),
        source_refs: Iterable[dict[str, Any]] = (),
    ) -> MemoryEventRecord:
        """Build and append one SERIES event on the owner's write connection."""
        event = build_memory_event(
            sequence=_ledger_next_sequence(connection),
            scope_type=ScopeType.SERIES.value,
            scope_id=self.context.series_id,
            operation={"namespace": operation_namespace, "id": operation_id},
            event_slot=event_slot,
            event_type=event_type,
            actor=actor,
            project_id=access.project_id,
            book_id=book_id,
            series_id=self.context.series_id,
            run_id=run_id,
            step_id=step_id,
            entity_refs=entity_refs,
            parent_refs=parent_refs,
            artifact_refs=artifact_refs,
            source_refs=source_refs,
            structured_payload=structured_payload,
        )
        return self.append_memory_event(connection, event, access=access)

    def classify_required_memory_event(
        self,
        access: SeriesAccessContext,
        *,
        operation_namespace: str,
        operation_id: str,
        event_slot: Iterable[str],
        baseline_locator: str | None = None,
        baseline_category: str | None = None,
        baseline_identity: tuple[str, str] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        """Classify a SERIES event against its exact bootstrap identity."""
        self._require_access_context(access)
        owns_connection = connection is None
        conn = self._memory_ledger_read_connection() if owns_connection else connection
        try:
            self._require_registered_member(conn, access)
            self._require_memory_ledger_active(conn)
            return _classify_required_memory_event(
                conn,
                scope_type=ScopeType.SERIES.value,
                scope_id=self.context.series_id,
                operation_namespace=operation_namespace,
                operation_id=operation_id,
                event_slot=event_slot,
                baseline_locator=baseline_locator,
                baseline_category=baseline_category,
                baseline_identity=baseline_identity,
            )
        finally:
            if owns_connection:
                conn.close()

    def _memory_ledger_read_connection(self) -> sqlite3.Connection:
        database_path = self._validated_database_path(create_parent=False)
        if not database_path.exists():
            raise MemoryLedgerMigrationRequired("series ledger database is absent")
        conn = _connect_repository_database(
            storage_root=self.context.storage_root,
            database_parent=self.context.series_root,
            database_path=self.context.database_path,
            error_type=SeriesStorageError,
            create_parent=False,
            readonly=True,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            version = _read_schema_version(conn)
            if version is None or version < SERIES_DB_SCHEMA_VERSION:
                raise MemoryLedgerMigrationRequired("series ledger migration is required")
            if version > SERIES_DB_SCHEMA_VERSION:
                raise MemoryLedgerMigrationRequired("series ledger schema is newer than supported")
            validate_memory_ledger_schema(conn, scope_type=ScopeType.SERIES.value)
            return conn
        except BaseException:
            conn.close()
            raise

    def get_memory_event(self, access: SeriesAccessContext, memory_event_id: str) -> MemoryEventRecord | None:
        self._require_access_context(access)
        conn = self._memory_ledger_read_connection()
        try:
            self._require_registered_member(conn, access)
            self._require_memory_ledger_active(conn)
            return _read_memory_event(conn, memory_event_id)
        finally:
            conn.close()

    def validate_memory_event_references(
        self, access: SeriesAccessContext, event: MemoryEventRecord,
    ) -> tuple[str, ...]:
        self._require_access_context(access)
        statuses: list[str] = []
        for reference in (*event.parent_refs, *event.artifact_refs, *event.source_refs):
            if reference["owner_scope_type"] != ScopeType.SERIES.value or reference["owner_scope_id"] != self.context.series_id:
                statuses.append("NOT_AUTHORIZED")
                continue
            if reference["kind"] != "MEMORY_EVENT":
                statuses.append("LEGACY_UNVERIFIABLE")
                continue
            target = self.get_memory_event(access, reference["locator"])
            if target is None:
                statuses.append("MISSING")
            elif reference["hash"] is not None and reference["hash"] != target.content_hash:
                statuses.append("HASH_MISMATCH")
            else:
                statuses.append("VERIFIED")
        return tuple(statuses)

    def list_memory_events(
        self, access: SeriesAccessContext, *, cursor: str | None = None, limit: int = 100,
        operation: tuple[str, str] | None = None, entity: tuple[str, str] | None = None,
    ) -> tuple[tuple[MemoryEventRecord, ...], str | None]:
        self._require_access_context(access)
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise SeriesStorageError("memory ledger limit must be between 1 and 200")
        conn = self._memory_ledger_read_connection()
        try:
            conn.execute("BEGIN")
            self._require_registered_member(conn, access)
            control = self._require_memory_ledger_active(conn)
            filter_data = {"operation": list(operation) if operation else None, "entity": list(entity) if entity else None}
            if cursor is None:
                high_row = conn.execute(
                    "SELECT sequence,content_hash FROM memory_events ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                if high_row is None:
                    raise MemoryLedgerIntegrityError("active ledger has no activation event")
                after, high_watermark = 0, int(high_row["sequence"])
                high_watermark_content_hash = str(high_row["content_hash"])
            else:
                after, high_watermark = _decode_memory_ledger_cursor(
                    cursor,
                    conn=conn,
                    scope_type=ScopeType.SERIES.value,
                    scope_id=self.context.series_id,
                    activation_event_id=str(control.get("activation_event_id") or ""),
                    filter_data=filter_data,
                    error_type=SeriesStorageError,
                )
                high_row = conn.execute(
                    "SELECT content_hash FROM memory_events WHERE sequence = ?", (high_watermark,)
                ).fetchone()
                high_watermark_content_hash = str(high_row["content_hash"])
            where, params = ["sequence > ?", "sequence <= ?"], [after, high_watermark]
            if operation is not None:
                where.extend(["operation_namespace = ?", "operation_id = ?"])
                params.extend(operation)
            if entity is not None:
                where.append("EXISTS (SELECT 1 FROM memory_event_entities e WHERE e.sequence = memory_events.sequence AND e.record_type = ? AND e.entity_id = ?)")
                params.extend(entity)
            rows = conn.execute("SELECT memory_event_id FROM memory_events WHERE " + " AND ".join(where) + " ORDER BY sequence LIMIT ?", (*params, limit + 1)).fetchall()
            events = tuple(
                _read_memory_event(conn, str(row["memory_event_id"])) for row in rows[:limit]
            )
            if any(event is None for event in events):
                raise MemoryLedgerIntegrityError("memory ledger page contains a missing event")
            if len(rows) <= limit:
                return events, None
            next_cursor = _encode_memory_ledger_cursor(
                scope_type=ScopeType.SERIES.value,
                scope_id=self.context.series_id,
                activation_event_id=str(control.get("activation_event_id") or ""),
                filter_data=filter_data,
                after_sequence=events[-1].sequence,
                high_watermark=high_watermark,
                high_watermark_content_hash=high_watermark_content_hash,
            )
            return events, next_cursor
        finally:
            conn.close()

    def _bootstrap_memory_ledger_internal(
        self, *, maintenance_confirmed: bool = False,
    ) -> MemoryEventRecord:
        """Repository-internal SERIES bootstrap; it intentionally has no caller actor."""
        if maintenance_confirmed is not True:
            raise MemoryLedgerNotActive("memory ledger bootstrap requires confirmed maintenance exclusivity")
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            control = self._memory_ledger_control(conn)
            if control.get("state") == "ACTIVE":
                return _validate_active_memory_ledger(
                    conn,
                    control=control,
                    scope_type=ScopeType.SERIES.value,
                    scope_id=self.context.series_id,
                )
            if control.get("state") != "SCHEMA_READY":
                raise MemoryLedgerNotActive("series ledger is not ready for bootstrap")
            _memory_ledger_bootstrap_preflight(
                conn,
                scope_type=ScopeType.SERIES.value,
                scope_id=self.context.series_id,
            )
            operation_id = f"bootstrap-v1:SERIES:{self.context.series_id}"
            manifest: list[dict[str, str]] = []
            sequence = _ledger_next_sequence(conn)
            sources = (
                ("series_state_records", "SERIES_STATE_RECORD"), ("edges", "SERIES_EDGE"),
                ("volume_closing_snapshots", "SERIES_VOLUME_SNAPSHOT"),
                ("series_memberships", "SERIES_MEMBERSHIP"), ("series_operations", "SERIES_OPERATION"),
            )
            for table, category in sources:
                rows = _bootstrap_table_rows(conn, table=table)
                for _row, locator, raw in rows:
                    digest = _ledger_sha256_text(raw)
                    event = build_memory_event(
                        sequence=sequence, scope_type=ScopeType.SERIES.value, scope_id=self.context.series_id,
                        operation={"namespace": "LEDGER_BOOTSTRAP", "id": operation_id},
                        event_slot=("object", category, locator), event_type="LEDGER_BASELINE_OBJECT",
                        actor={"kind": "SYSTEM", "id": "MEMORY_LEDGER_BOOTSTRAP_V1", "evidence_ref": None},
                        project_id=None, book_id=None, series_id=self.context.series_id,
                        structured_payload={"category": category, "locator": locator, "bytes_hash": digest,
                                            "snapshot_text": raw, "observed_schema_version": SERIES_DB_SCHEMA_VERSION,
                                            "history_completeness": "UNKNOWN_BEFORE_BOUNDARY"},
                    )
                    _append_memory_event(conn, event)
                    manifest.append({"category": category, "locator": locator, "bytes_hash": digest})
                    sequence += 1
            metadata_rows = _validated_bootstrap_metadata_rows(
                conn, metadata_table="series_metadata", scope_type="SERIES",
            )
            for row in metadata_rows:
                raw, locator = str(row["value"]), f"metadata:{row['key']}"
                digest = _ledger_sha256_text(raw)
                event = build_memory_event(
                    sequence=sequence, scope_type=ScopeType.SERIES.value, scope_id=self.context.series_id,
                    operation={"namespace": "LEDGER_BOOTSTRAP", "id": operation_id},
                    event_slot=("object", "SERIES_METADATA", locator), event_type="LEDGER_BASELINE_OBJECT",
                    actor={"kind": "SYSTEM", "id": "MEMORY_LEDGER_BOOTSTRAP_V1", "evidence_ref": None},
                    project_id=None, book_id=None, series_id=self.context.series_id,
                    structured_payload={"category": "SERIES_METADATA", "locator": locator, "bytes_hash": digest,
                                        "snapshot_text": raw, "observed_schema_version": SERIES_DB_SCHEMA_VERSION,
                                        "history_completeness": "UNKNOWN_BEFORE_BOUNDARY"},
                )
                _append_memory_event(conn, event)
                manifest.append({"category": "SERIES_METADATA", "locator": locator, "bytes_hash": digest})
                sequence += 1
            manifest_hash = _ledger_sha256_text(_ledger_canonical_json(manifest))
            activation = build_memory_event(
                sequence=sequence, scope_type=ScopeType.SERIES.value, scope_id=self.context.series_id,
                operation={"namespace": "LEDGER_BOOTSTRAP", "id": operation_id}, event_slot=("activated",),
                event_type="LEDGER_ACTIVATED",
                actor={"kind": "SYSTEM", "id": "MEMORY_LEDGER_BOOTSTRAP_V1", "evidence_ref": None},
                project_id=None, book_id=None, series_id=self.context.series_id,
                structured_payload={"baseline_count": len(manifest), "manifest_hash": manifest_hash,
                                    "coverage": MEMORY_LEDGER_COVERAGE, "origin": "LEGACY_CUTOVER"},
            )
            _append_memory_event(conn, activation)
            _set_memory_ledger_control(conn, metadata_table="series_metadata", state="ACTIVE",
                bootstrap_operation_id=operation_id, activation_event_id=activation.memory_event_id,
                baseline_count=len(manifest), manifest_hash=manifest_hash)
            return activation

    def get_schema_version(self) -> int:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            return int(row["version"])

    def get_series_identity(self) -> dict[str, str | int]:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT series_id, schema_version
                FROM series_identity
                WHERE id = 1
                """
            ).fetchone()
            return {
                "series_id": row["series_id"],
                "schema_version": int(row["schema_version"]),
            }

    def set_metadata(self, key: str, value: str) -> None:
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO series_metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(key), str(value)),
            )

    def set_scoped_metadata(self, scope: StorageScope, key: str, value: str) -> None:
        self.require_scope(scope)
        self.set_metadata(key, value)

    def get_metadata(self, key: str) -> str | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM series_metadata WHERE key = ?",
                (str(key),),
            ).fetchone()
            return None if row is None else str(row["value"])

    def list_metadata(self) -> dict[str, str]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM series_metadata ORDER BY key").fetchall()
            return {str(row["key"]): str(row["value"]) for row in rows}

    def _require_access_context(self, access: SeriesAccessContext) -> None:
        if not isinstance(access, SeriesAccessContext):
            raise SeriesAccessError("access must be a SeriesAccessContext")
        access.require_series(self.context.series_id)

    def get_metadata_readonly(
        self, access: SeriesAccessContext, key: str,
    ) -> str | None:
        """Locate SERIES process metadata without initialization or migration."""
        self._require_access_context(access)
        with self.connect(read_only=True) as conn:
            self._require_registered_member(conn, access)
            row = conn.execute(
                "SELECT value FROM series_metadata WHERE key = ?", (str(key),)
            ).fetchone()
            return None if row is None else str(row["value"])

    @contextmanager
    def canonical_proposal_transaction(
        self,
        access: SeriesAccessContext,
        proposal_id: str,
        *,
        include_writer: bool = False,
        read_only: bool = False,
    ):
        """Serialize one SERIES proposal with its canonical and graph basis."""
        self._require_access_context(access)
        proposal_id = _normalize_identifier(proposal_id, "proposal_id", SeriesStorageError)
        if not read_only:
            self.initialize()
        with self.connect(read_only=read_only) as conn:
            if not read_only:
                conn.execute("BEGIN IMMEDIATE")
            self._require_registered_member(conn, access)
            key = "canonical_proposal.v1:" + proposal_id
            row = conn.execute(
                "SELECT value FROM series_metadata WHERE key = ?", (key,)
            ).fetchone()
            document = {} if row is None else json.loads(str(row["value"]))
            persisted = conn.execute(
                "SELECT record_type, record_id, payload_json FROM series_state_records "
                "WHERE scope_type = ? AND scope_id = ? AND state_kind = ? "
                "ORDER BY record_type, record_id",
                (ScopeType.SERIES.value, self.scope.scope_id, "SERIES_CANON"),
            ).fetchall()
            records = []
            for stored in persisted:
                wrapper = json.loads(str(stored["payload_json"]))
                records.append({
                    "record_type": str(stored["record_type"]),
                    "record_id": str(stored["record_id"]),
                    "payload_json": json.dumps(
                        wrapper["state"], sort_keys=True, separators=(",", ":"), allow_nan=False,
                    ),
                    "series_payload_json": str(stored["payload_json"]),
                })
            edges = [dict(item) for item in conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? ORDER BY edge_id",
                (self.scope.scope_type.value, self.scope.scope_id),
            )]
            snapshot = {"records": records, "edges": edges}
            if include_writer:
                yield document, snapshot, conn
            else:
                yield document, snapshot
            if document and not read_only:
                conn.execute(
                    "INSERT INTO series_metadata (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)),
                )

    @contextmanager
    def canonical_pipeline_operation(
        self, access: SeriesAccessContext, operation_id: str, *, include_writer: bool = False,
    ):
        """Serialize accepted-artifact processing entirely inside series.db."""
        self._require_access_context(access)
        operation_id = _normalize_identifier(operation_id, "operation_id", SeriesStorageError)
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_registered_member(conn, access)
            key = "canonical_pipeline.v1:" + operation_id
            row = conn.execute(
                "SELECT value FROM series_metadata WHERE key = ?", (key,)
            ).fetchone()
            state = {} if row is None else json.loads(str(row["value"]))
            yield (state, conn) if include_writer else state
            conn.execute(
                "INSERT INTO series_metadata (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)),
            )

    def apply_canonical_record_set(
        self,
        access: SeriesAccessContext,
        connection: sqlite3.Connection,
        proposal: dict,
        snapshot: dict,
        *,
        impact: dict,
        approval: dict | None,
    ) -> dict:
        """Apply SERIES canon, history and guard result in the caller transaction."""
        from app.p20_core.series_memory import SeriesStateKind, SeriesStateRecord

        self._require_access_context(access)
        access.require_project(proposal["project_id"])
        self.require_scope(StorageScope(proposal["scope_type"], proposal["scope_id"]))
        decision = DomainMutationGuard().evaluate_canonical(
            proposal=proposal, snapshot=snapshot, impact=impact, approval=approval,
        )
        if decision["outcome"] != "ALLOW":
            raise SeriesStorageError("canonical mutation denied: " + decision["reason"])
        current = {
            (record["record_type"], record["record_id"]): record
            for record in snapshot["records"]
        }
        for mutation in proposal["proposed_mutations"]:
            kind = mutation["target_entity_type"]
            identity = mutation["target_entity_id"]
            proposed_state = mutation["proposed_state"]
            existing = current.get((kind, identity))
            history_key = "canonical_versions.v1:" + kind + ":" + identity
            previous = connection.execute(
                "SELECT value FROM series_metadata WHERE key = ?", (history_key,)
            ).fetchone()
            history = [] if previous is None else json.loads(str(previous["value"]))
            if not history and existing is not None:
                history.append(json.loads(existing["payload_json"]))
            history.append(proposed_state)
            connection.execute(
                "INSERT INTO series_metadata(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (history_key, json.dumps(history, sort_keys=True, separators=(",", ":"), allow_nan=False)),
            )
            existing_wrapper = (
                None if existing is None else json.loads(existing["series_payload_json"])
            )
            operation_digest = hashlib.sha256(
                f"{proposal['proposal_hash']}|{kind}|{identity}".encode("utf-8")
            ).hexdigest()
            wrapper = SeriesStateRecord(
                series_id=self.scope.scope_id,
                state_kind=SeriesStateKind.CANON,
                source_project_id=proposal["project_id"],
                source_book_id=proposal["book_id"],
                record_type=kind,
                record_id=identity,
                source_version=proposed_state["version"],
                source_ref=proposal["source_artifact_ref"],
                provenance_refs=(
                    proposal["source_artifact_ref"],
                    proposal["extraction_candidate_set_id"],
                    proposal["verification_ref"]["id"],
                ),
                transfer_reason="CANONICAL_CHANGE",
                state=proposed_state,
                operation_id="series-canonical-" + operation_digest,
                version=proposed_state["version"],
                frozen=proposed_state["frozen"],
                author_locked=proposed_state["author_locked"],
                created_at=(
                    proposal["created_at"]
                    if existing_wrapper is None
                    else existing_wrapper["created_at"]
                ),
                updated_at=proposal["created_at"],
            )
            connection.execute(
                "INSERT INTO series_state_records (scope_type, scope_id, state_kind, "
                "record_type, record_id, source_project_id, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(scope_type, scope_id, state_kind, record_type, record_id) "
                "DO UPDATE SET source_project_id=excluded.source_project_id, "
                "payload_json=excluded.payload_json",
                (ScopeType.SERIES.value, self.scope.scope_id, SeriesStateKind.CANON.value,
                 kind, identity, proposal["project_id"], wrapper.to_json()),
            )
            entity_refs = [{"record_type": kind, "entity_id": identity,
                            "version": proposed_state["version"]}]
            commit_operation_ref = hashlib.sha256(json.dumps(
                {key: proposal[key] for key in
                 ("project_id", "scope_type", "scope_id", "proposal_id", "proposal_hash")},
                sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")).hexdigest()
            self.record_memory_event(
                connection, access=access, book_id=proposal["book_id"],
                operation_namespace="CANONICAL_PROPOSAL",
                operation_id=proposal["proposal_id"],
                event_slot=("entity", str(proposal["proposal_version"]), kind, identity),
                event_type="CANONICAL_ENTITY_CHANGED",
                actor={"kind": "SYSTEM", "id": "CANONICAL_CHANGE_V1", "evidence_ref": None},
                run_id=proposal["run_id"], step_id=proposal["step_id"],
                entity_refs=entity_refs,
                structured_payload={
                    "proposal_id": proposal["proposal_id"],
                    "proposal_version": proposal["proposal_version"],
                    "proposal_hash": proposal["proposal_hash"],
                    "entity_type": kind, "entity_id": identity,
                    "old_version": mutation["expected_current_version"],
                    "old_hash": mutation["expected_current_hash"],
                    "new_version": proposed_state["version"],
                    "new_hash": _ledger_sha256_text(_ledger_canonical_json(proposed_state)),
                    "commit_operation_ref": commit_operation_ref,
                    "outcome": mutation["operation_type"],
                },
            )
        return decision

    @contextmanager
    def domain_transaction(
        self, access: SeriesAccessContext,
    ) -> Iterator[SeriesDomainTransaction]:
        self._require_access_context(access)
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN")
            self._require_registered_member(conn, access)
            tx = SeriesDomainTransaction(conn, self.scope)
            try:
                yield tx
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _decode_edge(self, row: sqlite3.Row) -> EdgeRecord:
        return _decode_scoped_edge(self, row, SeriesStorageError)

    def get_edge(self, access: SeriesAccessContext, edge_id: str) -> EdgeRecord | None:
        self.require_registered_member(access)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? AND edge_id = ?",
                (self.scope.scope_type.value, self.scope.scope_id, edge_id),
            ).fetchone()
        return None if row is None else self._decode_edge(row)

    def list_edges(self, access: SeriesAccessContext) -> tuple[EdgeRecord, ...]:
        self.require_registered_member(access)
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? ORDER BY edge_id",
                (self.scope.scope_type.value, self.scope.scope_id),
            ).fetchall()
        return tuple(self._decode_edge(row) for row in rows)

    @contextmanager
    def _graph_read_connection(
        self, access: SeriesAccessContext,
    ) -> Iterator[sqlite3.Connection]:
        self._require_access_context(access)
        database_path = self._validated_database_path(create_parent=False)
        if not database_path.exists():
            raise sqlite3.OperationalError("unable to open database file")
        conn = _connect_repository_database(
            storage_root=self.context.storage_root,
            database_parent=self.context.series_root,
            database_path=self.context.database_path,
            error_type=SeriesStorageError,
            create_parent=False,
            readonly=True,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            if _read_schema_version(conn) != SERIES_DB_SCHEMA_VERSION:
                raise SeriesStorageError(
                    "graph analysis requires current schema; use controlled migration"
                )
            identity = conn.execute(
                "SELECT series_id FROM series_identity WHERE id = 1"
            ).fetchone()
            if identity is None or identity["series_id"] != self.context.series_id:
                raise SeriesStorageError("series.db identity does not match repository context")
            self._require_registered_member(conn, access)
            yield conn
        finally:
            conn.close()

    def traverse_dependencies(
        self,
        access: SeriesAccessContext,
        start: GraphNodeRef,
        policy: DependencyTraversalPolicy | None = None,
    ) -> tuple[DependencyTraversalStep, ...]:
        self._require_access_context(access)
        if policy is None or not getattr(policy, "read_only", False):
            self.require_registered_member(access)
        return _traverse_scoped_dependencies(
            self, start, policy, error_type=SeriesStorageError,
            read_connection=lambda: self._graph_read_connection(access),
        )

    def register_member(self, access: SeriesAccessContext, membership: Any) -> Any:
        from app.p20_core.series_memory import SeriesMembershipRecord

        self._require_access_context(access)
        if not isinstance(membership, SeriesMembershipRecord):
            raise SeriesStorageError("membership must be a SeriesMembershipRecord")
        membership.require_access(access)
        self.initialize()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT payload_json FROM series_memberships WHERE project_id = ?",
                (access.project_id,),
            ).fetchone()
            payload_json = membership.to_json()
            if existing is not None:
                if str(existing["payload_json"]) != payload_json:
                    raise SeriesStorageError("series membership identity cannot be changed")
                return membership
            conn.execute(
                "INSERT INTO series_memberships (project_id, book_id, payload_json) "
                "VALUES (?, ?, ?)",
                (access.project_id, str(membership.book_id), payload_json),
            )
        return membership

    def _require_registered_member(
        self,
        conn: sqlite3.Connection,
        access: SeriesAccessContext,
    ) -> Any:
        from app.p20_core.series_memory import SeriesMembershipRecord

        self._require_access_context(access)
        row = conn.execute(
            "SELECT payload_json FROM series_memberships WHERE project_id = ?",
            (access.project_id,),
        ).fetchone()
        if row is None:
            raise SeriesAccessError("project is not a registered member of the series")
        membership = SeriesMembershipRecord(**json.loads(str(row["payload_json"])))
        membership.require_access(access)
        return membership

    def require_registered_member(
        self,
        access: SeriesAccessContext,
        *,
        book_id: str | None = None,
        read_only: bool = False,
    ) -> Any:
        if not read_only:
            self.initialize()
        with self.connect(read_only=read_only) as conn:
            membership = self._require_registered_member(conn, access)
        if book_id is not None and str(membership.book_id) != str(book_id):
            raise SeriesAccessError("series membership book does not match requested book")
        return membership

    @staticmethod
    def _read_operation(
        conn: sqlite3.Connection,
        *,
        operation_id: str,
        semantic_hash: str,
        result_type: str,
    ) -> str | None:
        row = conn.execute(
            "SELECT semantic_hash, result_type, result_payload_json "
            "FROM series_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        if (
            str(row["semantic_hash"]) != semantic_hash
            or str(row["result_type"]) != result_type
        ):
            raise SeriesStorageError("operation identity was already used for different input")
        return str(row["result_payload_json"])

    @staticmethod
    def _record_operation(
        conn: sqlite3.Connection,
        *,
        operation_id: str,
        semantic_hash: str,
        result_type: str,
        result_id: str,
        result_payload_json: str,
    ) -> None:
        conn.execute(
            "INSERT INTO series_operations (operation_id, semantic_hash, result_type, "
            "result_id, result_payload_json) VALUES (?, ?, ?, ?, ?)",
            (operation_id, semantic_hash, result_type, result_id, result_payload_json),
        )

    def _upsert_series_state_record(
        self,
        conn: sqlite3.Connection,
        *,
        access: SeriesAccessContext,
        record: Any,
        mutation_source: MutationSource | str,
        actor_id: str | None,
        mutation_policy: MutationPolicy,
    ) -> None:
        from app.p20_core.series_memory import SeriesStateRecord

        if not isinstance(record, SeriesStateRecord):
            raise SeriesStorageError("series state write requires a SeriesStateRecord")
        record.require_access(access)
        _require_matching_scope(self.scope, record.scope, SeriesStorageError)
        existing = conn.execute(
            "SELECT payload_json FROM series_state_records "
            "WHERE scope_type = ? AND scope_id = ? AND state_kind = ? "
            "AND record_type = ? AND record_id = ?",
            (
                ScopeType.SERIES.value,
                self.context.series_id,
                record.state_kind.value,
                record.record_type,
                str(record.record_id),
            ),
        ).fetchone()
        decision = DomainMutationGuard(mutation_policy).evaluate(
            current_payload=None if existing is None else str(existing["payload_json"]),
            proposed_payload=record.to_json(),
            context=MutationContext(
                project_id=access.project_id,
                record_type=record.record_type,
                record_id=str(record.record_id),
                mutation_type=MutationType.CREATE if existing is None else MutationType.UPDATE,
                source=mutation_source,
                actor_id=actor_id,
            ),
        )
        if decision.denied:
            raise SeriesStorageError(
                f"domain mutation denied: {decision.reason_code.value}"
            )
        conn.execute(
            """
            INSERT INTO series_state_records (
                scope_type, scope_id, state_kind, record_type, record_id,
                source_project_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_type, scope_id, state_kind, record_type, record_id)
            DO UPDATE SET
                source_project_id = excluded.source_project_id,
                payload_json = excluded.payload_json
            """,
            (
                ScopeType.SERIES.value,
                self.context.series_id,
                record.state_kind.value,
                record.record_type,
                str(record.record_id),
                str(record.source_project_id),
                record.to_json(),
            ),
        )

    def _save_series_state(
        self,
        access: SeriesAccessContext,
        record: Any,
        *,
        expected_kind: Any,
        mutation_source: MutationSource | str,
        actor_id: str | None,
        mutation_policy: MutationPolicy,
    ) -> Any:
        from app.p20_core.series_memory import SeriesStateRecord

        self._require_access_context(access)
        if not isinstance(record, SeriesStateRecord):
            raise SeriesStorageError("series state write requires a SeriesStateRecord")
        if record.state_kind != expected_kind:
            raise SeriesStorageError(f"record must use {expected_kind.value} state kind")
        record.require_access(access)
        self.initialize()
        with self.connect() as conn:
            self._require_registered_member(conn, access)
            replay = self._read_operation(
                conn,
                operation_id=record.operation_id,
                semantic_hash=record.semantic_identity,
                result_type=record.state_kind.value,
            )
            if replay is not None:
                return SeriesStateRecord(**json.loads(replay))
            self._upsert_series_state_record(
                conn,
                access=access,
                record=record,
                mutation_source=mutation_source,
                actor_id=actor_id,
                mutation_policy=mutation_policy,
            )
            self._record_operation(
                conn,
                operation_id=record.operation_id,
                semantic_hash=record.semantic_identity,
                result_type=record.state_kind.value,
                result_id=str(record.record_id),
                result_payload_json=record.to_json(),
            )
        return record

    def save_series_canon(
        self,
        access: SeriesAccessContext,
        record: Any,
        *,
        mutation_source: MutationSource | str = MutationSource.AUTHOR,
        actor_id: str | None = None,
        mutation_policy: MutationPolicy = DEFAULT_MUTATION_POLICY,
    ) -> Any:
        from app.p20_core.series_memory import SeriesStateKind

        return self._save_series_state(
            access,
            record,
            expected_kind=SeriesStateKind.CANON,
            mutation_source=mutation_source,
            actor_id=actor_id,
            mutation_policy=mutation_policy,
        )

    def save_series_memory(
        self,
        access: SeriesAccessContext,
        record: Any,
        *,
        mutation_source: MutationSource | str = MutationSource.AUTOMATION,
        actor_id: str | None = None,
        mutation_policy: MutationPolicy = DEFAULT_MUTATION_POLICY,
    ) -> Any:
        from app.p20_core.series_memory import SeriesStateKind

        return self._save_series_state(
            access,
            record,
            expected_kind=SeriesStateKind.MEMORY,
            mutation_source=mutation_source,
            actor_id=actor_id,
            mutation_policy=mutation_policy,
        )

    def _list_series_state(
        self,
        access: SeriesAccessContext,
        state_kind: Any,
    ) -> tuple[Any, ...]:
        from app.p20_core.series_memory import SeriesStateRecord

        self.initialize()
        with self.connect() as conn:
            self._require_registered_member(conn, access)
            rows = conn.execute(
                "SELECT payload_json FROM series_state_records "
                "WHERE scope_type = ? AND scope_id = ? AND state_kind = ? "
                "ORDER BY record_type, record_id",
                (ScopeType.SERIES.value, self.context.series_id, state_kind.value),
            ).fetchall()
        return tuple(
            SeriesStateRecord(**json.loads(str(row["payload_json"])))
            for row in rows
        )

    def list_series_canon(self, access: SeriesAccessContext) -> tuple[Any, ...]:
        from app.p20_core.series_memory import SeriesStateKind

        return self._list_series_state(access, SeriesStateKind.CANON)

    def list_series_memory(self, access: SeriesAccessContext) -> tuple[Any, ...]:
        from app.p20_core.series_memory import SeriesStateKind

        return self._list_series_state(access, SeriesStateKind.MEMORY)

    def close_volume(
        self,
        access: SeriesAccessContext,
        snapshot: Any,
        *,
        mutation_source: MutationSource | str = MutationSource.AUTOMATION,
        actor_id: str | None = None,
        mutation_policy: MutationPolicy = DEFAULT_MUTATION_POLICY,
    ) -> Any:
        from app.p20_core.series_memory import (
            VolumeClosingSnapshot,
            VolumeTransferTarget,
            series_state_from_snapshot_item,
        )

        self._require_access_context(access)
        if not isinstance(snapshot, VolumeClosingSnapshot):
            raise SeriesStorageError("close_volume requires a VolumeClosingSnapshot")
        snapshot.require_access(access)
        _require_matching_scope(self.scope, snapshot.scope, SeriesStorageError)
        self.initialize()
        with self.connect() as conn:
            membership = self._require_registered_member(conn, access)
            if str(membership.book_id) != str(snapshot.book_id):
                raise SeriesAccessError("snapshot book is not bound to the project membership")
            replay = self._read_operation(
                conn,
                operation_id=snapshot.operation_id,
                semantic_hash=snapshot.semantic_identity,
                result_type="VOLUME_CLOSING_SNAPSHOT",
            )
            if replay is not None:
                replayed = VolumeClosingSnapshot(**json.loads(replay))
                classification = self.classify_required_memory_event(
                    access,
                    operation_namespace="SERIES_VOLUME_OPERATION",
                    operation_id=replayed.operation_id,
                    event_slot=("closed",),
                    baseline_category="SERIES_OPERATION",
                    baseline_identity=("operation_id", replayed.operation_id),
                    connection=conn,
                )
                if classification == "MEMORY_LEDGER_NEEDS_INTERVENTION":
                    raise SeriesStorageError(
                        "MEMORY_LEDGER_NEEDS_INTERVENTION: required SERIES_VOLUME_CLOSED event is missing"
                    )
                return replayed

            duplicate = conn.execute(
                "SELECT payload_json FROM volume_closing_snapshots "
                "WHERE scope_type = ? AND scope_id = ? AND project_id = ? "
                "AND book_id = ? AND semantic_hash = ?",
                (
                    ScopeType.SERIES.value,
                    self.context.series_id,
                    access.project_id,
                    str(snapshot.book_id),
                    snapshot.semantic_identity,
                ),
            ).fetchone()
            if duplicate is not None:
                existing = VolumeClosingSnapshot(**json.loads(str(duplicate["payload_json"])))
                self._record_operation(
                    conn,
                    operation_id=snapshot.operation_id,
                    semantic_hash=snapshot.semantic_identity,
                    result_type="VOLUME_CLOSING_SNAPSHOT",
                    result_id=existing.snapshot_id,
                    result_payload_json=existing.to_json(),
                )
                return existing

            conflicting = conn.execute(
                "SELECT semantic_hash FROM volume_closing_snapshots WHERE snapshot_id = ?",
                (snapshot.snapshot_id,),
            ).fetchone()
            if conflicting is not None:
                raise SeriesStorageError("snapshot_id was already used for different input")

            conn.execute(
                "INSERT INTO volume_closing_snapshots (snapshot_id, scope_type, scope_id, "
                "project_id, book_id, source_state_version, semantic_hash, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot.snapshot_id,
                    ScopeType.SERIES.value,
                    self.context.series_id,
                    access.project_id,
                    str(snapshot.book_id),
                    snapshot.source_state_version,
                    snapshot.semantic_identity,
                    snapshot.to_json(),
                ),
            )
            for item in snapshot.items:
                if item.transfer_target == VolumeTransferTarget.SNAPSHOT_ONLY:
                    continue
                self._upsert_series_state_record(
                    conn,
                    access=access,
                    record=series_state_from_snapshot_item(snapshot, item),
                    mutation_source=mutation_source,
                    actor_id=actor_id,
                    mutation_policy=mutation_policy,
                )
            self._record_operation(
                conn,
                operation_id=snapshot.operation_id,
                semantic_hash=snapshot.semantic_identity,
                result_type="VOLUME_CLOSING_SNAPSHOT",
                result_id=snapshot.snapshot_id,
                result_payload_json=snapshot.to_json(),
            )
            transferred = [
                {
                    "record_type": item.record_type,
                    "entity_id": str(item.record_id),
                    "version": item.source_version,
                    "target": item.transfer_target.value,
                }
                for item in snapshot.items
                if item.transfer_target != VolumeTransferTarget.SNAPSHOT_ONLY
            ]
            self.record_memory_event(
                conn, access=access, book_id=str(snapshot.book_id),
                operation_namespace="SERIES_VOLUME_OPERATION",
                operation_id=snapshot.operation_id,
                event_slot=("closed",), event_type="SERIES_VOLUME_CLOSED",
                actor={"kind": "SYSTEM", "id": "SERIES_VOLUME_CLOSE_V1",
                       "evidence_ref": None},
                entity_refs=[
                    {"record_type": item["record_type"], "entity_id": item["entity_id"],
                     "version": item["version"]}
                    for item in transferred
                ],
                structured_payload={
                    "snapshot_id": snapshot.snapshot_id,
                    "semantic_identity": snapshot.semantic_identity,
                    "receipt_ref": "series_operations:" + snapshot.operation_id,
                    "transferred_entity_refs": transferred,
                    "outcome": "CLOSED",
                },
            )
        return snapshot

    def get_latest_volume_snapshot(
        self,
        access: SeriesAccessContext,
        *,
        book_id: str | None = None,
    ) -> Any | None:
        from app.p20_core.series_memory import VolumeClosingSnapshot

        self.initialize()
        with self.connect() as conn:
            self._require_registered_member(conn, access)
            params: list[Any] = [ScopeType.SERIES.value, self.context.series_id]
            where = "scope_type = ? AND scope_id = ?"
            if book_id is not None:
                where += " AND book_id = ?"
                params.append(str(book_id))
            row = conn.execute(
                f"SELECT payload_json FROM volume_closing_snapshots WHERE {where} "
                "ORDER BY source_state_version DESC, snapshot_id DESC LIMIT 1",
                tuple(params),
            ).fetchone()
        if row is None:
            return None
        return VolumeClosingSnapshot(**json.loads(str(row["payload_json"])))

    def list_volume_snapshots(self, access: SeriesAccessContext) -> tuple[Any, ...]:
        from app.p20_core.series_memory import VolumeClosingSnapshot

        self.initialize()
        with self.connect() as conn:
            self._require_registered_member(conn, access)
            rows = conn.execute(
                "SELECT payload_json FROM volume_closing_snapshots "
                "WHERE scope_type = ? AND scope_id = ? "
                "ORDER BY source_state_version, snapshot_id",
                (ScopeType.SERIES.value, self.context.series_id),
            ).fetchall()
        return tuple(
            VolumeClosingSnapshot(**json.loads(str(row["payload_json"])))
            for row in rows
        )

    def get_opening_state(self, access: SeriesAccessContext) -> Any:
        from app.p20_core.series_memory import SeriesOpeningState

        return SeriesOpeningState(
            series_id=self.context.series_id,
            project_id=access.project_id,
            series_canon=self.list_series_canon(access),
            series_memory=self.list_series_memory(access),
            latest_snapshot=self.get_latest_volume_snapshot(access),
        )

    def get_pragma(self, name: str) -> str | int:
        with self.connect() as conn:
            row = conn.execute(f"PRAGMA {name}").fetchone()
            return row[0]

    def inspect_schema(self) -> SchemaStatus:
        return _inspect_database_schema(
            storage_root=self.context.storage_root,
            database_parent=self.context.series_root,
            database_path=self.context.database_path,
            error_type=SeriesStorageError,
            required_version=SERIES_DB_SCHEMA_VERSION,
        )

    def migrate_schema(
        self,
        migrations: Iterable[SchemaMigration] = SERIES_DB_MIGRATIONS,
        *,
        target_version: int | None = None,
        backup_path: Path | None = None,
        maintenance_confirmed: bool = False,
        release_head: str | None = None,
        backup_deadline_seconds: float = _DOMAIN_DB_BUSY_TIMEOUT_MS / 1000,
    ) -> SchemaStatus:
        database_path = self._validated_database_path(create_parent=True)
        conn = _connect_repository_database(
            storage_root=self.context.storage_root,
            database_parent=self.context.series_root,
            database_path=self.context.database_path,
            error_type=SeriesStorageError,
            create_parent=True,
            timeout=_DOMAIN_DB_BUSY_TIMEOUT_MS / 1000,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {_DOMAIN_DB_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys = ON")
            runner = SchemaMigrationRunner(
                target_version=target_version or SERIES_DB_SCHEMA_VERSION,
                migrations=migrations,
            )
            status = _migration_inspect(runner, conn)
            identity = conn.execute(
                "SELECT series_id FROM series_identity WHERE id=1"
            ).fetchone()
            if identity is None or str(identity["series_id"]) != self.context.series_id:
                raise SchemaMigrationError("series migration identity does not match repository context")
            before_migrate = None
            if (
                status.current_version is not None
                and status.current_version < 4 <= runner.target_version
            ):
                if status.current_version != 3:
                    raise SchemaMigrationError("memory ledger migration requires series schema S3 entry")
                if maintenance_confirmed is not True:
                    raise SchemaMigrationError("memory ledger migration requires confirmed maintenance exclusivity")
                if not isinstance(release_head, str) or not release_head.strip():
                    raise SchemaMigrationError("memory ledger migration requires release/HEAD")
                selected_backup = backup_path or database_path.with_name(database_path.name + ".memory-ledger-v1.backup")
                expected_identity = {
                    "series_id": self.context.series_id,
                    "schema_version": 3,
                }

                def before_migrate(migration_conn: sqlite3.Connection) -> None:
                    _preflight_memory_ledger_migration(
                        migration_conn,
                        scope_type="SERIES",
                        expected_version=3,
                        expected_identity=expected_identity,
                    )
                    _create_verified_sqlite_backup(
                        database_path,
                        selected_backup,
                        expected_version=3,
                        scope_type="SERIES",
                        scope_id=self.context.series_id,
                        expected_identity=expected_identity,
                        release_head=release_head,
                        deadline_seconds=backup_deadline_seconds,
                    )
            result = runner.migrate(conn, before_migrate=before_migrate)
            identity = conn.execute(
                "SELECT series_id FROM series_identity WHERE id=1"
            ).fetchone()
            if identity is None or str(identity["series_id"]) != self.context.series_id:
                raise SchemaMigrationError("series migration identity does not match repository context")
            return result
        except sqlite3.OperationalError as exc:
            if _is_sqlite_busy_or_locked(exc):
                raise SchemaMigrationError("MAINTENANCE_BUSY") from exc
            raise
        finally:
            conn.close()


class SystemRepository:
    def __init__(self, context: SystemStorageContext) -> None:
        self.context = context

    @property
    def db_path(self) -> Path:
        return self.context.database_path

    def _validated_database_path(self, *, create_parent: bool) -> Path:
        return _validated_repository_database_path(
            storage_root=self.context.storage_root,
            database_parent=self.context.storage_root,
            database_path=self.context.database_path,
            error_type=SystemStorageError,
            create_parent=create_parent,
        )

    @contextmanager
    def connect(self, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        if read_only:
            conn = _connect_repository_database(
                storage_root=self.context.storage_root,
                database_parent=self.context.storage_root,
                database_path=self.context.database_path,
                error_type=SystemStorageError, create_parent=False, readonly=True,
            )
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only = ON")
                conn.execute("BEGIN")
                if _read_schema_version(conn) != SYSTEM_DB_SCHEMA_VERSION:
                    raise SystemStorageError("system schema version mismatch")
                identity = conn.execute("SELECT id, schema_version FROM system_identity").fetchall()
                if len(identity) != 1 or tuple(identity[0]) != (1, SYSTEM_DB_SCHEMA_VERSION):
                    raise SystemStorageError("system identity mismatch")
                yield conn
            finally:
                conn.close()
            return
        conn = _connect_repository_database(
            storage_root=self.context.storage_root,
            database_parent=self.context.storage_root,
            database_path=self.context.database_path,
            error_type=SystemStorageError,
            create_parent=True,
            timeout=_SYSTEM_DB_BUSY_TIMEOUT_MS / 1000,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {_SYSTEM_DB_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys = ON")
            # Concurrent first opens can race when changing journal mode, which
            # may return SQLITE_BUSY immediately despite busy_timeout. No domain
            # transaction has begun: retry only this idempotent initialization.
            import time
            deadline = time.monotonic() + _SYSTEM_DB_BUSY_TIMEOUT_MS / 1000
            while True:
                try:
                    conn.execute("PRAGMA journal_mode = WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_BUSY or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            _initialize_schema_version(
                conn,
                required_version=SYSTEM_DB_SCHEMA_VERSION,
                database_name="agentpro_system.db",
                error_type=SystemStorageError,
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_identity (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    schema_version INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO system_identity (id, schema_version)
                VALUES (1, ?)
                """,
                (SYSTEM_DB_SCHEMA_VERSION,),
            )

    def get_schema_version(self) -> int:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            return int(row["version"])

    @contextmanager
    def local_operator_transaction(self, *, read_only: bool = False):
        """Hold credential/registry stable through an operator decision.

        A decision only reads system state and writes its project DB. This is
        not a cross-store write transaction or recovery protocol.
        """
        if not read_only:
            self.initialize()
        with self.connect(read_only=read_only) as conn:
            if not read_only:
                conn.execute("BEGIN IMMEDIATE")
            key = "local_operator.v1"
            row = conn.execute("SELECT value FROM system_metadata WHERE key = ?", (key,)).fetchone()
            state = {} if row is None else json.loads(row["value"])
            original = json.dumps(state, sort_keys=True)
            registry = self._read_project_registry(conn)
            yield state, registry
            if json.dumps(state, sort_keys=True) != original:
                if read_only:
                    raise SystemStorageError("read-only operator state cannot be changed")
                conn.execute(
                    "INSERT INTO system_metadata (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(state, sort_keys=True, separators=(",", ":"))),
                )

    def set_metadata(self, key: str, value: str) -> None:
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO system_metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(key), str(value)),
            )

    def get_metadata(self, key: str) -> str | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM system_metadata WHERE key = ?",
                (str(key),),
            ).fetchone()
            return None if row is None else str(row["value"])

    def list_metadata(self) -> dict[str, str]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM system_metadata ORDER BY key").fetchall()
            return {str(row["key"]): str(row["value"]) for row in rows}

    @staticmethod
    def _decode_project_registry(raw: str | None) -> dict[str, str]:
        if raw is None:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise SystemStorageError("project registry metadata is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise SystemStorageError("project registry metadata must be an object")

        registry: dict[str, str] = {}
        for project_id, book_id in payload.items():
            normalized_project = _normalize_identifier(
                project_id,
                "project_id",
                SystemStorageError,
            )
            normalized_book = _normalize_identifier(
                book_id,
                "book_id",
                SystemStorageError,
            )
            registry[normalized_project] = normalized_book
        if len(set(registry.values())) != len(registry):
            raise SystemStorageError("project registry contains ambiguous book bindings")
        return registry

    @staticmethod
    def _read_project_registry(conn: sqlite3.Connection) -> dict[str, str]:
        row = conn.execute(
            "SELECT value FROM system_metadata WHERE key = ?",
            (PROJECT_REGISTRY_METADATA_KEY,),
        ).fetchone()
        raw = None if row is None else str(row["value"])
        return SystemRepository._decode_project_registry(raw)

    @staticmethod
    def _write_project_registry(
        conn: sqlite3.Connection,
        registry: dict[str, str],
    ) -> None:
        payload = json.dumps(registry, sort_keys=True, separators=(",", ":"))
        conn.execute(
            """
            INSERT INTO system_metadata (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (PROJECT_REGISTRY_METADATA_KEY, payload),
        )

    def list_project_bindings(self) -> dict[str, str]:
        self.initialize()
        with self.connect() as conn:
            return self._read_project_registry(conn)

    def resolve_project_id_for_book(self, book_id: str) -> str | None:
        normalized_book = _normalize_identifier(book_id, "book_id", SystemStorageError)
        matches = [
            project_id
            for project_id, bound_book_id in self.list_project_bindings().items()
            if bound_book_id == normalized_book
        ]
        if len(matches) > 1:
            raise SystemStorageError("project registry contains ambiguous book bindings")
        return matches[0] if matches else None

    def resolve_book_id_for_project(self, project_id: str) -> str | None:
        normalized_project = _normalize_identifier(
            project_id,
            "project_id",
            SystemStorageError,
        )
        return self.list_project_bindings().get(normalized_project)

    def bind_project(self, project_id: str, book_id: str) -> None:
        normalized_project = _normalize_identifier(
            project_id,
            "project_id",
            SystemStorageError,
        )
        normalized_book = _normalize_identifier(book_id, "book_id", SystemStorageError)
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            registry = self._read_project_registry(conn)
            existing_book = registry.get(normalized_project)
            if existing_book is not None and existing_book != normalized_book:
                raise SystemStorageError("project_id is already bound to a different book")
            existing_project = next(
                (
                    bound_project
                    for bound_project, bound_book in registry.items()
                    if bound_book == normalized_book
                ),
                None,
            )
            if existing_project is not None and existing_project != normalized_project:
                raise SystemStorageError("book_id is already bound to a different project")
            registry[normalized_project] = normalized_book
            self._write_project_registry(conn, registry)

    def resolve_or_bind_project(self, project_id: str, book_id: str) -> str:
        normalized_project = _normalize_identifier(
            project_id,
            "project_id",
            SystemStorageError,
        )
        normalized_book = _normalize_identifier(book_id, "book_id", SystemStorageError)
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            registry = self._read_project_registry(conn)
            existing_project = next(
                (
                    bound_project
                    for bound_project, bound_book in registry.items()
                    if bound_book == normalized_book
                ),
                None,
            )
            if existing_project is not None:
                return existing_project
            if normalized_project in registry:
                raise SystemStorageError("project_id is already bound to a different book")
            registry[normalized_project] = normalized_book
            self._write_project_registry(conn, registry)
            return normalized_project

    def discover_project_bindings(self, book_id: str) -> tuple[str, ...]:
        normalized_book = _normalize_identifier(book_id, "book_id", SystemStorageError)
        projects_root = self.context.storage_root / "projects"
        _assert_relative_to(projects_root, self.context.storage_root, "projects_root", SystemStorageError)
        if not projects_root.exists():
            return ()

        matches: list[str] = []
        for database_path in sorted(projects_root.glob(f"*/{PROJECT_DB_FILENAME}")):
            try:
                validated_path = _validated_repository_database_path(
                    storage_root=self.context.storage_root,
                    database_parent=database_path.parent,
                    database_path=database_path,
                    error_type=SystemStorageError,
                    create_parent=False,
                )
                conn = _connect_repository_database(
                    storage_root=self.context.storage_root,
                    database_parent=database_path.parent,
                    database_path=validated_path,
                    error_type=SystemStorageError,
                    create_parent=False,
                    readonly=True,
                )
                conn.row_factory = sqlite3.Row
                try:
                    if not _table_exists(conn, "project_identity"):
                        continue
                    row = conn.execute(
                        "SELECT project_id, book_id FROM project_identity WHERE id = 1"
                    ).fetchone()
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                raise SystemStorageError(
                    f"cannot inspect project identity: {database_path}"
                ) from exc
            if row is not None and str(row["book_id"]) == normalized_book:
                matches.append(str(row["project_id"]))
        return tuple(sorted(set(matches)))

    def discover_series_memberships(
        self,
        project_id: str,
    ) -> tuple[tuple[str, str], ...]:
        normalized_project = _normalize_identifier(
            project_id,
            "project_id",
            SystemStorageError,
        )
        series_root = self.context.storage_root / "series"
        _assert_relative_to(series_root, self.context.storage_root, "series_root", SystemStorageError)
        if not series_root.exists():
            return ()

        memberships: list[tuple[str, str]] = []
        for database_path in sorted(series_root.glob(f"*/{SERIES_DB_FILENAME}")):
            try:
                validated_path = _validated_repository_database_path(
                    storage_root=self.context.storage_root,
                    database_parent=database_path.parent,
                    database_path=database_path,
                    error_type=SystemStorageError,
                    create_parent=False,
                )
                conn = _connect_repository_database(
                    storage_root=self.context.storage_root,
                    database_parent=database_path.parent,
                    database_path=validated_path,
                    error_type=SystemStorageError,
                    create_parent=False,
                    readonly=True,
                )
                conn.row_factory = sqlite3.Row
                try:
                    if not _table_exists(conn, "series_identity") or not _table_exists(
                        conn, "series_memberships"
                    ):
                        continue
                    identity = conn.execute(
                        "SELECT series_id FROM series_identity WHERE id = 1"
                    ).fetchone()
                    membership = conn.execute(
                        "SELECT book_id FROM series_memberships WHERE project_id = ?",
                        (normalized_project,),
                    ).fetchone()
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                raise SystemStorageError(
                    f"cannot inspect series membership: {database_path}"
                ) from exc
            if identity is not None and membership is not None:
                memberships.append(
                    (str(identity["series_id"]), str(membership["book_id"]))
                )
        return tuple(sorted(set(memberships)))

    def get_pragma(self, name: str) -> str | int:
        with self.connect() as conn:
            row = conn.execute(f"PRAGMA {name}").fetchone()
            return row[0]

    def inspect_schema(self) -> SchemaStatus:
        return _inspect_database_schema(
            storage_root=self.context.storage_root,
            database_parent=self.context.storage_root,
            database_path=self.context.database_path,
            error_type=SystemStorageError,
            required_version=SYSTEM_DB_SCHEMA_VERSION,
        )

    def migrate_schema(
        self,
        migrations: Iterable[SchemaMigration],
        *,
        target_version: int | None = None,
    ) -> SchemaStatus:
        conn = _connect_repository_database(
            storage_root=self.context.storage_root,
            database_parent=self.context.storage_root,
            database_path=self.context.database_path,
            error_type=SystemStorageError,
            create_parent=True,
            timeout=_SYSTEM_DB_BUSY_TIMEOUT_MS / 1000,
        )
        conn.row_factory = sqlite3.Row
        try:
            return SchemaMigrationRunner(
                target_version=target_version or SYSTEM_DB_SCHEMA_VERSION,
                migrations=migrations,
            ).migrate(conn)
        finally:
            conn.close()


def resolve_book_project_context(
    book_id: str,
    *,
    project_id: str | None = None,
) -> ProjectStorageContext:
    return StorageResolver().resolve_book(book_id, project_id=project_id)


def ensure_project_repository_for_book(
    book_id: str,
    *,
    project_id: str | None = None,
) -> ProjectRepository:
    context = resolve_book_project_context(book_id, project_id=project_id)
    repository = ProjectRepository(context)
    repository.initialize()
    return repository


def resolve_series_context(series_id: str) -> SeriesStorageContext:
    return StorageResolver().resolve_series(series_id)


def ensure_series_repository(series_id: str | None) -> SeriesRepository | None:
    context = StorageResolver().resolve_optional_series(series_id)
    if context is None:
        return None
    repository = SeriesRepository(context)
    repository.initialize()
    return repository


def resolve_project_series_access_context(
    project_id: str,
    series_id: str,
) -> SeriesAccessContext:
    return StorageResolver().resolve_series_access(project_id, series_id)


def resolve_optional_project_series_access_context(
    project_id: str,
    series_id: str | None,
) -> SeriesAccessContext | None:
    return StorageResolver().resolve_optional_series_access(project_id, series_id)


def ensure_series_repository_for_access(
    access_context: SeriesAccessContext | None,
    *,
    requested_series_id: str | None = None,
) -> SeriesRepository | None:
    if access_context is None:
        return None
    if not isinstance(access_context, SeriesAccessContext):
        raise SeriesAccessError("access_context must be a SeriesAccessContext")
    if requested_series_id is not None:
        access_context.require_series(requested_series_id)
    return ensure_series_repository(access_context.series_id)


def resolve_system_context() -> SystemStorageContext:
    return StorageResolver().resolve_system()


def ensure_system_repository() -> SystemRepository:
    repository = SystemRepository(resolve_system_context())
    repository.initialize()
    return repository


__all__ = [
    "PROJECT_DB_FILENAME",
    "PROJECT_DB_MIGRATIONS",
    "PROJECT_DB_SCHEMA_VERSION",
    "SERIES_DB_FILENAME",
    "SERIES_DB_MIGRATIONS",
    "SERIES_DB_SCHEMA_VERSION",
    "SYSTEM_DB_FILENAME",
    "SYSTEM_DB_SCHEMA_VERSION",
    "ProjectRepository",
    "ProjectStorageContext",
    "ProjectStorageError",
    "ProjectDomainTransaction",
    "SchemaMigration",
    "SchemaMigrationError",
    "SchemaMigrationRunner",
    "SchemaStatus",
    "ScopeType",
    "ScopeValidationError",
    "SeriesRepository",
    "SeriesAccessContext",
    "SeriesAccessError",
    "SeriesStorageContext",
    "SeriesStorageError",
    "StorageResolver",
    "StorageScope",
    "SystemRepository",
    "SystemStorageContext",
    "SystemStorageError",
    "ensure_project_repository_for_book",
    "ensure_series_repository",
    "ensure_series_repository_for_access",
    "ensure_system_repository",
    "resolve_book_project_context",
    "resolve_optional_project_series_access_context",
    "resolve_project_series_access_context",
    "resolve_series_context",
    "resolve_system_context",
]

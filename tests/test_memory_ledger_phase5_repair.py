from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy

import pytest

import app.p20_core.project_repository as repository_module
from app.p20_core.memory_ledger import (
    _schema_check_signatures,
    _schema_sql_tokens,
    MemoryEventRecord,
    MemoryLedgerError,
    MemoryLedgerIntegrityError,
    append_memory_event,
    build_memory_event,
    canonical_json,
    create_memory_ledger_schema,
    next_sequence,
    read_memory_event,
    sha256_text,
    validate_memory_ledger_schema,
)
from app.p20_core.project_repository import (
    PROJECT_DB_SCHEMA_VERSION,
    MemoryLedgerNotActive,
    ProjectRepository,
    SchemaMigration,
    SchemaMigrationError,
    SchemaMigrationRunner,
    SeriesRepository,
    StorageResolver,
)


H = "a" * 64


def _schema_repository(storage_root, scope: str, suffix: str):
    resolver = StorageResolver(storage_root)
    if scope == "PROJECT":
        return ProjectRepository(resolver.resolve_project(
            "PROJ-ledger-b3-" + suffix, book_id="BOOK-ledger-b3-" + suffix,
        ))
    return SeriesRepository(resolver.resolve_series("SERIES-ledger-b3-" + suffix))


def _rewrite_table_schema_sql(repository, table: str, transform) -> None:
    with sqlite3.connect(repository.db_path) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        rewritten = transform(sql)
        assert rewritten != sql
        schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
        connection.execute("PRAGMA writable_schema=ON")
        try:
            connection.execute(
                "UPDATE sqlite_master SET sql=? WHERE type='table' AND name=?",
                (rewritten, table),
            )
        finally:
            connection.execute("PRAGMA writable_schema=OFF")
        connection.execute(f"PRAGMA schema_version={schema_version + 1}")


def _assert_public_schema_validation_rejects(repository, *, match: str) -> None:
    failures = (
        ValueError, MemoryLedgerIntegrityError, MemoryLedgerNotActive,
        sqlite3.Error,
    )
    with pytest.raises(failures, match=match):
        repository.migrate_schema()
    with pytest.raises(failures, match=match):
        repository.initialize()


def _case(event_type: str, namespace: str, slot: tuple[str, ...], payload: dict) -> dict:
    return {"event_type": event_type, "namespace": namespace, "slot": slot, "payload": payload}


CASES = (
    _case("LEDGER_BASELINE_OBJECT", "LEDGER_BOOTSTRAP", ("object", "PROJECT_FACT", "metadata:test"),
          {"category": "PROJECT_FACT", "locator": "metadata:test", "bytes_hash": H,
           "snapshot_text": "{}", "observed_schema_version": 5,
           "history_completeness": "UNKNOWN_BEFORE_BOUNDARY"}),
    _case("LEDGER_ACTIVATED", "LEDGER_BOOTSTRAP", ("activated",),
          {"baseline_count": 0, "manifest_hash": H, "coverage": "MEMORY_PIPELINES_V1",
           "origin": "EMPTY_STORE"}),
    _case("EXTRACTION_STARTED", "CANONICAL_PIPELINE", ("start",),
          {"phase": "STARTED", "attempt_ordinal": 0, "source_refs": ["source:1"],
           "outcome": "STARTED"}),
    _case("EXTRACTION_ATTEMPT_RECORDED", "CANONICAL_PIPELINE", ("attempt", "1"),
          {"phase": "VERIFIED_ATTEMPT", "attempt_ordinal": 1, "source_refs": ["source:1"],
           "candidate_refs": ["candidate:1"], "verification_refs": ["verification:1"],
           "invocation_refs": ["call:1"], "outcome": "ACCEPT"}),
    _case("EXTRACTION_FINISHED", "CANONICAL_PIPELINE", ("terminal",),
          {"phase": "TERMINAL", "attempt_ordinal": 1, "candidate_refs": ["candidate:1"],
           "verification_refs": ["verification:1"], "outcome": "ACCEPT"}),
    _case("PROPOSAL_RECORDED", "CANONICAL_PROPOSAL", ("proposal", "1"),
          {"phase": "PROPOSAL_FROZEN", "proposal_id": "operation-1", "proposal_version": 1,
           "proposal_hash": H, "new_state": "READY_FOR_ANALYSIS", "basis_hash": H,
           "outcome": "RECORDED"}),
    _case("PROPOSAL_STATE_RECORDED", "CANONICAL_PROPOSAL", ("state", "1", "initial-analysis"),
          {"phase": "INITIAL_ANALYSIS", "proposal_id": "operation-1", "proposal_version": 1,
           "proposal_hash": H, "previous_state": "READY_FOR_ANALYSIS",
           "new_state": "AWAITING_USER_APPROVAL", "basis_hash": H,
           "impact_refs": ["impact:1"], "guard_refs": ["guard:1"],
           "outcome": "REQUIRE_USER_APPROVAL", "reason_code": "PROTECTED_MUTATION"}),
    _case("AUTHOR_DECISION_RECORDED", "CANONICAL_PROPOSAL", ("decision", "1"),
          {"approval_id": "approval-1", "authorization_ref": "authorization-1",
           "proposal_id": "operation-1", "proposal_version": 1, "proposal_hash": H,
           "decision": "APPROVE", "outcome": "RECORDED"}),
    _case("CANONICAL_ENTITY_CHANGED", "CANONICAL_PROPOSAL", ("entity", "1", "FACT", "FACT-test"),
          {"proposal_id": "operation-1", "proposal_version": 1, "proposal_hash": H,
           "entity_type": "FACT", "entity_id": "FACT-test", "old_version": None,
           "old_hash": None, "new_version": 1, "new_hash": H,
           "commit_operation_ref": H, "outcome": "CREATE"}),
    _case("CANONICAL_COMMITTED", "CANONICAL_PROPOSAL", ("commit", "1"),
          {"proposal_id": "operation-1", "proposal_version": 1, "proposal_hash": H,
           "receipt_ref": "canonical_commit.v1:operation-1", "receipt_hash": H,
           "entity_event_ids": ["MEV-" + H], "outcome": "COMMITTED"}),
    _case("RESEARCH_COMMAND_RECORDED", "RESEARCH_COMMAND", ("command",),
          {"command": "QUESTION", "action": "CREATE", "result_refs": ["RESEARCH-test:v1"],
           "outcome": "COMPLETED"}),
    _case("RESEARCH_RESULT_RECORDED", "RESEARCH_OPERATION", ("result", "1"),
          {"action": "EXTRACT", "claim_refs": ["CLAIM-test:v1"],
           "result_refs": ["research.v1/operations/operation-1"], "outcome": "COMPLETED"}),
    _case("ARTIFACT_WRITE_INTENDED", "CROSS_STORE_OPERATION", ("intent",),
          {"operation_id": "operation-1", "input_hash": H, "artifact_type": "CHAPTER_ARTIFACT_LINEAGE_V2",
           "confined_path": "artifacts/test/chapter.json", "expected_hash": H,
           "status": "INTENDED", "outcome": "PENDING"}),
    _case("ARTIFACT_WRITE_CONFIRMED", "CROSS_STORE_OPERATION", ("confirmed",),
          {"operation_id": "operation-1", "input_hash": H, "artifact_type": "CHAPTER_ARTIFACT_LINEAGE_V2",
           "confined_path": "artifacts/test/chapter.json", "expected_hash": H,
           "observed_hash": H, "status": "CONFIRMED", "outcome": "COMMITTED"}),
    _case("CHAPTER_VERSION_RECORDED", "CROSS_STORE_OPERATION", ("chapter-version", "CHAPTER-test", "1"),
          {"operation_id": "operation-1", "input_hash": H, "artifact_type": "CHAPTER_ARTIFACT_LINEAGE_V2",
           "confined_path": "artifacts/test/chapter.json", "observed_hash": H,
           "chapter_id": "CHAPTER-test", "version": 1, "status": "ACCEPTED",
           "text_hash": H, "quality_evaluation_ref": None, "outcome": "RECORDED"}),
    _case("SERIES_VOLUME_CLOSED", "SERIES_VOLUME_OPERATION", ("closed",),
          {"snapshot_id": "SNAPSHOT-test", "semantic_identity": H,
           "receipt_ref": "series_operations:operation-1", "transferred_entity_refs": [],
           "outcome": "CLOSED"}),
    _case("MEMORY_EVENT_CORRECTION", "LEDGER_CORRECTION", ("correction",),
          {"target_memory_event_id": "MEV-" + H, "target_content_hash": H,
           "reason": "Neutral correction", "corrected_description": "Neutral description"}),
)


def _kwargs(case: dict) -> dict:
    series = case["event_type"] == "SERIES_VOLUME_CLOSED"
    kwargs = {
        "sequence": 1,
        "scope_type": "SERIES" if series else "PROJECT",
        "scope_id": "SERIES-ledger-codec" if series else "PROJ-ledger-codec",
        "operation": {"namespace": case["namespace"], "id": "operation-1"},
        "event_slot": case["slot"],
        "event_type": case["event_type"],
        "actor": ({
            "kind": "OPERATOR",
            "id": "operator-test",
            "evidence_ref": (
                case["payload"].get("authorization_ref") or "repair-ticket-neutral"
            ),
        }
                  if case["event_type"] in {
                      "AUTHOR_DECISION_RECORDED", "MEMORY_EVENT_CORRECTION",
                  }
                  else ({
                      "kind": "SYSTEM",
                      "id": "MEMORY_LEDGER_BOOTSTRAP_V1",
                      "evidence_ref": None,
                  } if case["namespace"] == "LEDGER_BOOTSTRAP" else {
                      "kind": "SYSTEM", "id": "PHASE5_TEST", "evidence_ref": None,
                  })),
        "project_id": "PROJ-ledger-codec",
        "book_id": "BOOK-ledger-codec",
        "series_id": "SERIES-ledger-codec" if series else None,
        "structured_payload": deepcopy(case["payload"]),
        "timestamp": "2026-09-23T12:00:00.000000Z",
    }
    if case["event_type"] == "CANONICAL_ENTITY_CHANGED":
        kwargs["entity_refs"] = [{
            "record_type": case["payload"]["entity_type"],
            "entity_id": case["payload"]["entity_id"],
            "version": case["payload"]["new_version"],
        }]
    if case["event_type"] == "MEMORY_EVENT_CORRECTION":
        kwargs["parent_refs"] = [{
            "kind": "MEMORY_EVENT",
            "owner_scope_type": "PROJECT",
            "owner_scope_id": "PROJ-ledger-codec",
            "locator": case["payload"]["target_memory_event_id"],
            "version": None,
            "hash_scheme": "SHA256",
            "hash": case["payload"]["target_content_hash"],
        }]
    return kwargs


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["event_type"])
def test_every_event_variant_is_closed_and_bound(case: dict) -> None:
    kwargs = _kwargs(case)
    event = build_memory_event(**kwargs)
    assert event.event_type == case["event_type"]
    assert MemoryEventRecord.from_dict(json.loads(event.to_json())) == event

    required = next(iter(case["payload"]))
    missing = deepcopy(kwargs)
    missing["structured_payload"] = deepcopy(case["payload"])
    missing["structured_payload"].pop(required)
    with pytest.raises(MemoryLedgerError):
        build_memory_event(**missing)

    foreign = deepcopy(kwargs)
    foreign["structured_payload"]["foreign_field"] = "forbidden"
    with pytest.raises(MemoryLedgerError, match="unknown fields"):
        build_memory_event(**foreign)

    wrong_type = deepcopy(kwargs)
    wrong_type["structured_payload"][required] = None
    with pytest.raises(MemoryLedgerError):
        build_memory_event(**wrong_type)

    wrong_namespace = deepcopy(kwargs)
    wrong_namespace["operation"]["namespace"] = (
        "RESEARCH_COMMAND" if case["namespace"] != "RESEARCH_COMMAND" else "CANONICAL_PIPELINE"
    )
    with pytest.raises(MemoryLedgerError, match="namespace"):
        build_memory_event(**wrong_namespace)

    wrong_slot = deepcopy(kwargs)
    wrong_slot["event_slot"] = ("wrong",)
    with pytest.raises(MemoryLedgerError, match="event_slot"):
        build_memory_event(**wrong_slot)

    conflicting = deepcopy(kwargs)
    if "proposal_id" in conflicting["structured_payload"]:
        conflicting["structured_payload"]["proposal_id"] = "different-operation"
    elif "operation_id" in conflicting["structured_payload"]:
        conflicting["structured_payload"]["operation_id"] = "different-operation"
    elif case["event_type"] == "LEDGER_BASELINE_OBJECT":
        conflicting["structured_payload"]["locator"] = "metadata:different"
    elif ("attempt_ordinal" in conflicting["structured_payload"]
          and case["event_type"] != "EXTRACTION_FINISHED"):
        conflicting["structured_payload"]["attempt_ordinal"] += 1
    elif conflicting["scope_type"] == "SERIES":
        conflicting["series_id"] = "SERIES-different"
    else:
        conflicting["project_id"] = "PROJ-different"
    with pytest.raises(MemoryLedgerError):
        build_memory_event(**conflicting)


@pytest.mark.parametrize(
    "event_type,field,value",
    [
        ("EXTRACTION_STARTED", "phase", "UNREGISTERED_PHASE"),
        ("EXTRACTION_FINISHED", "attempt_ordinal", 0),
        ("LEDGER_BASELINE_OBJECT", "category", "UNREGISTERED_CATEGORY"),
        ("LEDGER_BASELINE_OBJECT", "locator", "../../outside/secret"),
        ("PROPOSAL_RECORDED", "new_state", "UNREGISTERED_STATE"),
        ("PROPOSAL_RECORDED", "proposal_hash", None),
        ("CANONICAL_ENTITY_CHANGED", "outcome", "UNREGISTERED_OUTCOME"),
        ("CANONICAL_ENTITY_CHANGED", "new_version", 2),
        ("CANONICAL_COMMITTED", "entity_event_ids", ["not-a-memory-event-id"]),
        ("CANONICAL_COMMITTED", "entity_event_ids", []),
        ("RESEARCH_COMMAND_RECORDED", "action", "IMPORT"),
        ("PROPOSAL_STATE_RECORDED", "reason_code", "UNREGISTERED_REASON"),
    ],
)
def test_codec_rejects_unregistered_semantic_values(
    event_type: str, field: str, value: object,
) -> None:
    case = next(item for item in CASES if item["event_type"] == event_type)
    kwargs = _kwargs(case)
    kwargs["structured_payload"][field] = value
    with pytest.raises(MemoryLedgerError):
        build_memory_event(**kwargs)


def test_codec_rejects_unbound_operator_events_invalid_revision_and_surrogate() -> None:
    decision = _kwargs(next(
        item for item in CASES if item["event_type"] == "AUTHOR_DECISION_RECORDED"
    ))
    decision["actor"] = {"kind": "SYSTEM", "id": "forbidden", "evidence_ref": None}
    with pytest.raises(MemoryLedgerError, match="operator authority"):
        build_memory_event(**decision)

    correction = _kwargs(next(
        item for item in CASES if item["event_type"] == "MEMORY_EVENT_CORRECTION"
    ))
    correction["actor"] = {"kind": "SYSTEM", "id": "forbidden", "evidence_ref": None}
    with pytest.raises(MemoryLedgerError, match="operator evidence"):
        build_memory_event(**correction)

    transition = _kwargs(next(
        item for item in CASES if item["event_type"] == "PROPOSAL_STATE_RECORDED"
    ))
    transition["structured_payload"].update(
        phase="FINAL_VALIDATION",
        previous_state="APPROVED_FOR_COMMIT",
        new_state="COMMITTED",
        outcome="ALLOW",
    )
    transition["structured_payload"].pop("reason_code")
    transition["event_slot"] = ("state", "1", "final-validation", "arbitrary-revision")
    with pytest.raises(MemoryLedgerError, match="event_slot"):
        build_memory_event(**transition)

    surrogate = _kwargs(next(
        item for item in CASES if item["event_type"] == "LEDGER_BASELINE_OBJECT"
    ))
    surrogate["structured_payload"]["snapshot_text"] = "\ud800"
    with pytest.raises(MemoryLedgerError, match="Unicode"):
        build_memory_event(**surrogate)


def test_codec_closes_actor_authority_recovery_and_final_approval_binding() -> None:
    bootstrap = _kwargs(next(
        item for item in CASES if item["event_type"] == "LEDGER_ACTIVATED"
    ))
    bootstrap["actor"] = {"kind": "SYSTEM", "id": "OTHER", "evidence_ref": None}
    with pytest.raises(MemoryLedgerError, match="bootstrap event actor"):
        build_memory_event(**bootstrap)

    ordinary = _kwargs(next(
        item for item in CASES if item["event_type"] == "EXTRACTION_STARTED"
    ))
    ordinary["actor"] = {
        "kind": "OPERATOR", "id": "operator-test", "evidence_ref": "authorization-test",
    }
    with pytest.raises(MemoryLedgerError, match="system actor"):
        build_memory_event(**ordinary)

    recovery = _kwargs(next(
        item for item in CASES if item["event_type"] == "RESEARCH_RESULT_RECORDED"
    ))
    recovery["structured_payload"].update(
        action="RECOVERY", outcome="FAILED", reason_code="OPERATOR_RECOVERY",
    )
    with pytest.raises(MemoryLedgerError, match="recovery actor"):
        build_memory_event(**recovery)
    recovery["actor"] = {
        "kind": "OPERATOR", "id": "operator-test",
        "evidence_ref": "research-recovery:operation-1",
    }
    assert build_memory_event(**recovery).actor["kind"] == "OPERATOR"

    final = _kwargs(next(
        item for item in CASES if item["event_type"] == "PROPOSAL_STATE_RECORDED"
    ))
    final["structured_payload"].update(
        phase="FINAL_VALIDATION", previous_state="AWAITING_USER_APPROVAL",
        new_state="COMMITTED", outcome="ALLOW",
    )
    final["structured_payload"].pop("reason_code")
    final["event_slot"] = ("state", "1", "final-validation", "approval-1")
    with pytest.raises(MemoryLedgerError, match="approval revision"):
        build_memory_event(**final)
    final["parent_refs"] = [{
        "kind": "APPROVAL", "owner_scope_type": "PROJECT",
        "owner_scope_id": "PROJ-ledger-codec", "locator": "approval-1",
        "version": "1", "hash_scheme": "SHA256", "hash": H,
    }]
    assert build_memory_event(**final).event_slot[-1] == "approval-1"
    final["structured_payload"]["previous_state"] = "COMMITTED"
    with pytest.raises(MemoryLedgerError, match="state transition"):
        build_memory_event(**final)


def test_codec_rejects_remaining_cross_field_and_reference_bypasses() -> None:
    confirmed = _kwargs(next(
        item for item in CASES if item["event_type"] == "ARTIFACT_WRITE_CONFIRMED"
    ))
    confirmed["structured_payload"]["observed_hash"] = "b" * 64
    with pytest.raises(MemoryLedgerError, match="artifact confirmation"):
        build_memory_event(**confirmed)

    research = _kwargs(next(
        item for item in CASES if item["event_type"] == "RESEARCH_RESULT_RECORDED"
    ))
    research["structured_payload"]["result_refs"] = []
    with pytest.raises(MemoryLedgerError, match="durable result_refs"):
        build_memory_event(**research)

    chapter = _kwargs(next(
        item for item in CASES if item["event_type"] == "CHAPTER_VERSION_RECORDED"
    ))
    chapter["structured_payload"]["chapter_id"] = "../../outside"
    chapter["event_slot"] = ("chapter-version", "../../outside", "1")
    with pytest.raises(MemoryLedgerError, match="chapter version identity"):
        build_memory_event(**chapter)

    entity = _kwargs(next(
        item for item in CASES if item["event_type"] == "EXTRACTION_STARTED"
    ))
    entity["entity_refs"] = [{
        "record_type": "UNKNOWN", "entity_id": "../../outside", "version": 1,
    }]
    with pytest.raises(MemoryLedgerError, match="entity reference identity"):
        build_memory_event(**entity)

    commit = _kwargs(next(
        item for item in CASES if item["event_type"] == "CANONICAL_COMMITTED"
    ))
    commit["parent_refs"] = [{
        "kind": "BASELINE_OBJECT", "owner_scope_type": "PROJECT",
        "owner_scope_id": "PROJ-ledger-codec", "locator": "metadata:test",
        "version": None, "hash_scheme": None, "hash": None,
    }]
    with pytest.raises(MemoryLedgerError, match="bootstrap-only"):
        build_memory_event(**commit)


@pytest.mark.parametrize(
    "locator",
    ["metadata:C:/outside", "metadata:../outside", "project_metadata:https://outside"],
)
def test_codec_rejects_internal_prefix_locator_escape(locator: str) -> None:
    case = next(item for item in CASES if item["event_type"] == "EXTRACTION_STARTED")
    kwargs = _kwargs(case)
    kwargs["parent_refs"] = [{
        "kind": "SOURCE", "owner_scope_type": "PROJECT",
        "owner_scope_id": "PROJ-ledger-codec", "locator": locator,
        "version": "1", "hash_scheme": "SHA256", "hash": H,
    }]
    with pytest.raises(MemoryLedgerError, match="locator"):
        build_memory_event(**kwargs)


@pytest.mark.parametrize("path", ["C:/outside", "https://outside", "artifacts/../outside"])
def test_codec_rejects_unconfined_artifact_path(path: str) -> None:
    intended = _kwargs(next(
        item for item in CASES if item["event_type"] == "ARTIFACT_WRITE_INTENDED"
    ))
    intended["structured_payload"]["confined_path"] = path
    with pytest.raises(MemoryLedgerError, match="confined_path"):
        build_memory_event(**intended)


def test_from_dict_requires_json_object_shapes_and_preserves_unicode_forms() -> None:
    baseline = _kwargs(next(
        item for item in CASES if item["event_type"] == "LEDGER_BASELINE_OBJECT"
    ))
    baseline["structured_payload"]["snapshot_text"] = "\u00e9"
    composed = build_memory_event(**baseline)
    persisted = json.loads(composed.to_json())
    persisted["operation"] = [["namespace", "LEDGER_BOOTSTRAP"], ["id", "operation-1"]]
    with pytest.raises(MemoryLedgerError, match="operation must be an object"):
        MemoryEventRecord.from_dict(persisted)

    decomposed_kwargs = deepcopy(baseline)
    decomposed_kwargs["operation"] = {"namespace": "LEDGER_BOOTSTRAP", "id": "operation-2"}
    decomposed_kwargs["structured_payload"]["snapshot_text"] = "e\u0301"
    decomposed = build_memory_event(**decomposed_kwargs)
    assert MemoryEventRecord.from_dict(json.loads(composed.to_json())) == composed
    assert MemoryEventRecord.from_dict(json.loads(decomposed.to_json())) == decomposed
    assert composed.structured_payload["snapshot_text"] != decomposed.structured_payload["snapshot_text"]
    assert composed.content_hash != decomposed.content_hash


def test_codec_accepts_terminal_reject_with_verification_evidence_only() -> None:
    terminal = _kwargs(next(
        item for item in CASES if item["event_type"] == "EXTRACTION_FINISHED"
    ))
    terminal["structured_payload"].pop("candidate_refs")
    terminal["structured_payload"]["outcome"] = "REJECT"
    event = build_memory_event(**terminal)
    assert event.structured_payload["verification_refs"] == ["verification:1"]


@pytest.mark.parametrize(
    "reference",
    [
        {
            "kind": "SOURCE",
            "owner_scope_type": "PROJECT",
            "owner_scope_id": "PROJ-ledger-codec",
            "locator": "../../outside/secret",
            "version": "1",
            "hash_scheme": "sha256",
            "hash": H,
        },
        {
            "kind": "SOURCE",
            "owner_scope_type": "PROJECT",
            "owner_scope_id": "PROJ-ledger-codec",
            "locator": "file:/outside/secret",
            "version": "1",
            "hash_scheme": "sha256",
            "hash": H,
        },
        {
            "kind": "ARTIFACT",
            "owner_scope_type": "PROJECT",
            "owner_scope_id": "PROJ-ledger-codec",
            "locator": "artifacts/neutral.json:alternate-stream",
            "version": "1",
            "hash_scheme": "sha256",
            "hash": H,
        },
        {
            "kind": "MEMORY_EVENT",
            "owner_scope_type": "PROJECT",
            "owner_scope_id": "PROJ-ledger-codec",
            "locator": "MEV-not-a-valid-id",
            "version": "1",
            "hash_scheme": "sha256",
            "hash": H,
        },
        {
            "kind": "SOURCE",
            "owner_scope_type": "PROJECT",
            "owner_scope_id": "not-a-project-domain-id",
            "locator": "source/neutral",
            "version": "1",
            "hash_scheme": "sha256",
            "hash": H,
        },
    ],
)
def test_codec_rejects_unconfined_or_malformed_typed_reference(reference: dict) -> None:
    case = next(item for item in CASES if item["event_type"] == "EXTRACTION_STARTED")
    kwargs = _kwargs(case)
    kwargs["parent_refs"] = [reference]
    with pytest.raises(MemoryLedgerError):
        build_memory_event(**kwargs)


def test_codec_rejects_invalid_series_target_and_correction_without_parent() -> None:
    series = _kwargs(next(item for item in CASES if item["event_type"] == "SERIES_VOLUME_CLOSED"))
    series["structured_payload"]["transferred_entity_refs"] = [{
        "record_type": "FACT",
        "entity_id": "FACT-neutral",
        "version": 1,
        "target": "UNREGISTERED_TARGET",
    }]
    with pytest.raises(MemoryLedgerError, match="transfer target"):
        build_memory_event(**series)

    correction = _kwargs(next(
        item for item in CASES if item["event_type"] == "MEMORY_EVENT_CORRECTION"
    ))
    correction["parent_refs"] = []
    with pytest.raises(MemoryLedgerError, match="exact target memory event parent"):
        build_memory_event(**correction)


def test_persisted_read_revalidates_closed_semantics(isolated_agentpro_storage) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-persisted-codec", book_id="BOOK-ledger-persisted-codec"
    ))
    repository.initialize()
    case = next(item for item in CASES if item["event_type"] == "EXTRACTION_STARTED")
    kwargs = _kwargs(case)
    kwargs.update({
        "scope_id": repository.context.project_id,
        "project_id": repository.context.project_id,
        "book_id": repository.context.book_id,
    })
    with repository.connect() as connection:
        kwargs["sequence"] = next_sequence(connection)
        event = build_memory_event(**kwargs)
        append_memory_event(connection, event)
        corrupted = event.to_dict()
        corrupted["structured_payload"]["phase"] = "UNREGISTERED_PHASE"
        without_hash = dict(corrupted)
        without_hash.pop("content_hash")
        corrupted["content_hash"] = sha256_text(canonical_json(without_hash))
        connection.execute("DROP TRIGGER memory_events_reject_update")
        connection.execute(
            "UPDATE memory_events SET record_json=?,content_hash=? WHERE memory_event_id=?",
            (canonical_json(corrupted), corrupted["content_hash"], event.memory_event_id),
        )
        with pytest.raises(MemoryLedgerError, match="extraction start payload"):
            read_memory_event(connection, event.memory_event_id)


@pytest.mark.parametrize("mutation", ["payload", "nested_payload", "typed_reference"])
def test_public_append_revalidates_mutable_event_snapshot(
    isolated_agentpro_storage, mutation: str,
) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-mutable-" + mutation.replace("_", "-"),
        book_id="BOOK-ledger-mutable-" + mutation.replace("_", "-"),
    ))
    repository.initialize()
    case = next(item for item in CASES if item["event_type"] == "EXTRACTION_STARTED")
    kwargs = _kwargs(case)
    kwargs.update({
        "scope_id": repository.context.project_id,
        "project_id": repository.context.project_id,
        "book_id": repository.context.book_id,
        "parent_refs": [{
            "kind": "SOURCE", "owner_scope_type": "PROJECT",
            "owner_scope_id": repository.context.project_id,
            "locator": "source/neutral", "version": "1",
            "hash_scheme": "SHA256", "hash": H,
        }],
    })
    with repository.connect() as connection:
        kwargs["sequence"] = next_sequence(connection)
        event = build_memory_event(**kwargs)
        if mutation == "payload":
            event.structured_payload["outcome"] = "INVALID"
        elif mutation == "nested_payload":
            event.structured_payload["source_refs"].append("source:2")
        else:
            event.parent_refs[0]["locator"] = "source/changed"
        with pytest.raises((MemoryLedgerError, MemoryLedgerIntegrityError)):
            repository.append_memory_event(connection, event)
        assert connection.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0] == 1


def test_public_append_untouched_event_reopens_and_retries_idempotently(
    isolated_agentpro_storage,
) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-mutable-valid", book_id="BOOK-ledger-mutable-valid",
    ))
    repository.initialize()
    case = next(item for item in CASES if item["event_type"] == "EXTRACTION_STARTED")
    kwargs = _kwargs(case)
    kwargs.update({
        "scope_id": repository.context.project_id,
        "project_id": repository.context.project_id,
        "book_id": repository.context.book_id,
    })
    with repository.connect() as connection:
        kwargs["sequence"] = next_sequence(connection)
        event = build_memory_event(**kwargs)
        first = repository.append_memory_event(connection, event)
        retried = repository.append_memory_event(connection, event)
        assert retried == first
    reopened = ProjectRepository(repository.context)
    assert reopened.get_memory_event(event.memory_event_id) == first


def _drop_to_project_v5(repository: ProjectRepository) -> None:
    with repository.connect() as connection:
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'memory_event%'"
        ).fetchall():
            connection.execute(f"DROP TRIGGER {row[0]}")
        connection.execute("DROP TABLE memory_event_entities")
        connection.execute("DROP TABLE memory_events")
        connection.execute("DELETE FROM project_metadata WHERE key='memory_ledger_control.v1'")
        connection.execute("UPDATE schema_version SET version=5 WHERE id=1")
        connection.execute("UPDATE project_identity SET schema_version=5 WHERE id=1")


def _drop_to_series_v3(repository: SeriesRepository) -> None:
    with repository.connect() as connection:
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'memory_event%'"
        ).fetchall():
            connection.execute(f"DROP TRIGGER {row[0]}")
        connection.execute("DROP TABLE memory_event_entities")
        connection.execute("DROP TABLE memory_events")
        connection.execute("DELETE FROM series_metadata WHERE key='memory_ledger_control.v1'")
        connection.execute("UPDATE schema_version SET version=3 WHERE id=1")
        connection.execute("UPDATE series_identity SET schema_version=3 WHERE id=1")


def _migrate(repository: ProjectRepository, backup) -> None:
    repository.migrate_schema(
        backup_path=backup,
        maintenance_confirmed=True,
        release_head="TEST-HEAD",
    )


def test_migration_backup_manifest_wal_deadline_and_maintenance_gate(
    isolated_agentpro_storage,
) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-repair-migrate", book_id="BOOK-ledger-repair-migrate"
    ))
    repository.initialize()
    _drop_to_project_v5(repository)
    with repository.connect() as connection:
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute(
            "INSERT INTO project_metadata(key,value) VALUES('canonical_versions.v1:wal','{\"version\":1}')"
        )
    backup = isolated_agentpro_storage / "repair-migration.backup"
    with pytest.raises(SchemaMigrationError, match="maintenance"):
        repository.migrate_schema(backup_path=backup, release_head="TEST-HEAD")
    with pytest.raises(SchemaMigrationError, match="deadline"):
        repository.migrate_schema(
            backup_path=backup,
            maintenance_confirmed=True,
            release_head="TEST-HEAD",
            backup_deadline_seconds=1e-12,
        )
    assert repository.inspect_schema().current_version == 5

    _migrate(repository, backup)
    manifest_path = backup.with_name(backup.name + ".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["scope_id"] == repository.context.project_id
    assert manifest["source_schema_version"] == 5
    assert manifest["head"] == "TEST-HEAD"
    assert set(manifest["validation"].values()) == {"ok"}
    with sqlite3.connect(backup) as copied:
        assert copied.execute(
            "SELECT value FROM project_metadata WHERE key='canonical_versions.v1:wal'"
        ).fetchone()[0] == '{"version":1}'


def test_migration_reservation_blocks_writer_after_backup_before_ddl(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-repair-lock", book_id="BOOK-ledger-repair-lock"
    ))
    repository.initialize()
    _drop_to_project_v5(repository)
    original = repository_module._create_verified_sqlite_backup
    observed: dict[str, int] = {}

    def backup_then_probe_writer(*args, **kwargs):
        original(*args, **kwargs)
        writer = sqlite3.connect(repository.db_path, timeout=0.05)
        try:
            writer.execute("PRAGMA busy_timeout=50")
            with pytest.raises(sqlite3.OperationalError) as exc_info:
                writer.execute(
                    "INSERT INTO project_metadata(key,value) VALUES('writer-between-backup-and-ddl','blocked')"
                )
            observed["sqlite_errorcode"] = int(exc_info.value.sqlite_errorcode)
        finally:
            writer.close()

    monkeypatch.setattr(
        repository_module, "_create_verified_sqlite_backup", backup_then_probe_writer
    )
    backup = isolated_agentpro_storage / "reservation-window.backup"
    _migrate(repository, backup)
    assert observed == {"sqlite_errorcode": sqlite3.SQLITE_BUSY}
    assert repository.inspect_schema().current_version == PROJECT_DB_SCHEMA_VERSION
    with repository.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM project_metadata WHERE key='writer-between-backup-and-ddl'"
        ).fetchone() is None


def test_migration_busy_lock_maps_to_canonical_outcome_and_retry_is_clean(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-maintenance-busy", book_id="BOOK-ledger-maintenance-busy"
    ))
    repository.initialize()
    _drop_to_project_v5(repository)
    monkeypatch.setattr(repository_module, "_DOMAIN_DB_BUSY_TIMEOUT_MS", 50)
    backup = isolated_agentpro_storage / "maintenance-busy.backup"
    locker = sqlite3.connect(str(repository.db_path), timeout=0.05)
    try:
        locker.execute("PRAGMA busy_timeout=50")
        locker.execute("BEGIN IMMEDIATE")
        with pytest.raises(SchemaMigrationError, match="^MAINTENANCE_BUSY$"):
            _migrate(repository, backup)
        assert not backup.exists()
        assert not backup.with_name(backup.name + ".manifest.json").exists()
        with sqlite3.connect(repository.db_path) as observer:
            assert observer.execute(
                "SELECT version FROM schema_version WHERE id=1"
            ).fetchone()[0] == 5
            assert observer.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_events'"
            ).fetchone() is None
    finally:
        locker.rollback()
        locker.close()

    _migrate(repository, backup)
    assert repository.inspect_schema().current_version == PROJECT_DB_SCHEMA_VERSION


def test_initial_schema_read_lock_maps_to_maintenance_busy(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-initial-read-lock", book_id="BOOK-ledger-initial-read-lock"
    ))
    repository.initialize()
    _drop_to_project_v5(repository)
    monkeypatch.setattr(repository_module, "_DOMAIN_DB_BUSY_TIMEOUT_MS", 50)
    locker = sqlite3.connect(str(repository.db_path), timeout=0.05)
    try:
        locker.execute("PRAGMA journal_mode=DELETE")
        locker.execute("PRAGMA locking_mode=EXCLUSIVE")
        locker.execute("BEGIN EXCLUSIVE")
        locker.execute("UPDATE schema_version SET version=version WHERE id=1")
        with pytest.raises(SchemaMigrationError, match="^MAINTENANCE_BUSY$"):
            _migrate(repository, isolated_agentpro_storage / "initial-read-lock.backup")
    finally:
        locker.rollback()
        locker.close()
    assert repository.inspect_schema().current_version == 5


def test_migration_does_not_mask_unrelated_sqlite_operational_error() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE schema_version(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL)"
    )
    connection.execute("INSERT INTO schema_version(id,version) VALUES(1,1)")
    connection.commit()

    def fail_with_non_lock_error(_: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("synthetic unrelated sqlite failure")

    runner = SchemaMigrationRunner(
        target_version=2,
        migrations=(SchemaMigration(1, 2, fail_with_non_lock_error),),
    )
    with pytest.raises(sqlite3.OperationalError, match="synthetic unrelated sqlite failure"):
        runner.migrate(connection)
    assert connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == 1
    connection.close()


def test_series_migration_locked_maps_to_canonical_outcome(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    repository = SeriesRepository(
        StorageResolver().resolve_series("SERIES-ledger-maintenance-locked")
    )
    repository.initialize()
    locked = sqlite3.OperationalError("synthetic database table is locked")
    locked.sqlite_errorcode = sqlite3.SQLITE_LOCKED

    def raise_locked(*_args, **_kwargs):
        raise locked

    monkeypatch.setattr(repository_module, "_migration_inspect", raise_locked)
    with pytest.raises(SchemaMigrationError, match="^MAINTENANCE_BUSY$"):
        repository.migrate_schema()


def test_series_migration_busy_rolls_back_and_retry_is_clean(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    repository = SeriesRepository(
        StorageResolver().resolve_series("SERIES-ledger-maintenance-busy")
    )
    repository.initialize()
    _drop_to_series_v3(repository)
    monkeypatch.setattr(repository_module, "_DOMAIN_DB_BUSY_TIMEOUT_MS", 50)
    backup = isolated_agentpro_storage / "series-maintenance-busy.backup"
    locker = sqlite3.connect(str(repository.db_path), timeout=0.05)
    try:
        locker.execute("PRAGMA busy_timeout=50")
        locker.execute("BEGIN IMMEDIATE")
        with pytest.raises(SchemaMigrationError, match="^MAINTENANCE_BUSY$"):
            repository.migrate_schema(
                backup_path=backup,
                maintenance_confirmed=True,
                release_head="TEST-HEAD",
            )
        assert not backup.exists()
        with sqlite3.connect(repository.db_path) as observer:
            assert observer.execute(
                "SELECT version FROM schema_version WHERE id=1"
            ).fetchone()[0] == 3
            assert observer.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_events'"
            ).fetchone() is None
    finally:
        locker.rollback()
        locker.close()

    repository.migrate_schema(
        backup_path=backup,
        maintenance_confirmed=True,
        release_head="TEST-HEAD",
    )
    assert repository.inspect_schema().current_version == 4


def test_backup_target_open_failure_closes_source_and_removes_partial(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-backup-open", book_id="BOOK-ledger-backup-open"
    ))
    repository.initialize()
    destination = isolated_agentpro_storage / "target-open-failure.backup"
    real_connect = sqlite3.connect
    opened_sources: list[sqlite3.Connection] = []

    def fail_target_open(*args, **kwargs):
        if not opened_sources:
            source = real_connect(*args, **kwargs)
            opened_sources.append(source)
            return source
        raise sqlite3.OperationalError("synthetic target open failure")

    monkeypatch.setattr(repository_module.sqlite3, "connect", fail_target_open)
    with pytest.raises(sqlite3.OperationalError, match="synthetic target open failure"):
        repository_module._create_verified_sqlite_backup(
            repository.db_path,
            destination,
            expected_version=PROJECT_DB_SCHEMA_VERSION,
            scope_type="PROJECT",
            scope_id=repository.context.project_id,
            expected_identity={
                "project_id": repository.context.project_id,
                "book_id": repository.context.book_id,
                "schema_version": PROJECT_DB_SCHEMA_VERSION,
            },
            release_head="TEST-HEAD",
            deadline_seconds=1,
        )
    assert len(opened_sources) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened_sources[0].execute("SELECT 1")
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".manifest.json").exists()


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
def test_backup_never_overwrites_existing_target_for_project_and_series(
    isolated_agentpro_storage, scope: str,
) -> None:
    if scope == "PROJECT":
        repository = ProjectRepository(StorageResolver().resolve_project(
            "PROJ-ledger-backup-existing", book_id="BOOK-ledger-backup-existing",
        ))
        expected_identity = {
            "project_id": repository.context.project_id,
            "book_id": repository.context.book_id,
            "schema_version": PROJECT_DB_SCHEMA_VERSION,
        }
    else:
        repository = SeriesRepository(
            StorageResolver().resolve_series("SERIES-ledger-backup-existing")
        )
        expected_identity = {
            "series_id": repository.context.series_id,
            "schema_version": repository_module.SERIES_DB_SCHEMA_VERSION,
        }
    repository.initialize()
    destination = isolated_agentpro_storage / (scope.lower() + "-existing.backup")
    with sqlite3.connect(destination) as sentinel:
        sentinel.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
        sentinel.execute("INSERT INTO sentinel(value) VALUES('retained')")
    with pytest.raises(SchemaMigrationError, match="already exists"):
        repository_module._create_verified_sqlite_backup(
            repository.db_path,
            destination,
            expected_version=expected_identity["schema_version"],
            scope_type=scope,
            scope_id=(repository.context.project_id if scope == "PROJECT"
                      else repository.context.series_id),
            expected_identity=expected_identity,
            release_head="TEST-HEAD",
            deadline_seconds=1,
        )
    with sqlite3.connect(destination) as sentinel:
        assert sentinel.execute("SELECT value FROM sentinel").fetchone()[0] == "retained"


def test_backup_concurrent_target_creation_is_retained(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-backup-race", book_id="BOOK-ledger-backup-race",
    ))
    repository.initialize()
    destination = isolated_agentpro_storage / "concurrent-target.backup"
    real_open = repository_module.os.open
    injected = False

    def concurrent_create(path, flags, mode=0o777):
        nonlocal injected
        if not injected and str(path) == str(destination):
            injected = True
            with sqlite3.connect(destination) as sentinel:
                sentinel.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
                sentinel.execute("INSERT INTO sentinel(value) VALUES('retained')")
        return real_open(path, flags, mode)

    monkeypatch.setattr(repository_module.os, "open", concurrent_create)
    with pytest.raises(SchemaMigrationError, match="already exists"):
        repository_module._create_verified_sqlite_backup(
            repository.db_path,
            destination,
            expected_version=PROJECT_DB_SCHEMA_VERSION,
            scope_type="PROJECT",
            scope_id=repository.context.project_id,
            expected_identity={
                "project_id": repository.context.project_id,
                "book_id": repository.context.book_id,
                "schema_version": PROJECT_DB_SCHEMA_VERSION,
            },
            release_head="TEST-HEAD",
            deadline_seconds=1,
        )
    with sqlite3.connect(destination) as sentinel:
        assert sentinel.execute("SELECT value FROM sentinel").fetchone()[0] == "retained"


def test_backup_validation_uses_separate_reopen_and_cleans_failure(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-backup-reopen", book_id="BOOK-ledger-backup-reopen",
    ))
    repository.initialize()
    destination = isolated_agentpro_storage / "validation-reopen.backup"
    real_connect = sqlite3.connect
    target_opens = 0

    def fail_validation_reopen(database, *args, **kwargs):
        nonlocal target_opens
        if destination.name in str(database):
            target_opens += 1
            if target_opens == 2:
                raise sqlite3.OperationalError("synthetic validation reopen failure")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(repository_module.sqlite3, "connect", fail_validation_reopen)
    with pytest.raises(sqlite3.OperationalError, match="validation reopen"):
        repository_module._create_verified_sqlite_backup(
            repository.db_path,
            destination,
            expected_version=PROJECT_DB_SCHEMA_VERSION,
            scope_type="PROJECT",
            scope_id=repository.context.project_id,
            expected_identity={
                "project_id": repository.context.project_id,
                "book_id": repository.context.book_id,
                "schema_version": PROJECT_DB_SCHEMA_VERSION,
            },
            release_head="TEST-HEAD",
            deadline_seconds=1,
        )
    assert target_opens == 2
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".manifest.json").exists()


@pytest.mark.parametrize("damage", [
    "missing_control", "bad_control", "missing_table", "missing_index",
    "missing_trigger", "wrong_trigger", "missing_domain_table", "identity_version",
    "active_binding", "extra_check", "extra_unique", "extra_foreign_key",
    "extra_inline_unique", "unexpected_index", "unexpected_trigger", "unexpected_table",
])
def test_target_schema_and_control_are_fail_closed(isolated_agentpro_storage, damage: str) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-schema-" + damage.replace("_", "-"), book_id="BOOK-ledger-schema"
    ))
    repository.initialize()
    with repository.connect() as connection:
        if damage == "missing_control":
            connection.execute("DELETE FROM project_metadata WHERE key='memory_ledger_control.v1'")
        elif damage == "bad_control":
            connection.execute(
                "UPDATE project_metadata SET value='{}' WHERE key='memory_ledger_control.v1'"
            )
        elif damage == "missing_table":
            connection.execute("DROP TABLE memory_event_entities")
        elif damage == "missing_index":
            connection.execute("DROP INDEX memory_events_operation_lookup")
        elif damage == "missing_trigger":
            connection.execute("DROP TRIGGER memory_events_reject_update")
        elif damage == "wrong_trigger":
            connection.execute("DROP TRIGGER memory_events_reject_update")
            connection.execute(
                "CREATE TRIGGER memory_events_reject_update BEFORE UPDATE ON memory_events BEGIN SELECT 1; END"
            )
        elif damage == "missing_domain_table":
            connection.execute("DROP TABLE project_fact_records")
        elif damage == "identity_version":
            connection.execute("UPDATE project_identity SET schema_version=5 WHERE id=1")
        elif damage == "active_binding":
            control = json.loads(connection.execute(
                "SELECT value FROM project_metadata WHERE key='memory_ledger_control.v1'"
            ).fetchone()[0])
            control["activation_event_id"] = "MEV-" + "0" * 64
            connection.execute(
                "UPDATE project_metadata SET value=? WHERE key='memory_ledger_control.v1'",
                (json.dumps(control, sort_keys=True, separators=(",", ":")),),
            )
        elif damage == "unexpected_index":
            connection.execute(
                "CREATE INDEX unexpected_fact_scope ON project_fact_records(scope_id)"
            )
        elif damage == "unexpected_trigger":
            connection.execute(
                "CREATE TRIGGER unexpected_memory_trigger BEFORE INSERT ON memory_events "
                "BEGIN SELECT 1; END"
            )
        elif damage == "unexpected_table":
            connection.execute("CREATE TABLE unexpected_memory_table(id INTEGER PRIMARY KEY)")
        elif damage == "extra_inline_unique":
            connection.execute("ALTER TABLE project_metadata RENAME TO old_project_metadata")
            connection.execute(
                "CREATE TABLE project_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL UNIQUE)"
            )
            connection.execute(
                "INSERT INTO project_metadata(key,value) SELECT key,value FROM old_project_metadata"
            )
            connection.execute("DROP TABLE old_project_metadata")
        elif damage in {"extra_check", "extra_unique", "extra_foreign_key"}:
            connection.execute("ALTER TABLE project_fact_records RENAME TO old_project_fact_records")
            extra = {
                "extra_check": ", CHECK (length(fact_id) > 0)",
                "extra_unique": ", UNIQUE (scope_id)",
                "extra_foreign_key": ", FOREIGN KEY (scope_id) REFERENCES project_identity(project_id)",
            }[damage]
            connection.execute(
                "CREATE TABLE project_fact_records ("
                "scope_type TEXT NOT NULL, scope_id TEXT NOT NULL, "
                "fact_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL"
                + extra + ")"
            )
            connection.execute("DROP TABLE old_project_fact_records")
    with pytest.raises((SchemaMigrationError, MemoryLedgerIntegrityError, MemoryLedgerNotActive, sqlite3.Error)):
        repository.migrate_schema()
    with pytest.raises((SchemaMigrationError, MemoryLedgerIntegrityError, MemoryLedgerNotActive, sqlite3.Error)):
        repository.initialize()
    if damage == "missing_domain_table":
        with sqlite3.connect(repository.db_path) as connection:
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_fact_records'"
            ).fetchone() is None


@pytest.mark.parametrize(
    "column,value",
    [
        ("event_type", "UNREGISTERED_EVENT"),
        ("operation_id", ""),
        ("content_hash", "A" * 64),
        ("content_hash", "g" * 64),
    ],
)
def test_frozen_sql_checks_reject_invalid_rows(
    isolated_agentpro_storage, column: str, value: str,
) -> None:
    suffix = column.replace("_", "-") + "-" + value[:4].lower().replace(" ", "x")
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-sql-check-" + suffix, book_id="BOOK-ledger-sql-check-" + suffix
    ))
    repository.initialize()
    row = {
        "sequence": 100,
        "memory_event_id": "MEV-" + H,
        "event_key_json": '{"synthetic":"' + suffix + '"}',
        "scope_type": "PROJECT",
        "scope_id": repository.context.project_id,
        "operation_namespace": "LEDGER_BOOTSTRAP",
        "operation_id": "synthetic-operation",
        "event_type": "LEDGER_ACTIVATED",
        "schema_version": 1,
        "record_json": "{}",
        "content_hash": H,
    }
    row[column] = value
    with sqlite3.connect(repository.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO memory_events(sequence,memory_event_id,event_key_json,scope_type,"
                "scope_id,operation_namespace,operation_id,event_type,schema_version,record_json,"
                "content_hash) VALUES(:sequence,:memory_event_id,:event_key_json,:scope_type,"
                ":scope_id,:operation_namespace,:operation_id,:event_type,:schema_version,"
                ":record_json,:content_hash)",
                row,
            )


def test_memory_ledger_schema_rejects_missing_frozen_check(tmp_path) -> None:
    database_path = tmp_path / "missing-check.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE project_identity(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO project_identity(id,project_id) VALUES(1,'PROJ-ledger-missing-check')"
        )
        create_memory_ledger_schema(
            connection,
            scope_type="PROJECT",
            identity_table="project_identity",
            identity_column="project_id",
        )
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_events'"
        ).fetchone()[0]
        damaged = sql.replace("CHECK (length(trim(operation_id)) > 0)", "")
        assert damaged != sql
        version = connection.execute("PRAGMA schema_version").fetchone()[0]
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='memory_events'",
            (damaged,),
        )
        connection.execute("PRAGMA writable_schema=OFF")
        connection.execute(f"PRAGMA schema_version={version + 1}")
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        with pytest.raises(MemoryLedgerIntegrityError, match="CHECK constraints"):
            validate_memory_ledger_schema(connection, scope_type="PROJECT")


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize("damage", ["extra", "missing", "altered", "literal_case"])
def test_public_schema_validation_rejects_nonexact_ledger_checks(
    isolated_agentpro_storage, scope: str, damage: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-check-" + damage,
    )
    repository.initialize()

    def damage_sql(sql: str) -> str:
        if damage == "extra":
            close = sql.rfind(")")
            assert close > 0
            return (
                sql[:close]
                + ", CHECK(operation_id != 'DENIED_SYNTHETIC_OPERATION')"
                + sql[close:]
            )
        if damage == "missing":
            return sql.replace("CHECK (length(trim(operation_id)) > 0)", "", 1)
        if damage == "literal_case":
            return sql.replace("'MEV-'", "'mev-'", 1)
        return sql.replace(
            "CHECK (schema_version = 1)",
            "CHECK (schema_version IN (1, 2))",
            1,
        )

    _rewrite_table_schema_sql(repository, "memory_events", damage_sql)
    _assert_public_schema_validation_rejects(repository, match="CHECK constraints")


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize("comment_kind", ["block", "line"])
def test_public_schema_validation_rejects_check_text_only_in_comment(
    isolated_agentpro_storage, scope: str, comment_kind: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-comment-masquerade-" + comment_kind,
    )
    repository.initialize()
    required = "CHECK (length(trim(operation_id)) > 0)"
    replacement = (
        "/* " + required + " */"
        if comment_kind == "block"
        else "-- " + required + "\n"
    )
    _rewrite_table_schema_sql(
        repository, "memory_events",
        lambda sql: sql.replace(required, replacement, 1),
    )
    _assert_public_schema_validation_rejects(repository, match="CHECK constraints")


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize("comment_kind", ["block", "line"])
def test_public_schema_validation_ignores_extra_check_text_in_comment(
    isolated_agentpro_storage, scope: str, comment_kind: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-comment-extra-" + comment_kind,
    )
    repository.initialize()

    def add_comment(sql: str) -> str:
        close = sql.rfind(")")
        assert close > 0
        comment = (
            " /* CHECK(operation_id != 'DENIED_SYNTHETIC_OPERATION') */ "
            if comment_kind == "block"
            else " -- CHECK(operation_id != 'DENIED_SYNTHETIC_OPERATION')\n"
        )
        return sql[:close] + comment + sql[close:]

    _rewrite_table_schema_sql(repository, "memory_events", add_comment)
    repository.migrate_schema()
    repository.initialize()


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize(
    "line_ending,required_check_is_executable",
    [("\r", False), ("\n", True), ("\r\n", True)],
    ids=["bare-cr", "lf", "crlf"],
)
def test_public_schema_validation_matches_sqlite_line_comment_endings(
    isolated_agentpro_storage,
    scope: str,
    line_ending: str,
    required_check_is_executable: bool,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage,
        scope,
        scope.lower() + "-line-ending-" + line_ending.encode().hex(),
    )
    repository.initialize()
    required = "CHECK (length(trim(operation_id)) > 0)"
    replacement = "-- neutral comment" + line_ending + " " + required + "\n"
    _rewrite_table_schema_sql(
        repository,
        "memory_events",
        lambda sql: sql.replace(required, replacement, 1),
    )
    if not required_check_is_executable:
        _assert_public_schema_validation_rejects(repository, match="CHECK constraints")
        return
    repository.migrate_schema()
    repository.initialize()


@pytest.mark.parametrize(
    "line_ending,check_is_enforced",
    [
        ("\r", False),
        ("\n", True),
        ("\r\n", True),
        ("\n\n", True),
        ("\r\n\r\n", True),
        ("\r\r\n", True),
    ],
    ids=["bare-cr", "lf", "crlf", "repeated-lf", "repeated-crlf", "repeated-cr-before-lf"],
)
def test_schema_check_lexer_matches_sqlite_line_comment_semantics(
    line_ending: str,
    check_is_enforced: bool,
) -> None:
    executable_check = "CHECK(length(value) > 0)"
    sql = (
        "CREATE TABLE neutral(value TEXT -- neutral comment"
        + line_ending
        + " "
        + executable_check
        + "\n)"
    )
    with sqlite3.connect(":memory:") as connection:
        connection.execute(sql)
        stored_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='neutral'"
        ).fetchone()[0]
        assert line_ending in stored_sql
        if check_is_enforced:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO neutral(value) VALUES('')")
        else:
            connection.execute("INSERT INTO neutral(value) VALUES('')")
    expected = _schema_check_signatures(executable_check) if check_is_enforced else ()
    assert _schema_check_signatures(stored_sql) == expected


def test_schema_check_lexer_ignores_comments_and_preserves_string_literals() -> None:
    real_check = (
        "CHECK(reason != '/* not a comment */' "
        "AND reason != '-- still literal' AND reason != 'it''s valid' "
        "AND reason != 'bare\rcr literal' AND reason != 'CHECK(fake)')"
    )
    sql = (
        "CREATE TABLE neutral(reason TEXT " + real_check
        + " /* CHECK(block_fake) */ -- CHECK(line_fake)\n)"
    )
    assert _schema_check_signatures(sql) == _schema_check_signatures(real_check)
    tokens = _schema_sql_tokens(sql)
    assert "'/* not a comment */'" in tokens
    assert "'-- still literal'" in tokens
    assert "'it''s valid'" in tokens
    assert "'bare\rcr literal'" in tokens
    assert "'CHECK(fake)'" in tokens
    assert "block_fake" not in tokens and "line_fake" not in tokens
    with pytest.raises(MemoryLedgerIntegrityError, match="block comment"):
        _schema_sql_tokens("CREATE TABLE neutral(value TEXT /* unterminated")


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize(
    "damage",
    [
        "foreign_metadata", "foreign_metadata_upper", "foreign_metadata_mixed",
        "foreign_metadata_quoted_upper", "foreign_other", "foreign_other_upper",
        "foreign_ledger_mixed", "missing_expected", "altered_expected",
    ],
)
def test_public_schema_validation_rejects_nonexact_authoritative_triggers(
    isolated_agentpro_storage, scope: str, damage: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-trigger-" + damage,
    )
    repository.initialize()
    metadata_table = "project_metadata" if scope == "PROJECT" else "series_metadata"
    other_table = "project_fact_records" if scope == "PROJECT" else "series_memberships"
    with sqlite3.connect(repository.db_path) as connection:
        if damage.startswith("foreign_"):
            if damage.startswith("foreign_metadata"):
                table = metadata_table
            elif damage.startswith("foreign_other"):
                table = other_table
            else:
                table = "memory_events"
            if damage.endswith("upper"):
                table = table.upper()
            elif damage.endswith("mixed"):
                table = "_".join(part.capitalize() for part in table.split("_"))
            if damage.endswith("quoted_upper"):
                table = '"' + table.upper() + '"'
            connection.execute(
                f"CREATE TRIGGER unexpected_authority_trigger BEFORE INSERT ON {table} "
                "BEGIN SELECT RAISE(ABORT,'SYNTHETIC_DENIED'); END"
            )
        else:
            connection.execute("DROP TRIGGER memory_events_reject_update")
            if damage == "altered_expected":
                connection.execute(
                    "CREATE TRIGGER memory_events_reject_update "
                    "BEFORE UPDATE ON memory_events BEGIN SELECT 1; END"
                )
    _assert_public_schema_validation_rejects(repository, match="trigger")


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
def test_public_schema_validation_accepts_exact_checks_with_harmless_formatting(
    isolated_agentpro_storage, scope: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-check-formatting",
    )
    repository.initialize()

    def reformat(sql: str) -> str:
        reformatted = sql.replace(
            "CHECK (schema_version = 1)",
            "cHeCk\n( (( schema_version = 1 )) )",
            1,
        )
        return reformatted.replace("CHECK (", "ChEcK\n(")

    _rewrite_table_schema_sql(repository, "memory_events", reformat)
    repository.migrate_schema()
    repository.initialize()


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
def test_exact_frozen_schema_remains_valid_for_both_scopes(
    isolated_agentpro_storage, scope: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-exact-positive",
    )
    repository.initialize()
    repository.migrate_schema()
    repository.initialize()


def _base_schema_names(scope: str) -> tuple[str, str, str]:
    if scope == "PROJECT":
        return "project_metadata", "project_identity", "PROJECT"
    return "series_metadata", "series_identity", "SERIES"


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize(
    "damage",
    ["block", "line_lf", "line_crlf", "line_cr", "quoted_default",
     "literal_case", "literal_space"],
)
def test_b3_c1_base_check_validation_preserves_sqlite_semantics(
    isolated_agentpro_storage, scope: str, damage: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-c1-" + damage,
    )
    repository.initialize()
    required = f"CHECK (scope_type = '{scope}')"
    replacement = {
        "block": "/* " + required + " */",
        "line_lf": "-- " + required + "\n",
        "line_crlf": "-- " + required + "\r\n",
        "line_cr": "-- neutral\r " + required + "\n",
        "quoted_default": 'DEFAULT "' + required + '"',
        "literal_case": f"CHECK (scope_type = '{scope.lower()}')",
        "literal_space": f"CHECK (scope_type = ' {scope}')",
    }[damage]
    _rewrite_table_schema_sql(
        repository, "edges", lambda sql: sql.replace(required, replacement, 1),
    )
    _assert_public_schema_validation_rejects(
        repository, match="CHECK constraints|column definition",
    )


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
def test_b3_c1_base_check_accepts_semantically_equivalent_quoted_expression(
    isolated_agentpro_storage, scope: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-c1-positive-quoted",
    )
    repository.initialize()
    required = f"CHECK (scope_type = '{scope}')"
    _rewrite_table_schema_sql(
        repository,
        "edges",
        lambda sql: sql.replace(
            required,
            f"cHeCk (/* neutral 'it''s' comment */ (( \"scope_type\" = '{scope}' )))",
            1,
        ),
    )
    repository.migrate_schema()
    repository.initialize()


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize("damage", ["generated_column", "extra_column", "default_value"])
def test_b3_c2_complete_column_structure_is_required(
    isolated_agentpro_storage, scope: str, damage: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-c2-" + damage,
    )
    repository.initialize()
    metadata, _identity, _literal = _base_schema_names(scope)
    if damage == "generated_column":
        def add_hidden(sql: str) -> str:
            close = sql.rfind(")")
            return (
                sql[:close]
                + ", audit_hidden TEXT GENERATED ALWAYS AS ('neutral') VIRTUAL"
                + sql[close:]
            )
        _rewrite_table_schema_sql(repository, "memory_events", add_hidden)
    elif damage == "extra_column":
        _rewrite_table_schema_sql(
            repository,
            metadata,
            lambda sql: sql[:sql.rfind(")")] + ", audit_extra TEXT" + sql[sql.rfind(")"):],
        )
    else:
        _rewrite_table_schema_sql(
            repository,
            metadata,
            lambda sql: sql.replace(
                "value TEXT NOT NULL", "value TEXT NOT NULL DEFAULT 'neutral'", 1,
            ),
        )
    _assert_public_schema_validation_rejects(repository, match="column definition")


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize("damage", ["scope_collation", "unique_collation", "index_desc", "index_collation"])
def test_b3_c3_collation_and_complete_index_semantics_are_required(
    isolated_agentpro_storage, scope: str, damage: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-c3-" + damage,
    )
    repository.initialize()
    if damage == "scope_collation":
        _rewrite_table_schema_sql(
            repository,
            "memory_events",
            lambda sql: sql.replace(
                "scope_type TEXT NOT NULL", "scope_type TEXT COLLATE NOCASE NOT NULL", 1,
            ),
        )
    elif damage == "unique_collation":
        _rewrite_table_schema_sql(
            repository,
            "memory_events",
            lambda sql: sql.replace(
                "event_key_json TEXT NOT NULL UNIQUE",
                "event_key_json TEXT COLLATE NOCASE NOT NULL UNIQUE",
                1,
            ),
        )
    else:
        first_key = "scope_type DESC" if damage == "index_desc" else "scope_type COLLATE NOCASE"
        with sqlite3.connect(repository.db_path) as connection:
            connection.execute("DROP INDEX memory_events_operation_lookup")
            connection.execute(
                "CREATE INDEX memory_events_operation_lookup ON memory_events("
                + first_key
                + ",scope_id,operation_namespace,operation_id,sequence)"
            )
    _assert_public_schema_validation_rejects(repository, match="index|modifiers")


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize("damage", ["not_null_ignore", "primary_key_replace"])
def test_b3_c4_nondefault_conflict_policies_are_rejected(
    isolated_agentpro_storage, scope: str, damage: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-c4-" + damage,
    )
    repository.initialize()
    metadata, _identity, _literal = _base_schema_names(scope)
    if damage == "not_null_ignore":
        old, new = "value TEXT NOT NULL", "value TEXT NOT NULL ON CONFLICT IGNORE"
    else:
        old, new = "key TEXT PRIMARY KEY", "key TEXT PRIMARY KEY ON CONFLICT REPLACE"
    _rewrite_table_schema_sql(
        repository, metadata, lambda sql: sql.replace(old, new, 1),
    )
    _assert_public_schema_validation_rejects(repository, match="modifiers")


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
def test_b3_c4_frozen_conflict_behavior_still_aborts_and_preserves_original(
    isolated_agentpro_storage, scope: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-c4-positive-abort",
    )
    repository.initialize()
    metadata, _identity, _literal = _base_schema_names(scope)
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(
            f"INSERT INTO {metadata}(key,value) VALUES('c4-neutral-key','original')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO {metadata}(key,value) VALUES('c4-neutral-key','replacement')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO {metadata}(key,value) VALUES('c4-null-key',NULL)"
            )
        assert connection.execute(
            f"SELECT value FROM {metadata} WHERE key='c4-neutral-key'"
        ).fetchone()[0] == "original"


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
def test_b3_c5_tables_with_expected_empty_fk_sets_reject_extra_fk(
    isolated_agentpro_storage, scope: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-c5-extra-fk",
    )
    repository.initialize()
    metadata, _identity, _literal = _base_schema_names(scope)
    _rewrite_table_schema_sql(
        repository,
        "memory_events",
        lambda sql: sql.replace(
            "operation_id TEXT NOT NULL",
            f"operation_id TEXT NOT NULL REFERENCES {metadata}(key)",
            1,
        ),
    )
    _assert_public_schema_validation_rejects(
        repository, match="foreign key|SQL constraints",
    )


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize("damage", ["missing", "wrong_parent", "wrong_action"])
def test_b3_c5_required_foreign_key_set_is_exact(
    isolated_agentpro_storage, scope: str, damage: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-c5-" + damage,
    )
    repository.initialize()
    metadata, _identity, _literal = _base_schema_names(scope)
    required = "REFERENCES memory_events(sequence) ON DELETE RESTRICT"
    replacement = {
        "missing": "",
        "wrong_parent": f"REFERENCES {metadata}(key) ON DELETE RESTRICT",
        "wrong_action": "REFERENCES memory_events(sequence) ON DELETE CASCADE",
    }[damage]
    _rewrite_table_schema_sql(
        repository,
        "memory_event_entities",
        lambda sql: sql.replace(required, replacement, 1),
    )
    _assert_public_schema_validation_rejects(
        repository, match="foreign key|SQL constraints",
    )


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize("name", ["sqliteXaudit", "sqliteAneutral", "SQLITEXaudit"])
def test_b3_c6_sqlite_like_user_tables_are_not_treated_as_internal(
    isolated_agentpro_storage, scope: str, name: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-c6-" + name.lower(),
    )
    repository.initialize()
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(f'CREATE TABLE "{name}"(value TEXT)')
    _assert_public_schema_validation_rejects(repository, match="table set")


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
def test_b3_c6_real_sqlite_internal_table_remains_accepted(
    isolated_agentpro_storage, scope: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-c6-internal-positive",
    )
    repository.initialize()
    with sqlite3.connect(repository.db_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone() is not None
    repository.migrate_schema()
    repository.initialize()


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize("target", ["schema_version", "identity"])
@pytest.mark.parametrize("stored_kind", ["fractional", "non_numeric_text"])
def test_b3_c7_schema_versions_require_exact_integer_storage(
    isolated_agentpro_storage, scope: str, target: str, stored_kind: str,
) -> None:
    expected_version = 6 if scope == "PROJECT" else 4
    stored: object = (
        expected_version + 0.5
        if stored_kind == "fractional"
        else str(expected_version) + "x"
    )
    repository = _schema_repository(
        isolated_agentpro_storage,
        scope,
        scope.lower() + "-c7-" + target + "-" + stored_kind,
    )
    repository.initialize()
    _metadata, identity, _literal = _base_schema_names(scope)
    table = "schema_version" if target == "schema_version" else identity
    column = "version" if target == "schema_version" else "schema_version"
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(f"UPDATE {table} SET {column}=? WHERE id=1", (stored,))
    _assert_public_schema_validation_rejects(repository, match="version|identity")


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize("target", ["schema_version", "identity"])
def test_b3_c7_sqlite_integer_affinity_remains_compatible(
    isolated_agentpro_storage, scope: str, target: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-c7-affinity",
    )
    repository.initialize()
    expected_version = 6 if scope == "PROJECT" else 4
    _metadata, identity, _literal = _base_schema_names(scope)
    table = "schema_version" if target == "schema_version" else identity
    column = "version" if target == "schema_version" else "schema_version"
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(
            f"UPDATE {table} SET {column}=? WHERE id=1", (str(expected_version),)
        )
        stored = connection.execute(
            f"SELECT {column},typeof({column}) FROM {table} WHERE id=1"
        ).fetchone()
        assert stored == (expected_version, "integer")
    repository.migrate_schema()
    repository.initialize()


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize("target", ["schema_version", "identity"])
@pytest.mark.parametrize("damage", ["extra", "missing", "wrong_id", "wrong_content"])
def test_b3_c8_target_validation_requires_exact_singleton_state(
    isolated_agentpro_storage, scope: str, target: str, damage: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope,
        scope.lower() + "-c8-" + target + "-" + damage,
    )
    repository.initialize()
    _metadata, identity, _literal = _base_schema_names(scope)
    table = "schema_version" if target == "schema_version" else identity
    with sqlite3.connect(repository.db_path) as connection:
        columns = tuple(
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
        )
        values = list(connection.execute(f"SELECT * FROM {table} WHERE id=1").fetchone())
        if damage == "extra":
            connection.execute("PRAGMA ignore_check_constraints=ON")
            values[0] = 2
            connection.execute(
                f"INSERT INTO {table}({','.join(columns)}) VALUES("
                + ",".join("?" for _ in columns)
                + ")",
                values,
            )
            connection.execute("PRAGMA ignore_check_constraints=OFF")
        elif damage == "missing":
            connection.execute(f"DELETE FROM {table} WHERE id=1")
        elif damage == "wrong_id":
            connection.execute(f"DELETE FROM {table} WHERE id=1")
            connection.execute("PRAGMA ignore_check_constraints=ON")
            values[0] = 2
            connection.execute(
                f"INSERT INTO {table}({','.join(columns)}) VALUES("
                + ",".join("?" for _ in columns)
                + ")",
                values,
            )
            connection.execute("PRAGMA ignore_check_constraints=OFF")
        elif target == "schema_version":
            connection.execute("UPDATE schema_version SET version=version+1 WHERE id=1")
        else:
            identity_column = "project_id" if scope == "PROJECT" else "series_id"
            connection.execute(
                f"UPDATE {table} SET {identity_column}='' WHERE id=1"
            )
    _assert_public_schema_validation_rejects(
        repository,
        match="cardinality|identity|schema_version|newer than supported",
    )


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize(
    "damage", ["comment", "generated", "collation", "conflict", "user_object", "fractional_version", "extra_identity"],
)
def test_b3_root_causes_are_rejected_by_real_memory_ledger_upgrade(
    isolated_agentpro_storage, scope: str, damage: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-upgrade-" + damage,
    )
    repository.initialize()
    metadata, identity, literal = _base_schema_names(scope)
    if scope == "PROJECT":
        _drop_to_project_v5(repository)
        entry_version = 5
    else:
        _drop_to_series_v3(repository)
        entry_version = 3

    if damage == "comment":
        required = f"CHECK (scope_type = '{literal}')"
        _rewrite_table_schema_sql(
            repository, "edges", lambda sql: sql.replace(required, f"/* {required} */", 1),
        )
    elif damage == "generated":
        _rewrite_table_schema_sql(
            repository,
            metadata,
            lambda sql: sql.replace(
                "(", "(audit_hidden TEXT GENERATED ALWAYS AS ('neutral') VIRTUAL,", 1,
            ),
        )
    elif damage == "collation":
        _rewrite_table_schema_sql(
            repository,
            metadata,
            lambda sql: sql.replace("key TEXT PRIMARY KEY", "key TEXT PRIMARY KEY COLLATE NOCASE", 1),
        )
    elif damage == "conflict":
        _rewrite_table_schema_sql(
            repository,
            metadata,
            lambda sql: sql.replace(
                "value TEXT NOT NULL", "value TEXT NOT NULL ON CONFLICT IGNORE", 1,
            ),
        )
    with sqlite3.connect(repository.db_path) as connection:
        if damage == "user_object":
            connection.execute("CREATE TABLE sqliteXupgrade(value TEXT)")
        elif damage == "fractional_version":
            connection.execute(
                "UPDATE schema_version SET version=? WHERE id=1", (entry_version + 0.5,)
            )
        elif damage == "extra_identity":
            connection.execute("PRAGMA ignore_check_constraints=ON")
            columns = tuple(
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({identity})")
            )
            values = list(connection.execute(f"SELECT * FROM {identity} WHERE id=1").fetchone())
            values[0] = 2
            connection.execute(
                f"INSERT INTO {identity}({','.join(columns)}) VALUES("
                + ",".join("?" for _ in columns)
                + ")",
                values,
            )
            connection.execute("PRAGMA ignore_check_constraints=OFF")
    with pytest.raises((SchemaMigrationError, MemoryLedgerIntegrityError, sqlite3.Error)):
        repository.migrate_schema(
            backup_path=isolated_agentpro_storage / (scope.lower() + "-" + damage + ".backup"),
            maintenance_confirmed=True,
            release_head="TEST-HEAD",
        )


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
def test_b3_positive_schema_matrix_accepts_formatting_quoting_migration_and_reopen(
    isolated_agentpro_storage, scope: str,
) -> None:
    repository = _schema_repository(
        isolated_agentpro_storage, scope, scope.lower() + "-positive-c1-c8",
    )
    repository.initialize()
    required = f"CHECK (scope_type = '{scope}')"
    _rewrite_table_schema_sql(
        repository,
        "edges",
        lambda sql: sql.replace(
            required,
            f"cHeCk /* neutral formatting */ (( \"scope_type\" = '{scope}' ))",
            1,
        ),
    )
    index_name = "edges_source" if scope == "PROJECT" else "series_edges_source"
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(f'DROP INDEX "{index_name}"')
        connection.execute(
            f'CREATE INDEX "{index_name}" ON "edges"('
            '"scope_type","scope_id","source_id","edge_id")'
        )
    repository.migrate_schema()
    repository.initialize()

    if scope == "PROJECT":
        _drop_to_project_v5(repository)
    else:
        _drop_to_series_v3(repository)
    repository.migrate_schema(
        backup_path=isolated_agentpro_storage / (scope.lower() + "-positive.backup"),
        maintenance_confirmed=True,
        release_head="TEST-HEAD",
    )
    type(repository)(repository.context).initialize()


def test_incompatible_target_schema_version_fails_closed(isolated_agentpro_storage) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-schema-newer", book_id="BOOK-ledger-schema-newer"
    ))
    repository.initialize()
    with repository.connect() as connection:
        connection.execute("UPDATE schema_version SET version=99 WHERE id=1")
        connection.execute("UPDATE project_identity SET schema_version=99 WHERE id=1")
    with pytest.raises((SchemaMigrationError, ValueError), match="newer"):
        repository.initialize()


def test_partial_existing_store_is_not_silently_normalized(isolated_agentpro_storage) -> None:
    context = StorageResolver().resolve_project(
        "PROJ-ledger-partial-store", book_id="BOOK-ledger-partial-store"
    )
    context.project_root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(context.project_db_path) as connection:
        connection.execute(
            "CREATE TABLE schema_version(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_version(id,version) VALUES(1,?)", (PROJECT_DB_SCHEMA_VERSION,)
        )
    repository = ProjectRepository(context)
    with pytest.raises((SchemaMigrationError, ValueError, sqlite3.Error)):
        repository.initialize()
    with sqlite3.connect(context.project_db_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_identity'"
        ).fetchone() is None


def test_target_migration_rejects_repository_identity_swap(isolated_agentpro_storage) -> None:
    owner = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-identity-owner", book_id="BOOK-ledger-identity-owner"
    ))
    owner.initialize()
    context = owner.context
    foreign_context = type(context)(
        project_id="PROJ-ledger-identity-foreign",
        book_id="BOOK-ledger-identity-foreign",
        storage_root=context.storage_root,
        projects_root=context.projects_root,
        project_root=context.project_root,
        book_root=context.book_root,
        project_db_path=context.project_db_path,
    )
    with pytest.raises(SchemaMigrationError, match="identity does not match"):
        ProjectRepository(foreign_context).migrate_schema()


def _prepare_bootstrap_repository(storage_root, project_id: str, book_id: str,
                                  rows: tuple[tuple[str, str], ...]):
    repository = ProjectRepository(
        StorageResolver(storage_root).resolve_project(project_id, book_id=book_id)
    )
    repository.initialize()
    with repository.connect() as connection:
        for record_id, payload in rows:
            connection.execute(
                "INSERT INTO project_structured_memory_records"
                "(scope_type,scope_id,record_type,record_id,payload_json) VALUES('PROJECT',?,'FACT',?,?)",
                (project_id, record_id, payload),
            )
    _drop_to_project_v5(repository)
    return repository


def _pipeline_source(operation_id: str) -> dict:
    key = "canonical_pipeline.v1:" + operation_id
    text = "Neutral synthetic source for closed bootstrap validation."
    return {
        "text": text,
        "artifact_ref": "project_metadata:" + key + "#source",
        "artifact_id": "artifact-" + operation_id,
        "version": 1,
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "scene_id": "SCENE-" + operation_id,
        "accepted_step_artifact": "accepted-step-artifact-neutral",
        "context_package_id": "CTX-neutral-bootstrap",
        "context_hash": H,
    }


def _migrated_metadata_repository(
    storage_root, *, suffix: str, key: str, payload: object,
) -> ProjectRepository:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-bootstrap-" + suffix,
        book_id="BOOK-ledger-bootstrap-" + suffix,
    ))
    repository.initialize()
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO project_metadata(key,value) VALUES(?,?)",
            (key, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
        )
    _drop_to_project_v5(repository)
    _migrate(repository, storage_root / (suffix + ".backup"))
    return repository


def test_bootstrap_uses_stable_composite_keys_and_rejects_incomplete_pending(
    isolated_agentpro_storage,
) -> None:
    logical_rows = (("FACT-a", '{"value":"a"}'), ("FACT-b", '{"value":"b"}'))
    manifests = []
    locators = []
    for suffix, rows in (("ab", logical_rows), ("ba", tuple(reversed(logical_rows)))):
        repository = _prepare_bootstrap_repository(
            isolated_agentpro_storage / ("storage-" + suffix),
            "PROJ-ledger-order", "BOOK-ledger-order", rows,
        )
        backup = isolated_agentpro_storage / f"order-{suffix}.backup"
        _migrate(repository, backup)
        activation = repository.bootstrap_memory_ledger(maintenance_confirmed=True)
        manifests.append(activation.structured_payload["manifest_hash"])
        events, _ = repository.list_memory_events(limit=200)
        locators.append(sorted(
            event.structured_payload["locator"] for event in events
            if event.structured_payload.get("category") == "PROJECT_STRUCTURED_MEMORY"
        ))
    assert all(":key:" in locator for group in locators for locator in group)
    assert manifests[0] == manifests[1]
    assert locators[0] == locators[1]
    assert all('"record_id":"FACT-a"' in group[0] for group in locators)
    assert all('"record_id":"FACT-b"' in group[1] for group in locators)

    refused = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-pending-refusal", book_id="BOOK-ledger-pending-refusal"
    ))
    refused.initialize()
    with refused.connect() as connection:
        state = {
            "schema_version": 1,
            "project_id": "PROJ-ledger-pending-refusal",
            "book_id": "BOOK-ledger-pending-refusal",
            "records": {}, "sources": {}, "claims": {}, "conflicts": {}, "decisions": {},
            "operations": {"research-op": {
            "status": "RUNNING", "attempt_id": "attempt-1",
            "request": {"run_id": "RUN-test", "step_id": "STEP-test", "action": "EXTRACT"},
        }}}
        connection.execute(
            "INSERT INTO project_metadata(key,value) VALUES('research.v1',?)",
            (json.dumps(state),),
        )
    _drop_to_project_v5(refused)
    _migrate(refused, isolated_agentpro_storage / "pending-refusal.backup")
    with pytest.raises(MemoryLedgerIntegrityError, match="research bootstrap|durable binding"):
        refused.bootstrap_memory_ledger(maintenance_confirmed=True)
    with refused.connect() as connection:
        control = json.loads(connection.execute(
            "SELECT value FROM project_metadata WHERE key='memory_ledger_control.v1'"
        ).fetchone()[0])
        assert control["state"] == "SCHEMA_READY"
        assert connection.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0] == 0


@pytest.mark.parametrize("key,payload", [
    (
        "canonical_pipeline.v1:operation-incomplete",
        {"started": True, "source": {"artifact_ref": "source"}, "attempts": []},
    ),
    (
        "canonical_proposal.v1:proposal-incomplete",
        {"current_version": "1", "versions": {}},
    ),
    (
        "cross_store_operation.v1:cross-store-incomplete",
        {"schema_version": 1, "operation_id": "cross-store-incomplete", "status": "PENDING"},
    ),
])
def test_bootstrap_refuses_incomplete_covered_operation_bindings(
    isolated_agentpro_storage, key: str, payload: dict,
) -> None:
    suffix = key.split(":", 1)[1].replace("_", "-")
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-preflight-" + suffix,
        book_id="BOOK-ledger-preflight-" + suffix,
    ))
    repository.initialize()
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO project_metadata(key,value) VALUES(?,?)",
            (key, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
        )
    _drop_to_project_v5(repository)
    _migrate(repository, isolated_agentpro_storage / (suffix + ".backup"))
    with pytest.raises(MemoryLedgerIntegrityError, match="bootstrap|canonical|cross.store"):
        repository.bootstrap_memory_ledger(maintenance_confirmed=True)
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0] == 0


@pytest.mark.parametrize(
    "kind",
    [
        "unknown_metadata",
        "malformed_canonical_versions",
        "unsupported_terminal_result",
        "incomplete_terminal_binding",
    ],
)
def test_bootstrap_closed_preflight_rejects_unprovable_metadata(
    isolated_agentpro_storage, kind: str,
) -> None:
    operation_id = "operation-" + kind.replace("_", "-")
    if kind == "unknown_metadata":
        key, payload = "unknown_metadata.v1:neutral", {}
    elif kind == "malformed_canonical_versions":
        key = "canonical_versions.v1:FACT:FACT-neutral"
        payload = [{"unexpected": "shape"}]
    else:
        key = "canonical_pipeline.v1:" + operation_id
        payload = {
            "started": True,
            "source": _pipeline_source(operation_id),
            "attempts": [],
            "result": (
                {"unexpected": "terminal-shape"}
                if kind == "unsupported_terminal_result"
                else {"status": "REJECT", "canonical_commit": False, "verification": {}}
            ),
        }
    repository = _migrated_metadata_repository(
        isolated_agentpro_storage,
        suffix=kind.replace("_", "-"),
        key=key,
        payload=payload,
    )
    with pytest.raises(MemoryLedgerIntegrityError, match="bootstrap|canonical|terminal"):
        repository.bootstrap_memory_ledger(maintenance_confirmed=True)
    with repository.connect() as connection:
        control = json.loads(connection.execute(
            "SELECT value FROM project_metadata WHERE key='memory_ledger_control.v1'"
        ).fetchone()[0])
        assert control["state"] == "SCHEMA_READY"
        assert connection.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0] == 0


def test_schema_ready_with_partial_ledger_state_fails_before_bootstrap_mutation(
    isolated_agentpro_storage,
) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-partial-bootstrap", book_id="BOOK-ledger-partial-bootstrap"
    ))
    repository.initialize()
    _drop_to_project_v5(repository)
    _migrate(repository, isolated_agentpro_storage / "partial-bootstrap.backup")
    with repository.connect() as connection:
        event = build_memory_event(
            sequence=next_sequence(connection),
            scope_type="PROJECT",
            scope_id=repository.context.project_id,
            operation={"namespace": "LEDGER_BOOTSTRAP", "id": "bootstrap-v1:PROJECT"},
            event_slot=(
                "object", "PROJECT_FACT",
                'table:project_fact_records:key:{"fact_id":"FACT-neutral"}',
            ),
            event_type="LEDGER_BASELINE_OBJECT",
            actor={
                "kind": "SYSTEM", "id": "MEMORY_LEDGER_BOOTSTRAP_V1",
                "evidence_ref": None,
            },
            project_id=repository.context.project_id,
            book_id=repository.context.book_id,
            series_id=None,
            structured_payload={
                "category": "PROJECT_FACT",
                "locator": 'table:project_fact_records:key:{"fact_id":"FACT-neutral"}',
                "bytes_hash": H,
                "snapshot_text": "{}",
                "observed_schema_version": 5,
                "history_completeness": "UNKNOWN_BEFORE_BOUNDARY",
            },
            timestamp="2026-09-23T12:00:00.000000Z",
        )
        append_memory_event(connection, event)
    with pytest.raises(SchemaMigrationError, match="SCHEMA_READY|control"):
        repository.initialize()
    with repository.connect() as connection:
        control = json.loads(connection.execute(
            "SELECT value FROM project_metadata WHERE key='memory_ledger_control.v1'"
        ).fetchone()[0])
        assert control["state"] == "SCHEMA_READY"
        assert connection.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0] == 1


def test_series_bootstrap_refuses_inconsistent_membership_binding(
    isolated_agentpro_storage,
) -> None:
    repository = SeriesRepository(
        StorageResolver().resolve_series("SERIES-ledger-membership-refusal")
    )
    repository.initialize()
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO series_memberships(project_id,book_id,payload_json) VALUES(?,?,?)",
            (
                "PROJ-ledger-member", "BOOK-ledger-member",
                json.dumps({
                    "series_id": "SERIES-foreign", "project_id": "PROJ-ledger-member",
                    "book_id": "BOOK-ledger-member", "source_ref": "synthetic",
                    "version": 1, "created_at": "2026-09-23T12:00:00Z",
                }, sort_keys=True, separators=(",", ":")),
            ),
        )
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'memory_event%'"
        ).fetchall():
            connection.execute(f"DROP TRIGGER {row[0]}")
        connection.execute("DROP TABLE memory_event_entities")
        connection.execute("DROP TABLE memory_events")
        connection.execute("DELETE FROM series_metadata WHERE key='memory_ledger_control.v1'")
        connection.execute("UPDATE schema_version SET version=3 WHERE id=1")
        connection.execute("UPDATE series_identity SET schema_version=3 WHERE id=1")
    repository.migrate_schema(
        backup_path=isolated_agentpro_storage / "membership-refusal.backup",
        maintenance_confirmed=True,
        release_head="TEST-HEAD",
    )
    with pytest.raises(MemoryLedgerIntegrityError, match="membership binding"):
        repository._bootstrap_memory_ledger_internal(maintenance_confirmed=True)
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0] == 0


def test_bootstrap_accepts_real_producer_pipeline_bindings(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    from fastapi.testclient import TestClient
    from app.main import app
    from app.p20_core.book_bible_test_helper import ensure_test_book_bible
    from app.p20_core.project_repository import ensure_system_repository
    from tests.test_memory_transport_integration import (
        BOOK,
        PROJECT,
        SERIES,
        _install_sdk_boundary,
        _step,
        _use_production_provider,
    )
    from tests.test_p20_runtime_context_integration import _register_series_member

    ensure_test_book_bible(BOOK)
    ensure_system_repository().bind_project(PROJECT, BOOK)
    repository = ProjectRepository(StorageResolver().resolve_project(PROJECT, book_id=BOOK))
    repository.initialize()
    _use_production_provider(monkeypatch)
    _install_sdk_boundary(monkeypatch)
    _register_series_member(PROJECT, BOOK, SERIES)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 51951)) as client:
        result = _step(
            client,
            run="run-ledger-bootstrap-real-producer",
            step_id="step-ledger-bootstrap-real-producer",
            series_id=SERIES,
        )
    assert result["canonical_change"]["canonical_commit"] is True

    _drop_to_project_v5(repository)
    _migrate(repository, isolated_agentpro_storage / "real-producer-bootstrap.backup")
    activation = repository.bootstrap_memory_ledger(maintenance_confirmed=True)
    assert activation.event_type == "LEDGER_ACTIVATED"
    assert activation.structured_payload["baseline_count"] > 0


@pytest.mark.parametrize(
    "damage",
    [None, "missing_identity", "incompatible_identity", "missing_membership"],
)
def test_series_bootstrap_uses_registered_initiating_identity(
    isolated_agentpro_storage, monkeypatch, damage: str | None,
) -> None:
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from openai.resources.responses.responses import Responses

    import app.tools as runtime_tools
    from app.main import app
    from app.p20_core.book_bible_test_helper import ensure_test_book_bible
    from app.p20_core.domain_records import FactRecord
    from app.p20_core.project_repository import SeriesAccessContext, ensure_system_repository
    from app.p20_core.series_memory import SeriesMembershipRecord

    suffix = "pass" if damage is None else damage.replace("_", "-")
    project_id = "PROJ-ledger-series-bootstrap-" + suffix
    book_id = "BOOK-ledger-series-bootstrap-" + suffix
    series_id = "SERIES-ledger-series-bootstrap-" + suffix
    ensure_test_book_bible(book_id)
    ensure_system_repository().bind_project(project_id, book_id)
    access = SeriesAccessContext.bind(project_id, series_id)
    repository = SeriesRepository(StorageResolver().resolve_series(series_id))
    repository.register_member(access, SeriesMembershipRecord(
        series_id=series_id,
        project_id=project_id,
        book_id=book_id,
        source_ref="synthetic/series-membership",
        version=1,
        created_at="2026-09-23T12:00:00Z",
    ))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-live")

    def create(_self, **kwargs):
        prompt = json.loads(kwargs["input"])
        task = next(item for item in prompt["context_package"]["included_items"]
                    if item["layer"] == "TASK")
        payload = json.loads(task["content"])["input"]
        if payload["role"] == "EXTRACTOR":
            source = payload["source"]
            fact = FactRecord(
                fact_id="FACT-ledger-series-bootstrap-" + suffix,
                project_id=project_id,
                subject_id="FACT-ledger-series-bootstrap-" + suffix,
                predicate="description",
                object_type="TEXT",
                object_id=None,
                object_value="Neutral synthetic series fact",
                reality_status="TRUE",
                verification_status="VERIFIED",
                confidence=1,
                frozen=False,
                author_locked=False,
                valid_from=None,
                valid_to=None,
                established_event_id=None,
                established_scene_id=source["scene_id"],
                source_artifact_ref=source["artifact_ref"],
                source_refs=(source["scene_id"],),
                canon_version=1,
                version=1,
                created_at="2026-09-23T12:00:00Z",
                updated_at="2026-09-23T12:00:00Z",
            )
            output = {"records": [{"record_type": "FACT", "payload": fact.to_dict()}]}
        else:
            output = {"precision_status": "ACCEPT", "completeness_status": "ACCEPT"}
        return SimpleNamespace(output_text=json.dumps(output), model=kwargs["model"], output=[])

    monkeypatch.setattr(Responses, "create", create)
    monkeypatch.setitem(runtime_tools.TOOLS, "WRITE", lambda payload: {
        "tool": "WRITE", "payload": {"text": "Neutral synthetic series text."},
    })
    body = {
        "mode": "WRITE", "project_id": project_id, "book_id": book_id,
        "series_id": series_id, "run_id": "RUN-ledger-series-bootstrap-" + suffix,
        "step_id": "STEP-ledger-series-bootstrap-" + suffix,
        "payload": {"text": "Write a neutral series observation.", "scope_type": "SERIES"},
    }
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 51961)) as client:
        response = client.post("/agent/step", json=body)
        assert response.status_code == 200, response.text
        change = response.json()["canonical_change"]
    assert change["status"] == "COMMITTED"

    if damage is not None:
        with repository.connect() as connection:
            if damage == "missing_membership":
                connection.execute("DELETE FROM series_memberships WHERE project_id=?", (project_id,))
            else:
                key = "canonical_proposal.v1:" + change["proposal_id"]
                document = json.loads(connection.execute(
                    "SELECT value FROM series_metadata WHERE key=?", (key,)
                ).fetchone()[0])
                proposal = document["versions"][document["current_version"]]["proposal"]
                if damage == "missing_identity":
                    proposal["project_id"] = None
                else:
                    proposal["project_id"] = "PROJ-unrelated-series-bootstrap"
                connection.execute(
                    "UPDATE series_metadata SET value=? WHERE key=?",
                    (json.dumps(document, sort_keys=True, separators=(",", ":")), key),
                )

    _drop_to_series_v3(repository)
    backup = isolated_agentpro_storage / ("series-bootstrap-" + suffix + ".backup")
    repository.migrate_schema(
        backup_path=backup,
        maintenance_confirmed=True,
        release_head="TEST-HEAD",
    )
    if damage is None:
        activation = repository._bootstrap_memory_ledger_internal(maintenance_confirmed=True)
        assert activation.event_type == "LEDGER_ACTIVATED"
        reopened = SeriesRepository(repository.context)
        events, _ = reopened.list_memory_events(access, limit=200)
        assert events[-1].memory_event_id == activation.memory_event_id
        assert any(event.event_type == "LEDGER_BASELINE_OBJECT" for event in events)
    else:
        with pytest.raises(MemoryLedgerIntegrityError):
            repository._bootstrap_memory_ledger_internal(maintenance_confirmed=True)
        with repository.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0] == 0


def test_bootstrap_accepts_legitimate_multiversion_research_producer_state(
    isolated_agentpro_storage, tmp_path, monkeypatch,
) -> None:
    from types import SimpleNamespace
    from openai.resources.responses.responses import Responses
    from app.p20_core import research
    from app.p20_core.book_bible_test_helper import ensure_test_book_bible
    from app.p20_core.canon_service import prepare_research_proposal
    from app.p20_core.context_runtime import ProjectExecutionContext
    from app.p20_core.project_repository import ensure_system_repository
    from tests.test_memory_transport_integration import _use_production_provider

    project_id = "PROJ-research-bootstrap-history"
    book_id = "BOOK-research-bootstrap-history"
    research_id = "RESEARCH-bootstrap-history"
    source_id = "SOURCE-bootstrap-history"
    ensure_test_book_bible(book_id)
    ensure_system_repository().bind_project(project_id, book_id)
    repository = ProjectRepository(StorageResolver().resolve_project(
        project_id, book_id=book_id,
    ))
    repository.initialize()
    _use_production_provider(monkeypatch)

    def task_input(package: dict) -> dict:
        item = next(value for value in package["included_items"] if value["layer"] == "TASK")
        return json.loads(item["content"])["input"]

    def sdk_boundary(self, **kwargs):
        prompt = json.loads(kwargs["input"])
        assert prompt["protocol"] == "AGENTPRO_RESEARCH_V1"
        evidence = task_input(prompt["context_package"])["evidence"]
        source = evidence["sources"][0]
        citation = {
            "source_id": source["source_id"], "version": source["version"],
            "content_hash": source["content_hash"], "start": 0,
            "end": len(source["content"]), "quote": source["content"],
        }
        if prompt["phase"] == "EXTRACT":
            output = {"claims": [{"claim": source["content"], "source_refs": [citation]}]}
        else:
            output = {"evaluations": [{
                "claim_id": claim["claim_id"], "status": "CONFIRMED",
                "confidence": 0.9, "reason": "Neutral source supports the assertion.",
                "evidence": [citation], "contradiction": None,
            } for claim in evidence["claims"]]}
        return SimpleNamespace(output_text=json.dumps(output), model=kwargs["model"], output=[])

    monkeypatch.setattr(Responses, "create", sdk_boundary)
    research.create_research(
        repository, operation_id="question-bootstrap", research_id=research_id,
        question="When does the synthetic station open?", purpose="Neutral bootstrap proof",
        requested_by="operator-test",
    )
    research.import_source(
        repository, operation_id="source-bootstrap-v1", source_id=source_id,
        version=1, source_type="USER_NOTE", title="Neutral source version one",
        content="The synthetic station opens at 08:00.",
    )
    execution = ProjectExecutionContext.create(
        project_id=project_id, book_id=book_id, series_id=None,
        run_id="RUN-research-bootstrap", step_id="STEP-research-bootstrap",
    )

    def research_version(suffix: str, version: int) -> tuple[dict, dict]:
        extracted = research.run_research(
            repository, execution=execution, operation_id="extract-" + suffix,
            research_id=research_id, action="EXTRACT",
            source_refs=[{"source_id": source_id, "version": version}],
            effective_model="gpt-memory-effective", requested_model="gpt-memory-requested",
        )
        verified = research.run_research(
            repository, execution=execution, operation_id="verify-" + suffix,
            research_id=research_id, action="VERIFY",
            claim_ids=[item["claim_id"] for item in extracted["claims"]],
            effective_model="gpt-memory-effective", requested_model="gpt-memory-requested",
        )
        proposal = prepare_research_proposal(
            repository, proposal_id="proposal-bootstrap-history",
            operation_id="verify-" + suffix,
            target_fact_ids={
                item["claim_id"]: "FACT-bootstrap-" + suffix
                for item in verified["claims"]
            },
        )
        return verified, proposal

    _, first = research_version("v1", 1)
    research.import_source(
        repository, operation_id="source-bootstrap-v2", source_id=source_id,
        version=2, source_type="USER_NOTE", title="Neutral source version two",
        content="The synthetic station opens at 10:00.",
    )
    _, second = research_version("v2", 2)
    assert first["proposal_version"] == 1
    assert second["proposal_version"] == 2
    history = json.loads(repository.get_metadata(
        "canonical_proposal.v1:proposal-bootstrap-history"
    ))
    assert history["versions"]["1"]["proposal"]["status"] == "STALE"
    assert history["versions"]["2"]["proposal"]["status"] == "AWAITING_USER_APPROVAL"
    state = repository.read_research_state()
    validator = repository_module._validate_research_proposal_bootstrap_binding
    current = history["versions"]["2"]
    for tamper in ("evidence_hash", "request_hash", "operation_id"):
        damaged = deepcopy(current)
        if tamper == "evidence_hash":
            damaged["proposal"]["research_evidence"]["hash"] = H
        elif tamper == "request_hash":
            damaged["research_request_hash"] = H
        else:
            damaged["proposal"]["research_evidence"]["operation_id"] = "verify-missing"
        with pytest.raises(MemoryLedgerIntegrityError, match="research proposal"):
            validator(
                proposal=damaged["proposal"], record=damaged, research_state=state,
            )

    _drop_to_project_v5(repository)
    _migrate(repository, isolated_agentpro_storage / "research-history-bootstrap.backup")
    activation = repository.bootstrap_memory_ledger(maintenance_confirmed=True)
    assert activation.event_type == "LEDGER_ACTIVATED"
    assert activation.structured_payload["baseline_count"] > 0

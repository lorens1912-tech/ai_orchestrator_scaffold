from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

import app.p20_core.project_repository as repository_module
from app.p20_core.memory_ledger import (
    MemoryLedgerError,
    MemoryLedgerIdentityConflict,
    MemoryLedgerIntegrityError,
    MemoryLedgerMigrationRequired,
    MemoryLedgerNotActive,
    build_memory_event,
    create_memory_ledger_schema,
)
from app.p20_core.cross_store_recovery import (
    CrossStoreOperationPlan,
    CrossStoreRecoveryService,
)
from app.p20_core.project_repository import (
    PROJECT_DB_MIGRATIONS,
    ProjectRepository,
    ProjectStorageError,
    StorageResolver,
)
from app.p20_core.research import create_research


def _repository(project_id: str) -> ProjectRepository:
    repository = ProjectRepository(
        StorageResolver().resolve_project(project_id, book_id="BOOK-neutral-phase4")
    )
    repository.initialize()
    return repository


def _append(
    repository: ProjectRepository,
    *,
    operation_id: str,
    slot: str,
    entity_id: str | None = None,
    parent_refs=(),
    event_type: str = "EXTRACTION_ATTEMPT_RECORDED",
    payload: dict | None = None,
):
    entity_refs = () if entity_id is None else (
        {"record_type": "FACT", "entity_id": entity_id, "version": 1},
    )
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        return repository.record_memory_event(
            connection,
            operation_namespace=(
                "LEDGER_CORRECTION"
                if event_type == "MEMORY_EVENT_CORRECTION"
                else "CANONICAL_PIPELINE"
            ),
            operation_id=operation_id,
            event_slot=(("correction",) if event_type == "MEMORY_EVENT_CORRECTION"
                        else ("attempt", slot)),
            event_type=event_type,
            actor=(
                {"kind": "OPERATOR", "id": "operator-phase4", "evidence_ref": "repair-ticket-phase4"}
                if event_type == "MEMORY_EVENT_CORRECTION"
                else {"kind": "SYSTEM", "id": "PHASE4_TEST", "evidence_ref": None}
            ),
            entity_refs=entity_refs,
            parent_refs=parent_refs,
            structured_payload=(
                {
                    "phase": "VERIFIED_ATTEMPT",
                    "attempt_ordinal": int(slot),
                    "source_refs": ["synthetic:source"],
                    "candidate_refs": ["synthetic:candidate"],
                    "verification_refs": ["synthetic:verification"],
                    "invocation_refs": ["synthetic:invocation"],
                    "outcome": "ACCEPT",
                }
                if payload is None else payload
            ),
        )


def _drop_project_ledger(repository: ProjectRepository) -> None:
    with repository.connect() as connection:
        for table in ("translation_request_receipts", "translation_heads", "translation_records",
                      "gap018_manuscript_head", "gap018_source_head", "gap018_records"):
            connection.execute(f"DROP TABLE {table}")
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'memory_event%'"
        ).fetchall():
            connection.execute(f"DROP TRIGGER {row[0]}")
        connection.execute("DROP TABLE memory_event_entities")
        connection.execute("DROP TABLE memory_events")
        connection.execute(
            "DELETE FROM project_metadata WHERE key='memory_ledger_control.v1'"
        )
        connection.execute("UPDATE schema_version SET version=5 WHERE id=1")
        connection.execute("UPDATE project_identity SET schema_version=5 WHERE id=1")


def _restore_project_triggers(connection: sqlite3.Connection) -> None:
    create_memory_ledger_schema(
        connection,
        scope_type="PROJECT",
        identity_table="project_identity",
        identity_column="project_id",
        replace_triggers=True,
    )


def test_cursor_freezes_snapshot_and_rejects_every_binding_change(
    isolated_agentpro_storage,
) -> None:
    repository = _repository("PROJ-ledger-phase4-cursor")
    first = _append(repository, operation_id="cursor-op", slot="1")
    second = _append(repository, operation_id="cursor-op", slot="2")
    page, cursor = repository.list_memory_events(limit=1)
    assert len(page) == 1 and cursor is not None
    frozen = json.loads(cursor)

    later = _append(repository, operation_id="cursor-op", slot="3")
    remainder, final_cursor = repository.list_memory_events(cursor=cursor, limit=200)
    assert final_cursor is None
    assert [event.memory_event_id for event in (*page, *remainder)] == [
        event.memory_event_id for event in repository.list_memory_events(limit=200)[0]
        if event.sequence <= frozen["high_watermark"]
    ]
    assert later.memory_event_id not in {event.memory_event_id for event in remainder}
    assert {first.memory_event_id, second.memory_event_id}.issubset(
        {event.memory_event_id for event in (*page, *remainder)}
    )

    mutations = (
        {"scope_id": "PROJ-forged"},
        {"activation_event_id": "MEV-" + "0" * 64},
        {"filter": {"operation": ["CANONICAL_PIPELINE", "other"], "entity": None}},
        {"after_sequence": -1},
        {"high_watermark": frozen["high_watermark"] + 1},
        {"high_watermark_content_hash": "0" * 64},
        {"unexpected": True},
    )
    for mutation in mutations:
        forged = dict(frozen)
        forged.update(mutation)
        with pytest.raises(ProjectStorageError):
            repository.list_memory_events(cursor=json.dumps(forged), limit=1)
    with pytest.raises(ProjectStorageError):
        repository.list_memory_events(
            cursor=cursor,
            operation=("CANONICAL_PIPELINE", "cursor-op"),
            limit=1,
        )
    duplicate_key = cursor[:-1] + ',"scope_id":"PROJ-duplicate"}'
    with pytest.raises(ProjectStorageError):
        repository.list_memory_events(cursor=duplicate_key, limit=1)


@pytest.mark.parametrize("damage", ["record_json", "content_hash", "scope", "entity_index"])
def test_read_fails_closed_for_corrupt_record_index_and_scope(
    isolated_agentpro_storage, damage: str,
) -> None:
    repository = _repository("PROJ-ledger-phase4-corrupt-" + damage)
    event = _append(
        repository,
        operation_id="corruption-op",
        slot="1",
        entity_id="FACT-neutral-corruption",
    )
    with repository.connect() as connection:
        if damage == "entity_index":
            connection.execute("DROP TRIGGER memory_event_entities_reject_delete")
            connection.execute(
                "DELETE FROM memory_event_entities WHERE sequence=?", (event.sequence,)
            )
        else:
            connection.execute("DROP TRIGGER memory_events_reject_update")
            if damage == "record_json":
                connection.execute(
                    "UPDATE memory_events SET record_json='not-json' WHERE sequence=?",
                    (event.sequence,),
                )
            elif damage == "content_hash":
                connection.execute(
                    "UPDATE memory_events SET content_hash=? WHERE sequence=?",
                    ("0" * 64, event.sequence),
                )
            else:
                connection.execute(
                    "UPDATE memory_events SET scope_id='PROJ-forged' WHERE sequence=?",
                    (event.sequence,),
                )
        _restore_project_triggers(connection)
    with pytest.raises(MemoryLedgerError):
        repository.get_memory_event(event.memory_event_id)
    with pytest.raises(MemoryLedgerError):
        repository.list_memory_events(limit=200)


def test_append_only_guards_hold_with_recursive_triggers_off_and_on(
    isolated_agentpro_storage,
) -> None:
    repository = _repository("PROJ-ledger-phase4-append-only")
    event = _append(
        repository,
        operation_id="append-only-op",
        slot="1",
        entity_id="FACT-neutral-append-only",
    )
    for recursive in ("OFF", "ON"):
        with repository.connect() as connection:
            connection.execute(f"PRAGMA recursive_triggers={recursive}")
            for statement, params in (
                ("UPDATE memory_events SET event_type=event_type WHERE sequence=?", (event.sequence,)),
                ("DELETE FROM memory_events WHERE sequence=?", (event.sequence,)),
                (
                    "INSERT OR REPLACE INTO memory_events "
                    "SELECT * FROM memory_events WHERE sequence=?",
                    (event.sequence,),
                ),
                (
                    "UPDATE memory_event_entities SET entity_id=entity_id WHERE sequence=?",
                    (event.sequence,),
                ),
                ("DELETE FROM memory_event_entities WHERE sequence=?", (event.sequence,)),
                (
                    "INSERT OR REPLACE INTO memory_event_entities "
                    "SELECT * FROM memory_event_entities WHERE sequence=?",
                    (event.sequence,),
                ),
            ):
                with pytest.raises(sqlite3.IntegrityError):
                    connection.execute(statement, params)
    assert repository.get_memory_event(event.memory_event_id) == event


def test_references_correction_and_sequence_history_are_preserved(
    isolated_agentpro_storage,
) -> None:
    repository = _repository("PROJ-ledger-phase4-references")
    target = _append(repository, operation_id="reference-op", slot="1")

    def ref(*, owner: str, locator: str, digest: str | None, kind="MEMORY_EVENT"):
        return {
            "kind": kind,
            "owner_scope_type": "PROJECT",
            "owner_scope_id": owner,
            "locator": locator,
            "version": None,
            "hash_scheme": None if digest is None else "SHA256",
            "hash": digest,
        }

    references = (
        ref(owner=repository.context.project_id, locator=target.memory_event_id, digest=target.content_hash),
        ref(owner=repository.context.project_id, locator=target.memory_event_id, digest="0" * 64),
        ref(owner=repository.context.project_id, locator="MEV-" + "1" * 64, digest="1" * 64),
        ref(owner="PROJ-foreign", locator=target.memory_event_id, digest=target.content_hash),
        ref(
            owner=repository.context.project_id,
            locator='table:neutral:key:{"id":"0"}',
            digest="2" * 64,
            kind="BASELINE_OBJECT",
        ),
    )
    referencing = _append(
        repository,
        operation_id="reference-op",
        slot="2",
        parent_refs=references,
    )
    assert sorted(repository.validate_memory_event_references(referencing)) == sorted((
        "MISSING", "VERIFIED", "HASH_MISMATCH", "NOT_AUTHORIZED",
        "LEGACY_UNVERIFIABLE",
    ))

    correction = _append(
        repository,
        operation_id="correction-op",
        slot="correction",
        event_type="MEMORY_EVENT_CORRECTION",
        parent_refs=(ref(
            owner=repository.context.project_id,
            locator=target.memory_event_id,
            digest=target.content_hash,
        ),),
        payload={
            "target_memory_event_id": target.memory_event_id,
            "target_content_hash": target.content_hash,
            "reason": "Synthetic correction proof",
            "corrected_description": "The immutable source remains unchanged.",
        },
    )
    assert correction.sequence > target.sequence
    assert repository.get_memory_event(target.memory_event_id) == target

    deleted = _append(repository, operation_id="sequence-op", slot="1")
    with repository.connect() as connection:
        connection.execute("DROP TRIGGER memory_events_reject_delete")
        connection.execute("DELETE FROM memory_events WHERE sequence=?", (deleted.sequence,))
        _restore_project_triggers(connection)
    replacement = _append(repository, operation_id="sequence-op", slot="2")
    assert replacement.sequence == deleted.sequence + 1


def test_history_read_never_creates_or_migrates_storage(isolated_agentpro_storage) -> None:
    absent = ProjectRepository(
        StorageResolver().resolve_project(
            "PROJ-ledger-phase4-absent", book_id="BOOK-neutral-phase4"
        )
    )
    assert not absent.db_path.exists()
    with pytest.raises(MemoryLedgerMigrationRequired):
        absent.get_memory_event("MEV-" + "0" * 64)
    assert not absent.db_path.exists()

    legacy = _repository("PROJ-ledger-phase4-read-p5")
    _drop_project_ledger(legacy)
    with legacy.connect() as connection:
        before = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        )
    with pytest.raises(MemoryLedgerMigrationRequired):
        legacy.list_memory_events(limit=1)
    with legacy.connect() as connection:
        after = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        )
        assert connection.execute(
            "SELECT value FROM project_metadata WHERE key='memory_ledger_control.v1'"
        ).fetchone() is None
    assert after == before


def test_migration_rollback_schema_ready_gate_and_target_validation(
    isolated_agentpro_storage,
) -> None:
    repository = _repository("PROJ-ledger-phase4-migration")
    _drop_project_ledger(repository)
    original = PROJECT_DB_MIGRATIONS[-1]

    def fail_after_schema(connection: sqlite3.Connection) -> None:
        original.apply(connection)
        raise RuntimeError("synthetic schema crash")

    migrations = (*PROJECT_DB_MIGRATIONS[:-1], replace(original, apply=fail_after_schema))
    with pytest.raises(RuntimeError, match="synthetic schema crash"):
        repository.migrate_schema(
            migrations=migrations,
            backup_path=isolated_agentpro_storage / "migration-crash.backup",
            maintenance_confirmed=True,
            release_head="TEST-HEAD",
        )
    assert repository.inspect_schema().current_version == 5
    with repository.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_events'"
        ).fetchone() is None

    repository.migrate_schema(
        backup_path=isolated_agentpro_storage / "migration-success.backup",
        maintenance_confirmed=True,
        release_head="TEST-HEAD",
    )
    with pytest.raises(MemoryLedgerNotActive):
        create_research(
            repository,
            operation_id="phase4-research-command",
            research_id="RESEARCH-neutral-phase4",
            question="What does the synthetic fixture prove?",
            purpose="Migration gate proof",
            requested_by="operator-neutral",
            related_entity_refs=(),
        )
    assert repository.get_metadata("research.v1") is None

    with repository.connect() as connection:
        connection.execute("DROP TRIGGER memory_events_reject_delete")
        connection.execute(
            "CREATE TRIGGER memory_events_reject_delete BEFORE DELETE ON memory_events "
            "BEGIN SELECT 1; END"
        )
    with pytest.raises(MemoryLedgerIntegrityError):
        repository.migrate_schema()


def test_bootstrap_crash_retry_and_active_retry_validate_saved_manifest(
    isolated_agentpro_storage,
) -> None:
    repository = _repository("PROJ-ledger-phase4-bootstrap")
    _drop_project_ledger(repository)
    research_state = {
        "schema_version": 1,
        "project_id": repository.context.project_id,
        "book_id": repository.context.book_id,
        "records": {},
        "sources": {},
        "claims": {},
        "operations": {},
        "conflicts": {},
        "decisions": {},
    }
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO project_metadata(key,value) VALUES ('research.v1',?)",
            (json.dumps(research_state, sort_keys=True, separators=(",", ":")),),
        )
    repository.migrate_schema(
        backup_path=isolated_agentpro_storage / "bootstrap.backup",
        maintenance_confirmed=True,
        release_head="TEST-HEAD",
    )
    with repository.connect() as connection:
        connection.execute(
            "CREATE TRIGGER phase4_abort_activation BEFORE INSERT ON memory_events "
            "WHEN NEW.event_type='LEDGER_ACTIVATED' "
            "BEGIN SELECT RAISE(ABORT, 'synthetic activation crash'); END"
        )
    with pytest.raises(MemoryLedgerError):
        repository.bootstrap_memory_ledger(maintenance_confirmed=True)
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0] == 0
        control = json.loads(connection.execute(
            "SELECT value FROM project_metadata WHERE key='memory_ledger_control.v1'"
        ).fetchone()[0])
        assert control["state"] == "SCHEMA_READY"
        connection.execute("DROP TRIGGER phase4_abort_activation")

    activation = repository.bootstrap_memory_ledger(maintenance_confirmed=True)
    with repository.connect() as connection:
        before = tuple(
            connection.execute(
                "SELECT memory_event_id,content_hash FROM memory_events ORDER BY sequence"
            ).fetchall()
        )
        research_state["schema_version"] = 2
        connection.execute(
            "UPDATE project_metadata SET value=? WHERE key='research.v1'",
            (json.dumps(research_state, sort_keys=True, separators=(",", ":")),),
        )
    assert repository.bootstrap_memory_ledger(maintenance_confirmed=True) == activation
    with repository.connect() as connection:
        after = tuple(
            connection.execute(
                "SELECT memory_event_id,content_hash FROM memory_events ORDER BY sequence"
            ).fetchall()
        )
    assert after == before


def test_wal_backup_contains_committed_state(isolated_agentpro_storage) -> None:
    repository = _repository("PROJ-ledger-phase4-wal")
    _drop_project_ledger(repository)
    writer = sqlite3.connect(repository.db_path)
    try:
        assert str(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower() == "wal"
        writer.execute(
            "INSERT INTO project_metadata(key,value) VALUES ('phase4.wal','committed')"
        )
        writer.commit()
        backup = isolated_agentpro_storage / "wal-state.backup"
        repository.migrate_schema(
            backup_path=backup, maintenance_confirmed=True, release_head="TEST-HEAD",
        )
    finally:
        writer.close()
    with sqlite3.connect(backup) as connection:
        assert connection.execute(
            "SELECT value FROM project_metadata WHERE key='phase4.wal'"
        ).fetchone()[0] == "committed"
        assert connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == 5


def test_final_audit_confirmation_and_chapter_events_rollback_as_one_unit(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    repository = _repository("PROJ-ledger-phase4-final-audit")
    document = {
        "chapter_id": "chapter_neutral_001",
        "version": 1,
        "quality_evaluation": {"artifact_path": "runs/neutral/quality.json"},
        "versions": [
            {
                "version": 1,
                "status": "ACCEPTED",
                "artifact_hash": "4" * 64,
                "step_id": "STEP-neutral-final-audit",
                "source_evaluation": None,
            }
        ],
    }
    plan = CrossStoreOperationPlan(
        operation_id="phase4-final-audit-op",
        project_id=repository.context.project_id,
        book_id=repository.context.book_id,
        operation_type="CHAPTER_ARTIFACT_LINEAGE_V2",
        source_ref="runs/neutral/write.json",
        artifact_relative_path="artifacts/neutral/chapter_neutral_001.json",
        artifact_bytes=json.dumps(document, sort_keys=True).encode("utf-8"),
        expected_versions={"chapter_version": 1},
        provenance_refs=("runs/neutral/write.json",),
        run_id="RUN-neutral-final-audit",
        step_id="STEP-neutral-final-audit",
    )
    original_append = repository_module._append_memory_event

    def abort_chapter_event(connection, event):
        if event.event_type == "CHAPTER_VERSION_RECORDED":
            raise MemoryLedgerIdentityConflict("synthetic chapter event failure")
        return original_append(connection, event)

    with monkeypatch.context() as failure:
        failure.setattr(repository_module, "_append_memory_event", abort_chapter_event)
        with pytest.raises(MemoryLedgerIdentityConflict):
            CrossStoreRecoveryService(repository).execute(plan)

    failed = repository.get_cross_store_operation(plan.operation_id)
    assert failed["status"] == "IN_PROGRESS"
    assert "FINAL_AUDIT" not in failed["completed_writes"]
    assert repository.get_metadata("cross_store_audit.v1:" + plan.operation_id) is None
    events, cursor = repository.list_memory_events(
        operation=("CROSS_STORE_OPERATION", plan.operation_id), limit=200
    )
    assert cursor is None
    assert [event.event_type for event in events] == ["ARTIFACT_WRITE_INTENDED"]

    reopened = ProjectRepository(repository.context)
    recovered = CrossStoreRecoveryService(reopened).recover(plan.operation_id)
    assert recovered["status"] == "COMMITTED"
    assert [
        event.event_type
        for event in reopened.list_memory_events(
            operation=("CROSS_STORE_OPERATION", plan.operation_id), limit=200
        )[0]
    ] == [
        "ARTIFACT_WRITE_INTENDED",
        "ARTIFACT_WRITE_CONFIRMED",
        "CHAPTER_VERSION_RECORDED",
    ]
    assert CrossStoreRecoveryService(ProjectRepository(repository.context)).execute(plan) == recovered

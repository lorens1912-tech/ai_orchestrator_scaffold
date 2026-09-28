from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.p20_core.memory_ledger import (
    MemoryLedgerError,
    MemoryLedgerIdentityConflict,
    MemoryLedgerNotActive,
    build_memory_event,
)
from app.p20_core.project_repository import (
    PROJECT_DB_SCHEMA_VERSION,
    SERIES_DB_SCHEMA_VERSION,
    ProjectRepository,
    SchemaMigrationError,
    SeriesAccessContext,
    SeriesAccessError,
    SeriesRepository,
    StorageResolver,
)
from app.p20_core.series_memory import SeriesMembershipRecord


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
@pytest.mark.parametrize("state", ["absent", "legacy", "current", "schema_ready"])
def test_history_connection_never_creates_or_migrates(isolated_agentpro_storage, scope, state):
    from app.p20_core.memory_ledger import MemoryLedgerMigrationRequired

    repo = (ProjectRepository(StorageResolver().resolve_project("PROJ-read-boundary", book_id="BOOK-read-boundary"))
            if scope == "PROJECT" else SeriesRepository(StorageResolver().resolve_series("SERIES-read-boundary")))
    if state != "absent":
        repo.initialize()
        if state == "legacy":
            with sqlite3.connect(repo.db_path) as conn:
                conn.execute("UPDATE schema_version SET version=version-1 WHERE id=1")
        elif state == "schema_ready":
            prefix, version = ("project", 5) if scope == "PROJECT" else ("series", 3)
            with sqlite3.connect(repo.db_path) as conn:
                _drop_ledger(conn, metadata_table=prefix + "_metadata", identity_table=prefix + "_identity", version=version)
            repo.migrate_schema(backup_path=isolated_agentpro_storage / "read-backup.db",
                                maintenance_confirmed=True, release_head="TEST-HEAD")
        before = repo.db_path.read_bytes()
    for _ in range(2):
        if state == "current":
            with repo.connect(read_only=True) as conn:
                with pytest.raises(sqlite3.OperationalError, match="readonly"):
                    conn.execute("CREATE TABLE forbidden_read_mutation (id INTEGER)")
        else:
            expected = MemoryLedgerNotActive if state == "schema_ready" else MemoryLedgerMigrationRequired
            with pytest.raises(expected):
                with repo.connect(read_only=True):
                    pytest.fail("absent/legacy storage cannot be read as active")
    if state == "absent":
        assert not repo.db_path.parent.exists()
    else:
        assert repo.db_path.read_bytes() == before


@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
def test_failed_history_preflight_closes_connection(isolated_agentpro_storage, monkeypatch, scope):
    from app.p20_core import project_repository as storage

    repo = (ProjectRepository(StorageResolver().resolve_project("PROJ-read-error", book_id="BOOK-read-error"))
            if scope == "PROJECT" else SeriesRepository(StorageResolver().resolve_series("SERIES-read-error")))
    repo.initialize()
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute("UPDATE schema_version SET version='invalid' WHERE id=1")
    opened = []
    original = storage._connect_repository_database

    def capture(**kwargs):
        conn = original(**kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(storage, "_connect_repository_database", capture)
    with pytest.raises(SchemaMigrationError):
        with repo.connect(read_only=True):
            pytest.fail("invalid schema must not be readable")
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


@pytest.mark.parametrize(
    "scope,operation,fault",
    [(scope, "connect", fault)
     for scope in ("PROJECT", "SERIES", "SYSTEM")
     for fault in ("busy_timeout", "foreign_keys", "journal_mode", "corrupt_header")]
    + [(scope, "migrate", fault)
       for scope in ("PROJECT", "SERIES")
       for fault in ("busy_timeout", "foreign_keys")]
    + [(scope, "read", "query_only") for scope in ("PROJECT", "SERIES", "SYSTEM")],
)
def test_connection_preparation_failure_releases_fence_and_allows_retry(
    isolated_agentpro_storage, monkeypatch, scope, operation, fault,
):
    from app.p20_core import project_repository as storage

    resolver = StorageResolver()
    if scope == "PROJECT":
        repo = ProjectRepository(resolver.resolve_project("PROJ-prepare", book_id="BOOK-prepare"))
    elif scope == "SERIES":
        repo = SeriesRepository(resolver.resolve_series("SERIES-prepare"))
    else:
        repo = storage.SystemRepository(resolver.resolve_system())
    if fault == "corrupt_header":
        repo.db_path.parent.mkdir(parents=True, exist_ok=True)
        repo.db_path.write_bytes(b"synthetic invalid SQLite header\n" * 64)
    else:
        repo.initialize()
    before = repo.db_path.read_bytes()
    opened = []
    original = storage._connect_repository_database

    def capture(**kwargs):
        conn = original(**kwargs)
        opened.append(conn)
        if fault != "corrupt_header":
            conn.set_authorizer(
                lambda action, name, _value, _db, _source:
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_PRAGMA
                and str(name).lower() == fault else sqlite3.SQLITE_OK
            )
        return conn

    try:
        with monkeypatch.context() as patch:
            patch.setattr(storage, "_connect_repository_database", capture)
            with pytest.raises(sqlite3.DatabaseError):
                if operation == "migrate":
                    repo.migrate_schema()
                else:
                    with repo.connect(read_only=operation == "read"):
                        pytest.fail("failed preparation must not grant a connection")
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            opened[0].execute("SELECT 1")
        assert opened[0]._physical_fence is None
        assert repo.db_path.read_bytes() == before
        if fault != "corrupt_header":
            if operation == "migrate":
                assert repo.migrate_schema().current_version == (
                    PROJECT_DB_SCHEMA_VERSION if scope == "PROJECT" else SERIES_DB_SCHEMA_VERSION
                )
            for _ in range(2):
                with repo.connect(read_only=True) as conn:
                    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
                    if scope != "SYSTEM":
                        assert conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0] == 1
            assert repo.db_path.read_bytes() == before
    finally:
        for conn in opened:
            conn.close()


def _project_event(
    repo: ProjectRepository,
    *,
    sequence: int = 2,
    operation_id: str = "operation-test-001",
    payload=None,
):
    default_payload = {
        "phase": "STARTED",
        "attempt_ordinal": 0,
        "source_refs": ["synthetic:source"],
        "outcome": "STARTED",
    }
    return build_memory_event(
        sequence=sequence,
        scope_type="PROJECT",
        scope_id=repo.context.project_id,
        operation={"namespace": "CANONICAL_PIPELINE", "id": operation_id},
        event_slot=("start",),
        event_type="EXTRACTION_STARTED",
        actor={"kind": "SYSTEM", "id": "TEST_SYSTEM", "evidence_ref": None},
        project_id=repo.context.project_id,
        book_id=repo.context.book_id,
        series_id=None,
        structured_payload=default_payload if payload is None else payload,
    )


def _drop_ledger(conn: sqlite3.Connection, *, metadata_table: str, identity_table: str, version: int) -> None:
    # The fixture rewinds a newly initialized project to its historical P5
    # shape. GAP-018 tables belong to P7 and cannot remain in that P5 fixture.
    if identity_table == "project_identity":
        for table in ("translation_request_receipts", "translation_heads", "translation_records"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("DROP TABLE IF EXISTS gap018_source_head")
        conn.execute("DROP TABLE IF EXISTS gap018_manuscript_head")
        conn.execute("DROP TABLE IF EXISTS gap018_records")
    for trigger in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'memory_event%'"
    ).fetchall():
        conn.execute(f"DROP TRIGGER {trigger[0]}")
    conn.execute("DROP TABLE memory_event_entities")
    conn.execute("DROP TABLE memory_events")
    conn.execute(f"DELETE FROM {metadata_table} WHERE key = 'memory_ledger_control.v1'")
    conn.execute("UPDATE schema_version SET version = ? WHERE id = 1", (version,))
    conn.execute(f"UPDATE {identity_table} SET schema_version = ? WHERE id = 1", (version,))


def test_project_append_is_idempotent_append_only_and_paginated(isolated_agentpro_storage) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("PROJ-ledger-a", book_id="BOOK-ledger-a"))
    repo.initialize()
    event = _project_event(repo)
    with repo.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert repo.append_memory_event(conn, event) == event
        assert repo.append_memory_event(conn, event) == event

    with repo.connect() as conn:
        row = conn.execute("SELECT record_json, event_key_json FROM memory_events WHERE sequence=2").fetchone()
        original_json, original_key = row["record_json"], row["event_key_json"]
        for recursive in ("OFF", "ON"):
            conn.execute(f"PRAGMA recursive_triggers = {recursive}")
            with pytest.raises(sqlite3.IntegrityError, match="append-only collision"):
                conn.execute(
                    "INSERT OR REPLACE INTO memory_events "
                    "(sequence,memory_event_id,event_key_json,scope_type,scope_id,operation_namespace,operation_id,event_type,schema_version,record_json,content_hash) "
                    "SELECT sequence,memory_event_id,event_key_json,scope_type,scope_id,operation_namespace,operation_id,event_type,schema_version,record_json,content_hash FROM memory_events WHERE sequence=2"
                )
        same = conn.execute("SELECT record_json,event_key_json FROM memory_events WHERE sequence=2").fetchone()
        assert tuple(same) == (original_json, original_key)
        with pytest.raises(sqlite3.IntegrityError, match="append-only collision"):
            conn.execute(
                "INSERT INTO memory_event_entities(sequence,record_type,entity_id) VALUES (2,'FACT','FACT-not-present')"
            )
        candidate = _project_event(repo, sequence=3, operation_id="operation-test-002")
        existing_id = event.memory_event_id

        def insert_candidate(sequence, memory_event_id, key):
            conn.execute(
                "INSERT INTO memory_events(sequence,memory_event_id,event_key_json,scope_type,scope_id,operation_namespace,operation_id,event_type,schema_version,record_json,content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (sequence, memory_event_id, key, "PROJECT", repo.context.project_id,
                 candidate.operation["namespace"], candidate.operation["id"], candidate.event_type,
                 candidate.schema_version, candidate.to_json(), candidate.content_hash),
            )

        for sequence, memory_event_id, key in (
            (2, candidate.memory_event_id, "{\"different\":\"sequence\"}"),
            (candidate.sequence, existing_id, "{\"different\":\"id\"}"),
            (candidate.sequence, candidate.memory_event_id, original_key),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only collision"):
                insert_candidate(sequence, memory_event_id, key)

    conflict = _project_event(repo, payload={
        "phase": "STARTED", "attempt_ordinal": 0,
        "source_refs": ["synthetic:different-source"], "outcome": "STARTED",
    })
    with repo.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(MemoryLedgerIdentityConflict):
            repo.append_memory_event(conn, conflict)

    records, cursor = repo.list_memory_events(limit=1)
    assert len(records) == 1 and cursor is not None
    next_records, final_cursor = repo.list_memory_events(cursor=cursor, limit=1)
    assert [item.sequence for item in next_records] == [2]
    assert final_cursor is None


def test_codec_rejects_unknown_payload_and_keeps_unicode_hash_deterministic() -> None:
    kwargs = dict(
        sequence=1, scope_type="PROJECT", scope_id="PROJ-ledger-codec",
        operation={"namespace": "CANONICAL_PIPELINE", "id": "operation-unicode-ą"}, event_slot=("start",),
        event_type="EXTRACTION_STARTED", actor={"kind": "SYSTEM", "id": "TEST_SYSTEM", "evidence_ref": None},
        project_id="PROJ-ledger-codec", book_id="BOOK-ledger-codec", series_id=None,
        structured_payload={"phase": "STARTED", "attempt_ordinal": 0,
                            "source_refs": ["synthetic:source"], "outcome": "STARTED"},
        timestamp="2026-09-23T12:00:00.000000Z",
    )
    first, second = build_memory_event(**kwargs), build_memory_event(**kwargs)
    assert first.memory_event_id == second.memory_event_id
    assert first.content_hash == second.content_hash
    with pytest.raises(MemoryLedgerError, match="unknown fields"):
        build_memory_event(**(kwargs | {"structured_payload": {"metadata": "forbidden"}}))


def test_append_failure_rolls_back_owner_transaction(isolated_agentpro_storage) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("PROJ-ledger-rollback", book_id="BOOK-ledger-rollback"))
    repo.initialize()
    with pytest.raises(sqlite3.IntegrityError):
        with repo.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO project_metadata(key,value) VALUES ('ledger.rollback.test','written')")
            conn.execute("DELETE FROM memory_events WHERE sequence = 1")
    assert repo.get_metadata("ledger.rollback.test") is None


def test_concurrent_duplicate_append_returns_one_durable_event(isolated_agentpro_storage) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("PROJ-ledger-concurrent", book_id="BOOK-ledger-concurrent"))
    repo.initialize()
    event = _project_event(repo)

    def append_once() -> str:
        with repo.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return repo.append_memory_event(conn, event).memory_event_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert set(pool.map(lambda _: append_once(), range(2))) == {event.memory_event_id}
    with repo.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_events WHERE sequence=2").fetchone()[0] == 1


def test_series_requires_membership_and_empty_store_activation(isolated_agentpro_storage) -> None:
    series_id, project_id, book_id = "SERIES-ledger-a", "PROJ-ledger-series-a", "BOOK-ledger-series-a"
    repo = SeriesRepository(StorageResolver().resolve_series(series_id))
    repo.initialize()
    access = SeriesAccessContext.bind(project_id, series_id)
    with pytest.raises(SeriesAccessError):
        repo.list_memory_events(access)
    membership = SeriesMembershipRecord(
        series_id=series_id, project_id=project_id, book_id=book_id,
        source_ref="synthetic/membership", version=1, created_at="2026-09-23T00:00:00Z",
    )
    repo.register_member(access, membership)
    event = build_memory_event(
        sequence=2, scope_type="SERIES", scope_id=series_id,
        operation={"namespace": "CANONICAL_PIPELINE", "id": "series-op-001"}, event_slot=("start",),
        event_type="EXTRACTION_STARTED", actor={"kind": "SYSTEM", "id": "TEST_SYSTEM", "evidence_ref": None},
        project_id=project_id, book_id=book_id, series_id=series_id,
        structured_payload={"phase": "STARTED", "attempt_ordinal": 0,
                            "source_refs": ["synthetic:source"], "outcome": "STARTED"},
    )
    with repo.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        repo.append_memory_event(conn, event, access=access)
    assert repo.get_memory_event(access, event.memory_event_id) == event
    forged = build_memory_event(
        sequence=3, scope_type="SERIES", scope_id=series_id,
        operation={"namespace": "LEDGER_BOOTSTRAP", "id": "bootstrap-v1:SERIES:forged"},
        event_slot=("activated",), event_type="LEDGER_ACTIVATED",
        actor={
            "kind": "SYSTEM", "id": "MEMORY_LEDGER_BOOTSTRAP_V1",
            "evidence_ref": None,
        },
        project_id=project_id, book_id=book_id, series_id=series_id,
        structured_payload={"baseline_count": 0, "manifest_hash": "0" * 64,
                            "coverage": "MEMORY_PIPELINES_V1", "origin": "EMPTY_STORE"},
    )
    with repo.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(SeriesAccessError, match="internal maintenance"):
            repo.append_memory_event(conn, forged, access=access)


def test_project_v5_migration_creates_verified_backup_and_bootstraps(isolated_agentpro_storage) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("PROJ-ledger-migrate", book_id="BOOK-ledger-migrate"))
    repo.initialize()
    with repo.connect() as conn:
        _drop_ledger(conn, metadata_table="project_metadata", identity_table="project_identity", version=5)
        conn.execute(
            "INSERT INTO project_metadata(key,value) VALUES ('research.v1',?)",
            (json.dumps({
                "schema_version": 1,
                "project_id": "PROJ-ledger-migrate",
                "book_id": "BOOK-ledger-migrate",
                "records": {},
                "sources": {},
                "claims": {},
                "operations": {},
                "conflicts": {},
                "decisions": {},
            }, sort_keys=True, separators=(",", ":")),),
        )
    backup = isolated_agentpro_storage / "project-ledger.backup"
    migrated = repo.migrate_schema(
        backup_path=backup, maintenance_confirmed=True, release_head="TEST-HEAD",
    )
    assert migrated.current_version == PROJECT_DB_SCHEMA_VERSION == 8
    assert backup.exists()
    with pytest.raises(MemoryLedgerNotActive):
        repo.get_memory_event("MEV-" + "0" * 64)
    activation = repo.bootstrap_memory_ledger(maintenance_confirmed=True)
    assert activation.event_type == "LEDGER_ACTIVATED"
    assert repo.bootstrap_memory_ledger(maintenance_confirmed=True) == activation


def test_ledger_migration_refuses_existing_unverified_backup(isolated_agentpro_storage) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("PROJ-ledger-backup", book_id="BOOK-ledger-backup"))
    repo.initialize()
    with repo.connect() as conn:
        _drop_ledger(conn, metadata_table="project_metadata", identity_table="project_identity", version=5)
    backup = isolated_agentpro_storage / "corrupt-ledger.backup"
    backup.write_text("not a sqlite backup", encoding="utf-8")
    with pytest.raises(SchemaMigrationError, match="backup path already exists"):
        repo.migrate_schema(
            backup_path=backup, maintenance_confirmed=True, release_head="TEST-HEAD",
        )
    assert repo.inspect_schema().current_version == 5


def test_two_migrators_and_bootstrap_retry_converge(isolated_agentpro_storage) -> None:
    context = StorageResolver().resolve_project("PROJ-ledger-race", book_id="BOOK-ledger-race")
    first, second = ProjectRepository(context), ProjectRepository(context)
    first.initialize()
    with first.connect() as conn:
        _drop_ledger(conn, metadata_table="project_metadata", identity_table="project_identity", version=5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(
            lambda args: args[0].migrate_schema(
                backup_path=args[1], maintenance_confirmed=True, release_head="TEST-HEAD",
            ),
            ((first, isolated_agentpro_storage / "race-a.backup"), (second, isolated_agentpro_storage / "race-b.backup")),
        ))
    assert [status.current_version for status in statuses] == [PROJECT_DB_SCHEMA_VERSION] * 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        activations = list(pool.map(
            lambda repo: repo.bootstrap_memory_ledger(maintenance_confirmed=True),
            (first, second),
        ))
    assert {event.memory_event_id for event in activations} == {activations[0].memory_event_id}


def test_series_v3_bootstrap_preserves_all_operation_receipts(isolated_agentpro_storage) -> None:
    series_id = "SERIES-ledger-migrate"
    repo = SeriesRepository(StorageResolver().resolve_series(series_id))
    repo.initialize()
    raw_result = '{"raw": [1, 2], "alias": true}'
    with repo.connect() as conn:
        _drop_ledger(conn, metadata_table="series_metadata", identity_table="series_identity", version=3)
        conn.execute(
            "INSERT INTO series_operations(operation_id,semantic_hash,result_type,result_id,result_payload_json) VALUES (?,?,?,?,?)",
            ("operation-primary", "a" * 64, "VOLUME_CLOSING_SNAPSHOT", "SNAPSHOT-a", raw_result),
        )
        conn.execute(
            "INSERT INTO series_operations(operation_id,semantic_hash,result_type,result_id,result_payload_json) VALUES (?,?,?,?,?)",
            ("operation-alias", "a" * 64, "VOLUME_CLOSING_SNAPSHOT", "SNAPSHOT-a", raw_result),
        )
    backup = isolated_agentpro_storage / "series-ledger.backup"
    assert repo.migrate_schema(
        backup_path=backup, maintenance_confirmed=True, release_head="TEST-HEAD",
    ).current_version == SERIES_DB_SCHEMA_VERSION == 4
    activation = repo._bootstrap_memory_ledger_internal(maintenance_confirmed=True)
    assert activation.event_type == "LEDGER_ACTIVATED"
    with repo.connect() as conn:
        rows = conn.execute("SELECT record_json FROM memory_events WHERE event_type='LEDGER_BASELINE_OBJECT'").fetchall()
        historical = conn.execute(
            "SELECT event_type FROM memory_events WHERE event_type IN ('SERIES_VOLUME_CLOSED','CANONICAL_COMMITTED')"
        ).fetchall()
    assert historical == []
    snapshots = [json.loads(row["record_json"])["structured_payload"]["snapshot_text"] for row in rows]
    operation_rows = [json.loads(snapshot) for snapshot in snapshots if "result_payload_json" in snapshot]
    assert len(operation_rows) == 2
    assert [row["result_payload_json"] for row in operation_rows] == [raw_result, raw_result]

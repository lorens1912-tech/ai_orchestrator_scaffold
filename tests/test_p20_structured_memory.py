from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.p20_core.domain_records import (
    CharacterState,
    DomainContractError,
    EventRecord,
    FactRecord,
    KnowledgeEvent,
    PayoffRecord,
    SetupRecord,
    ThreadRecord,
)
from app.p20_core.project_repository import (
    PROJECT_DB_MIGRATIONS,
    PROJECT_DB_SCHEMA_VERSION,
    ProjectRepository,
    ProjectStorageError,
    SeriesRepository,
    StorageResolver,
    StorageScope,
    SystemRepository,
)


STAMP = "2026-09-11T00:00:00Z"


def _table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        return {str(row[0]) for row in rows}
    finally:
        conn.close()


def _reset_sqlite_file(db_path: Path) -> None:
    for path in (
        db_path,
        Path(str(db_path) + "-wal"),
        Path(str(db_path) + "-shm"),
    ):
        path.unlink(missing_ok=True)


def _create_project_v1_db(db_path: Path) -> None:
    _reset_sqlite_file(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE schema_version (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE project_identity (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                project_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            )
            """
        )
        conn.execute("INSERT INTO schema_version (id, version) VALUES (1, 1)")
        conn.execute(
            """
            INSERT INTO project_identity (id, project_id, book_id, schema_version)
            VALUES (1, 'PROJ-migration', 'BOOK-migration', 1)
            """
        )
        conn.commit()
    finally:
        conn.close()


def _fact(**overrides):
    data = {
        "fact_id": "FACT-door-open",
        "project_id": "PROJ-memory",
        "subject_id": "CHAR-ada",
        "predicate": "opened",
        "object_type": "state",
        "object_id": None,
        "object_value": "archive door",
        "reality_status": "CANON",
        "verification_status": "VERIFIED",
        "confidence": 1.0,
        "frozen": True,
        "author_locked": False,
        "valid_from": "001",
        "valid_to": None,
        "established_event_id": "EVENT-door",
        "established_scene_id": "SCENE-001",
        "source_artifact_ref": "books/memory/chapters/chapter_001.json",
        "source_refs": ["SCENE-001#p1"],
        "canon_version": 1,
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return FactRecord(**data)


def _character_state(**overrides):
    data = {
        "state_id": "CONTEXT-ada-state-001",
        "project_id": "PROJ-memory",
        "character_id": "CHAR-ada",
        "source_scene_id": "SCENE-001",
        "source_event_id": "EVENT-door",
        "source_artifact_ref": "books/memory/chapters/chapter_001.json",
        "state_payload": {"mood": "resolved", "injured": False},
        "valid_from": "001",
        "valid_to": None,
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return CharacterState(**data)


def _event(**overrides):
    data = {
        "event_id": "EVENT-door",
        "project_id": "PROJ-memory",
        "event_type": "DISCOVERY",
        "time_start": "day-1T08:00",
        "time_end": "day-1T08:05",
        "narrative_order": 1,
        "location_id": "PLACE-archive",
        "participant_ids": ["CHAR-ada"],
        "description": "Ada opens the archive door.",
        "cause_refs": [],
        "effect_refs": [],
        "source_scene_id": "SCENE-001",
        "source_artifact_ref": "books/memory/chapters/chapter_001.json",
        "canon_status": "CANON",
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return EventRecord(**data)


def _knowledge(**overrides):
    data = {
        "knowledge_event_id": "KNOWLEDGE-ada-door",
        "project_id": "PROJ-memory",
        "character_id": "CHAR-ada",
        "fact_id": "FACT-door-open",
        "event_id": None,
        "knowledge_status": "KNOWS",
        "learned_at_scene_id": "SCENE-001",
        "learned_at_event_id": "EVENT-door",
        "learned_from_character_id": None,
        "source_ref": "SCENE-001#p2",
        "valid_from": "001",
        "valid_to": None,
        "confidence": 1.0,
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return KnowledgeEvent(**data)


def _thread(**overrides):
    data = {
        "thread_id": "THREAD-archive",
        "project_id": "PROJ-memory",
        "name": "Archive mystery",
        "description": "The missing archive index points to a buried plot.",
        "importance": 8,
        "opened_scene_id": "SCENE-001",
        "closed_scene_id": None,
        "status": "OPEN",
        "payoff_required": True,
        "target_payoff": "Reveal who emptied the shelf.",
        "actual_payoff_ref": None,
        "deliberately_left_reason": None,
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return ThreadRecord(**data)


def _setup(**overrides):
    data = {
        "setup_id": "SETUP-marker",
        "project_id": "PROJ-memory",
        "created_scene_id": "SCENE-001",
        "description": "A brass marker is hidden in the shelf.",
        "importance": 7,
        "expected_payoff": "The marker opens the lower archive.",
        "target_range": "ACT-002..ACT-003",
        "actual_payoff_scene_id": None,
        "status": "OPEN",
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return SetupRecord(**data)


def _payoff(**overrides):
    data = {
        "payoff_id": "PAYOFF-marker",
        "project_id": "PROJ-memory",
        "setup_id": "SETUP-marker",
        "completed_scene_id": "SCENE-009",
        "description": "The brass marker opens the lower archive.",
        "source_artifact_ref": "books/memory/chapters/chapter_009.json",
        "status": "PAID_OFF",
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return PayoffRecord(**data)


def test_structured_memory_record_contracts_validate_namespaces_and_provenance() -> None:
    records = [_fact(), _character_state(), _event(), _knowledge(), _thread(), _setup(), _payoff()]

    assert {record.memory_record_type for record in records} == {
        "FACT",
        "CHARACTER_STATE",
        "EVENT",
        "KNOWLEDGE_EVENT",
        "THREAD",
        "SETUP",
        "PAYOFF",
    }
    assert all(record.scope == StorageScope.project("PROJ-memory") for record in records)
    assert _fact().record_id.value == "FACT-door-open"
    assert _knowledge().record_id.value == "KNOWLEDGE-ada-door"

    with pytest.raises(DomainContractError, match="fact_id"):
        _fact(fact_id="EVENT-door")

    with pytest.raises(DomainContractError, match="provenance"):
        _fact(established_scene_id=None, source_artifact_ref=None, source_refs=[])


def test_frozen_and_author_locked_are_independent_fields() -> None:
    canon_locked = _fact(frozen=True, author_locked=False)
    author_locked = _fact(fact_id="FACT-author-lock", frozen=False, author_locked=True)

    assert canon_locked.frozen is True
    assert canon_locked.author_locked is False
    assert author_locked.frozen is False
    assert author_locked.author_locked is True


def test_temporal_and_version_validation_for_memory_records() -> None:
    with pytest.raises(DomainContractError, match="valid_to"):
        _fact(valid_from="010", valid_to="001")

    with pytest.raises(DomainContractError, match="version"):
        _knowledge(version=0)

    with pytest.raises(DomainContractError, match="confidence"):
        _knowledge(confidence=1.5)

    with pytest.raises(DomainContractError, match="fact_id or event_id"):
        _knowledge(fact_id=None, event_id=None)


def test_structured_memory_records_are_deterministically_serialized() -> None:
    first = _character_state(state_payload={"b": 2, "a": 1})
    second = _character_state(state_payload={"a": 1, "b": 2})

    assert first.to_json() == second.to_json()
    assert '"state_payload":{"a":1,"b":2}' in first.to_json()


def test_project_repository_persists_structured_memory_records(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("PROJ-memory"))
    _reset_sqlite_file(repo.db_path)

    with repo.domain_transaction() as tx:
        for record in (_fact(), _character_state(), _event(), _knowledge(), _thread(), _setup(), _payoff()):
            tx.add_structured_memory_record(record)

    records = repo.list_structured_memory_records()
    scopes = repo.list_structured_memory_record_scopes()

    assert set(records) == {
        "FACT-door-open",
        "CONTEXT-ada-state-001",
        "EVENT-door",
        "KNOWLEDGE-ada-door",
        "THREAD-archive",
        "SETUP-marker",
        "PAYOFF-marker",
    }
    assert repo.list_structured_memory_records("FACT") == {
        "FACT-door-open": _fact().to_json(),
    }
    assert scopes["FACT-door-open"] == {
        "record_type": "FACT",
        "scope_type": "PROJECT",
        "scope_id": "PROJ-memory",
    }
    assert repo.db_path == isolated_agentpro_storage / "projects" / "PROJ-memory" / "project.db"


def test_structured_memory_transaction_rolls_back_all_records(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("PROJ-memory-rollback"))
    _reset_sqlite_file(repo.db_path)

    with pytest.raises(RuntimeError, match="boom"):
        with repo.domain_transaction() as tx:
            tx.add_structured_memory_record(_fact(project_id="PROJ-memory-rollback"))
            tx.add_structured_memory_record(_event(project_id="PROJ-memory-rollback"))
            raise RuntimeError("boom")

    assert repo.list_structured_memory_records() == {}


def test_structured_memory_rejects_cross_project_write(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("PROJ-memory-a"))
    _reset_sqlite_file(repo.db_path)

    with pytest.raises(ProjectStorageError, match="record scope does not match"):
        with repo.domain_transaction() as tx:
            tx.add_structured_memory_record(_fact(project_id="PROJ-memory-b"))

    assert repo.list_structured_memory_records() == {}


def test_project_schema_v2_adds_structured_memory_table_by_controlled_migration(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("PROJ-migration", book_id="BOOK-migration"))
    _create_project_v1_db(repo.db_path)

    status = repo.inspect_schema()

    assert status.current_version == 1
    assert status.required_version == PROJECT_DB_SCHEMA_VERSION
    assert status.migration_needed is True
    assert "project_structured_memory_records" not in _table_names(repo.db_path)

    with pytest.raises(ProjectStorageError, match="controlled migration"):
        repo.initialize()

    migrated = repo.migrate_schema(PROJECT_DB_MIGRATIONS)
    repo.initialize()

    assert migrated.current_version == PROJECT_DB_SCHEMA_VERSION
    assert repo.get_schema_version() == PROJECT_DB_SCHEMA_VERSION
    assert repo.get_project_identity()["schema_version"] == PROJECT_DB_SCHEMA_VERSION
    assert "project_structured_memory_records" in _table_names(repo.db_path)


def test_structured_memory_stays_in_project_db_not_series_or_system_db(
    isolated_agentpro_storage,
) -> None:
    resolver = StorageResolver()
    project_repo = ProjectRepository(resolver.resolve_project("PROJ-memory-storage"))
    series_repo = SeriesRepository(resolver.resolve_series("SERIES-memory-storage"))
    system_repo = SystemRepository(resolver.resolve_system())
    _reset_sqlite_file(project_repo.db_path)
    _reset_sqlite_file(series_repo.db_path)
    _reset_sqlite_file(system_repo.db_path)

    with project_repo.domain_transaction() as tx:
        tx.add_structured_memory_record(_fact(project_id="PROJ-memory-storage"))

    series_repo.set_metadata("probe", "series")
    system_repo.set_metadata("probe", "system")

    assert "project_structured_memory_records" in _table_names(project_repo.db_path)
    assert "project_structured_memory_records" not in _table_names(series_repo.db_path)
    assert "project_structured_memory_records" not in _table_names(system_repo.db_path)
    assert project_repo.db_path.is_relative_to(isolated_agentpro_storage)
    assert series_repo.db_path.is_relative_to(isolated_agentpro_storage)
    assert system_repo.db_path.is_relative_to(isolated_agentpro_storage)

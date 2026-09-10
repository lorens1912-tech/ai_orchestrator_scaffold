from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.p20_core.project_repository import (
    PROJECT_DB_SCHEMA_VERSION,
    SERIES_DB_SCHEMA_VERSION,
    SYSTEM_DB_SCHEMA_VERSION,
    ProjectRepository,
    ProjectStorageError,
    SchemaMigration,
    SchemaMigrationError,
    SchemaMigrationRunner,
    SeriesRepository,
    StorageResolver,
    SystemRepository,
    ensure_system_repository,
)
from app.p20_core.storage_paths import get_system_db_path


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


def _create_versioned_db(db_path: Path, version: int) -> None:
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
            "INSERT INTO schema_version (id, version) VALUES (1, ?)",
            (version,),
        )
        conn.commit()
    finally:
        conn.close()


def _read_schema_version(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
        return int(row[0])
    finally:
        conn.close()


def test_system_repository_creates_agentpro_system_db(isolated_agentpro_storage) -> None:
    repo = ensure_system_repository()

    assert repo.db_path == isolated_agentpro_storage / "agentpro_system.db"
    assert repo.db_path == get_system_db_path()
    assert repo.db_path.exists()
    assert repo.get_schema_version() == SYSTEM_DB_SCHEMA_VERSION
    assert str(repo.get_pragma("journal_mode")).lower() == "wal"
    assert int(repo.get_pragma("foreign_keys")) == 1


def test_project_series_and_system_databases_have_schema_versions(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()
    project_repo = ProjectRepository(resolver.resolve_book("gap003_schema_project"))
    series_repo = SeriesRepository(resolver.resolve_series("gap003_schema_series"))
    system_repo = SystemRepository(resolver.resolve_system())

    project_repo.initialize()
    series_repo.initialize()
    system_repo.initialize()

    project_status = project_repo.inspect_schema()
    series_status = series_repo.inspect_schema()
    system_status = system_repo.inspect_schema()

    assert project_status.current_version == PROJECT_DB_SCHEMA_VERSION
    assert project_status.required_version == PROJECT_DB_SCHEMA_VERSION
    assert project_status.initialized is True
    assert project_status.migration_needed is False
    assert project_status.newer_than_supported is False

    assert series_status.current_version == SERIES_DB_SCHEMA_VERSION
    assert series_status.required_version == SERIES_DB_SCHEMA_VERSION
    assert series_status.initialized is True
    assert series_status.migration_needed is False
    assert series_status.newer_than_supported is False

    assert system_status.current_version == SYSTEM_DB_SCHEMA_VERSION
    assert system_status.required_version == SYSTEM_DB_SCHEMA_VERSION
    assert system_status.initialized is True
    assert system_status.migration_needed is False
    assert system_status.newer_than_supported is False


def test_migration_runner_recognizes_current_version(isolated_agentpro_storage) -> None:
    db_path = isolated_agentpro_storage / "migration_current.db"
    _create_versioned_db(db_path, 1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        status = SchemaMigrationRunner(target_version=1).inspect(conn)
    finally:
        conn.close()

    assert status.current_version == 1
    assert status.required_version == 1
    assert status.migration_needed is False
    assert status.newer_than_supported is False


def test_migration_runner_applies_n_to_n_plus_one_deterministically(
    isolated_agentpro_storage,
) -> None:
    db_path = isolated_agentpro_storage / "migration_forward.db"
    _create_versioned_db(db_path, 1)
    events: list[str] = []

    def backup(conn: sqlite3.Connection) -> None:
        events.append("backup")

    def apply(conn: sqlite3.Connection) -> None:
        events.append("migration")
        conn.execute("CREATE TABLE migrated_values (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO migrated_values (id, value) VALUES ('one', 'stable')")

    def validate(conn: sqlite3.Connection) -> None:
        events.append("validation")
        row = conn.execute("SELECT value FROM migrated_values WHERE id = 'one'").fetchone()
        version = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
        assert row[0] == "stable"
        assert int(version[0]) == 1

    migration = SchemaMigration(
        source_version=1,
        target_version=2,
        backup=backup,
        apply=apply,
        validate=validate,
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        first = SchemaMigrationRunner(target_version=2, migrations=[migration]).migrate(conn)
        second = SchemaMigrationRunner(target_version=2, migrations=[migration]).migrate(conn)
    finally:
        conn.close()

    assert first.current_version == 2
    assert second.current_version == 2
    assert events == ["backup", "migration", "validation"]
    assert _read_schema_version(db_path) == 2


def test_migration_error_rolls_back_whole_database_migration(isolated_agentpro_storage) -> None:
    db_path = isolated_agentpro_storage / "migration_rollback.db"
    _create_versioned_db(db_path, 1)

    def apply(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE half_migrated (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO half_migrated (id) VALUES ('partial')")
        raise RuntimeError("migration failed")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(RuntimeError, match="migration failed"):
            SchemaMigrationRunner(
                target_version=2,
                migrations=[SchemaMigration(1, 2, apply=apply)],
            ).migrate(conn)
    finally:
        conn.close()

    assert _read_schema_version(db_path) == 1
    assert "half_migrated" not in _table_names(db_path)


def test_migration_runner_rejects_newer_and_unknown_versions(isolated_agentpro_storage) -> None:
    newer_db = isolated_agentpro_storage / "migration_newer.db"
    missing_step_db = isolated_agentpro_storage / "migration_missing_step.db"
    _create_versioned_db(newer_db, 99)
    _create_versioned_db(missing_step_db, 1)

    newer_conn = sqlite3.connect(str(newer_db))
    newer_conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(SchemaMigrationError, match="newer than supported"):
            SchemaMigrationRunner(target_version=2).migrate(newer_conn)
    finally:
        newer_conn.close()

    missing_conn = sqlite3.connect(str(missing_step_db))
    missing_conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(SchemaMigrationError, match="missing migration"):
            SchemaMigrationRunner(
                target_version=3,
                migrations=[
                    SchemaMigration(
                        source_version=1,
                        target_version=2,
                        apply=lambda conn: conn.execute("CREATE TABLE step_one (id TEXT PRIMARY KEY)"),
                    )
                ],
            ).migrate(missing_conn)
    finally:
        missing_conn.close()

    assert _read_schema_version(newer_db) == 99
    assert _read_schema_version(missing_step_db) == 1
    assert "step_one" not in _table_names(missing_step_db)


def test_plain_schema_read_does_not_run_silent_migration(isolated_agentpro_storage) -> None:
    context = StorageResolver().resolve_book("gap003_old_project")
    _create_versioned_db(context.project_db_path, 0)
    repo = ProjectRepository(context)

    status = repo.inspect_schema()

    assert status.current_version == 0
    assert status.required_version == PROJECT_DB_SCHEMA_VERSION
    assert status.migration_needed is True
    assert _read_schema_version(context.project_db_path) == 0

    with pytest.raises(ProjectStorageError, match="controlled migration"):
        repo.initialize()

    assert _read_schema_version(context.project_db_path) == 0


def test_project_domain_transaction_commit_persists_all_changes(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_book("gap003_commit_project"))
    _reset_sqlite_file(repo.db_path)

    with repo.domain_transaction() as tx:
        tx.add_fact_record("fact_commit", '{"text":"accepted fact"}')
        tx.add_character_state("state_commit", "character_a", '{"mood":"focused"}')

    assert repo.list_fact_records() == {"fact_commit": '{"text":"accepted fact"}'}
    assert repo.list_character_states() == {
        "state_commit": {
            "character_id": "character_a",
            "payload_json": '{"mood":"focused"}',
        }
    }


def test_project_domain_transaction_rollback_discards_all_changes(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_book("gap003_rollback_project"))
    _reset_sqlite_file(repo.db_path)

    with pytest.raises(RuntimeError, match="boom"):
        with repo.domain_transaction() as tx:
            tx.add_fact_record("fact_rollback", '{"text":"partial fact"}')
            tx.add_character_state("state_rollback", "character_a", '{"mood":"partial"}')
            raise RuntimeError("boom")

    assert repo.list_fact_records() == {}
    assert repo.list_character_states() == {}


def test_project_series_and_system_databases_do_not_mix_scopes(
    isolated_agentpro_storage,
) -> None:
    resolver = StorageResolver()
    project_repo = ProjectRepository(resolver.resolve_book("gap003_scope_project"))
    series_repo = SeriesRepository(resolver.resolve_series("gap003_scope_series"))
    system_repo = SystemRepository(resolver.resolve_system())
    _reset_sqlite_file(project_repo.db_path)
    _reset_sqlite_file(series_repo.db_path)
    _reset_sqlite_file(system_repo.db_path)

    with project_repo.domain_transaction() as tx:
        tx.add_fact_record("fact_scope", '{"scope":"project"}')
    series_repo.set_metadata("scope", "series")
    system_repo.set_metadata("scope", "system")

    project_tables = _table_names(project_repo.db_path)
    series_tables = _table_names(series_repo.db_path)
    system_tables = _table_names(system_repo.db_path)

    assert project_repo.db_path == isolated_agentpro_storage / "projects" / "gap003_scope_project" / "project.db"
    assert series_repo.db_path == isolated_agentpro_storage / "series" / "gap003_scope_series" / "series.db"
    assert system_repo.db_path == isolated_agentpro_storage / "agentpro_system.db"

    assert "project_fact_records" in project_tables
    assert "project_character_states" in project_tables
    assert "series_identity" not in project_tables
    assert "series_metadata" not in project_tables
    assert "system_metadata" not in project_tables

    assert "series_identity" in series_tables
    assert "series_metadata" in series_tables
    assert "project_identity" not in series_tables
    assert "project_fact_records" not in series_tables
    assert "system_metadata" not in series_tables

    assert "system_metadata" in system_tables
    assert "project_identity" not in system_tables
    assert "project_fact_records" not in system_tables
    assert "project_character_states" not in system_tables
    assert "series_identity" not in system_tables

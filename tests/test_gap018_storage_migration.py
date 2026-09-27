"""P6 -> P7 compatibility and GAP-018 project-owned storage gates."""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.p20_core.project_repository import (
    ProjectRepository, ProjectStorageError, SeriesRepository, StorageResolver,
)


def _legacy_p6_repository(storage_root):
    repository = ProjectRepository(StorageResolver(storage_root).resolve_project(
        "PROJ-GAP018-MIGRATION", book_id="BOOK-GAP018-MIGRATION",
    ))
    repository.initialize()
    repository.set_metadata("model_invocation.v1:legacy-proof", json.dumps({
        "project_id": repository.context.project_id,
        "book_id": repository.context.book_id,
        "status": "SYNTHETIC_LEGACY_RECORD",
    }))
    with sqlite3.connect(repository.db_path) as connection:
        for table in ("gap018_records", "gap018_source_head", "gap018_manuscript_head"):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute("UPDATE schema_version SET version=6 WHERE id=1")
        connection.execute("UPDATE project_identity SET schema_version=6 WHERE id=1")
    return repository


def test_controlled_p6_to_p7_preserves_existing_identity_metadata_and_ledger(isolated_agentpro_storage):
    repo = _legacy_p6_repository(isolated_agentpro_storage)
    series = SeriesRepository(StorageResolver(isolated_agentpro_storage).resolve_series(
        "SERIES-GAP018-MIGRATION-UNRELATED",
    ))
    series.initialize()
    series_bytes_before = series.db_path.read_bytes()
    with sqlite3.connect(repo.db_path) as connection:
        ledger_rows_before = connection.execute("SELECT * FROM memory_events ORDER BY rowid").fetchall()
    assert repo.inspect_schema().current_version == 6
    assert repo.inspect_schema().migration_needed
    with pytest.raises(ProjectStorageError):
        repo.initialize()
    upgraded = repo.migrate_schema()
    assert upgraded.current_version == 7
    assert not upgraded.migration_needed
    assert series.db_path.read_bytes() == series_bytes_before
    reopened = ProjectRepository(StorageResolver(isolated_agentpro_storage).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id,
    ))
    assert reopened.get_project_identity() == {
        "project_id": repo.context.project_id,
        "book_id": repo.context.book_id,
        "schema_version": 7,
    }
    assert json.loads(reopened.get_metadata_readonly("model_invocation.v1:legacy-proof"))[
        "status"
    ] == "SYNTHETIC_LEGACY_RECORD"
    with reopened.connect(read_only=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert [tuple(row) for row in connection.execute(
            "SELECT * FROM memory_events ORDER BY rowid",
        )] == ledger_rows_before
        assert connection.execute("SELECT COUNT(*) FROM gap018_records").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM gap018_source_head").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM gap018_manuscript_head").fetchone()[0] == 0
    assert reopened.inspect_schema().current_version == 7
    assert not reopened.inspect_schema().migration_needed
    assert series.db_path.read_bytes() == series_bytes_before


def test_new_project_has_p7_without_migration(isolated_agentpro_storage):
    repo = ProjectRepository(StorageResolver(isolated_agentpro_storage).resolve_project(
        "PROJ-GAP018-NEW", book_id="BOOK-GAP018-NEW",
    ))
    repo.initialize()
    assert repo.get_schema_version() == 7
    with repo.connect(read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM gap018_source_head").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM gap018_manuscript_head").fetchone()[0] == 0

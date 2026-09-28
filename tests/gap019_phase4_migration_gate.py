"""Direct synthetic schema 7 to 8 migration and reopen gate for GAP-019."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from app.p20_core.project_repository import (
    ProjectRepository, SeriesRepository, StorageResolver,
)
from app.p20_core.source_promotion import current_source_master
from tests.test_gap018_manuscript_version import case, make_ready_candidate
from tests.test_gap019_phase2 import _source


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="agentpro-gap019-migration-") as directory:
        root = Path(directory)
        os.environ["AGENTPRO_STORAGE_ROOT"] = str(root)
        prepared = make_ready_candidate(case.__wrapped__(root))
        repo, _root, _actor, source = _source(None, prepared)
        series = SeriesRepository(StorageResolver(root).resolve_series("SERIES-GAP019-NEUTRAL"))
        series.initialize()
        series_hash = _hash(series.db_path)
        with closing(sqlite3.connect(repo.db_path)) as connection, connection:
            connection.row_factory = sqlite3.Row
            before = {
                table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("memory_events", "gap018_records", "gap018_source_head", "project_metadata")
            }
            for table in ("translation_records", "translation_heads", "translation_request_receipts"):
                connection.execute(f"DROP TABLE {table}")
            connection.execute("UPDATE schema_version SET version=7 WHERE id=1")
            connection.execute("UPDATE project_identity SET schema_version=7 WHERE id=1")
        assert repo.inspect_schema().current_version == 7
        assert repo.migrate_schema().current_version == 8
        reopened = ProjectRepository(StorageResolver(root).resolve_project(
            repo.context.project_id, book_id=repo.context.book_id))
        assert reopened.inspect_schema().current_version == 8
        assert reopened.inspect_schema().migration_needed is False
        assert reopened.migrate_schema().current_version == 8
        assert current_source_master(reopened).source_master_id == source["source_master_id"]
        with reopened.connect(read_only=True) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            for table, rows in before.items():
                assert [tuple(row) for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY rowid")] == rows, table
            translation_rows = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("translation_records", "translation_heads", "translation_request_receipts")
            }
        assert translation_rows == {table: 0 for table in translation_rows}
        assert _hash(series.db_path) == series_hash
        print(json.dumps({"status": "PASS", "schema_before": 7, "schema_after": 8,
            "source_master_id": source["source_master_id"],
            "ledger_rows": len(before["memory_events"]),
            "translation_rows": translation_rows, "series_db_unchanged": True,
            "integrity_check": "ok", "reopen_no_migration": True}, sort_keys=True))


if __name__ == "__main__":
    main()

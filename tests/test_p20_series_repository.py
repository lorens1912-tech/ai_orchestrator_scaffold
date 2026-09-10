from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.p20_core.project_repository import (
    PROJECT_DB_FILENAME,
    SERIES_DB_FILENAME,
    SERIES_DB_SCHEMA_VERSION,
    ProjectRepository,
    SeriesRepository,
    SeriesStorageContext,
    SeriesStorageError,
    StorageResolver,
    ensure_series_repository,
)
from app.p20_core.storage_paths import get_projects_root, get_series_root, get_storage_root


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_series_storage_resolution_is_deterministic(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()

    first = resolver.resolve_series("gap002_series_a")
    second = resolver.resolve_series("gap002_series_a")

    assert first == second
    assert first.series_id == "gap002_series_a"
    assert first.storage_root == isolated_agentpro_storage.resolve()
    assert first.series_collection_root == isolated_agentpro_storage / "series"
    assert first.series_root == isolated_agentpro_storage / "series" / "gap002_series_a"
    assert first.database_path == first.series_root / SERIES_DB_FILENAME


def test_two_series_get_separate_series_dbs(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()
    context_a = resolver.resolve_series("gap002_series_a")
    context_b = resolver.resolve_series("gap002_series_b")

    assert context_a.database_path != context_b.database_path
    assert context_a.series_root != context_b.series_root

    repo_a = SeriesRepository(context_a)
    repo_b = SeriesRepository(context_b)
    repo_a.initialize()
    repo_b.initialize()

    assert context_a.database_path.exists()
    assert context_b.database_path.exists()
    assert context_a.database_path.is_relative_to(isolated_agentpro_storage)
    assert context_b.database_path.is_relative_to(isolated_agentpro_storage)


def test_series_repository_is_bound_to_one_context(isolated_agentpro_storage) -> None:
    context = StorageResolver().resolve_series("gap002_bound_series")
    repo = SeriesRepository(context)

    repo.initialize()

    assert repo.context == context
    assert repo.db_path == context.database_path
    assert isinstance(repo.context, SeriesStorageContext)
    assert repo.get_series_identity() == {
        "series_id": "gap002_bound_series",
        "schema_version": SERIES_DB_SCHEMA_VERSION,
    }

    for method in (repo.set_metadata, repo.get_metadata, repo.list_metadata):
        assert "series_id" not in inspect.signature(method).parameters
        assert "project_id" not in inspect.signature(method).parameters


def test_write_to_series_a_is_not_visible_in_series_b(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()
    repo_a = SeriesRepository(resolver.resolve_series("gap002_isolated_a"))
    repo_b = SeriesRepository(resolver.resolve_series("gap002_isolated_b"))

    repo_a.set_metadata("shared_fact", "series-a-only")

    assert repo_a.get_metadata("shared_fact") == "series-a-only"
    assert repo_b.get_metadata("shared_fact") is None
    assert "shared_fact" not in repo_b.list_metadata()


def test_series_repository_rejects_series_db_identity_mismatch(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()
    context_a = resolver.resolve_series("gap002_identity_a")
    context_b = resolver.resolve_series("gap002_identity_b")
    repo_a = SeriesRepository(context_a)
    repo_a.initialize()

    mismatched_context = SeriesStorageContext(
        series_id=context_b.series_id,
        storage_root=context_a.storage_root,
        series_collection_root=context_a.series_collection_root,
        series_root=context_a.series_root,
        database_path=context_a.database_path,
    )

    with pytest.raises(SeriesStorageError, match="identity does not match"):
        SeriesRepository(mismatched_context).initialize()


def test_project_and_series_repositories_use_separate_databases(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()
    project_context = resolver.resolve_book("gap002_project_a")
    series_context = resolver.resolve_series("gap002_series_for_a")
    project_repo = ProjectRepository(project_context)
    series_repo = SeriesRepository(series_context)

    project_repo.set_metadata("scope", "project")
    series_repo.set_metadata("scope", "series")

    assert project_context.project_db_path != series_context.database_path
    assert project_context.project_db_path.name == PROJECT_DB_FILENAME
    assert series_context.database_path.name == SERIES_DB_FILENAME
    assert project_repo.get_metadata("scope") == "project"
    assert series_repo.get_metadata("scope") == "series"
    assert series_repo.db_path.is_relative_to(isolated_agentpro_storage / "series")
    assert project_repo.db_path.is_relative_to(isolated_agentpro_storage / "projects")


def test_absent_series_id_does_not_create_global_series_db(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()

    assert resolver.resolve_optional_series(None) is None
    assert resolver.resolve_optional_series("") is None
    assert resolver.resolve_optional_series("   ") is None
    assert ensure_series_repository(None) is None
    assert ensure_series_repository("") is None

    assert not (isolated_agentpro_storage / "series").exists()
    assert not (isolated_agentpro_storage / "series.db").exists()


def test_series_resolver_respects_test_storage_root(isolated_agentpro_storage) -> None:
    context = StorageResolver().resolve_series("gap002_storage_root")

    assert get_storage_root() == isolated_agentpro_storage
    assert get_projects_root() == isolated_agentpro_storage / "projects"
    assert get_series_root() == isolated_agentpro_storage / "series"
    assert context.series_root == isolated_agentpro_storage / "series" / "gap002_storage_root"
    assert context.database_path == isolated_agentpro_storage / "series" / "gap002_storage_root" / "series.db"
    assert not (REPO_ROOT / "series" / "gap002_storage_root").exists()


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        ".",
        "..",
        "../escape",
        "..\\escape",
        "nested/series",
        "nested\\series",
        "/absolute",
        "C:\\absolute",
    ],
)
def test_series_resolver_rejects_path_traversal_and_storage_escape(
    isolated_agentpro_storage,
    bad_id,
) -> None:
    with pytest.raises(SeriesStorageError):
        StorageResolver().resolve_series(bad_id)

    assert not (isolated_agentpro_storage / "series").exists()


def test_series_repository_initializes_sqlite_wal_and_foreign_keys(isolated_agentpro_storage) -> None:
    repo = SeriesRepository(StorageResolver().resolve_series("gap002_sqlite"))

    repo.initialize()

    assert repo.db_path.exists()
    assert repo.get_schema_version() == SERIES_DB_SCHEMA_VERSION
    assert str(repo.get_pragma("journal_mode")).lower() == "wal"
    assert int(repo.get_pragma("foreign_keys")) == 1

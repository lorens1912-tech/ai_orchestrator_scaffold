from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.p20_core.canon_service import ensure_book_dirs
from app.p20_core.project_repository import (
    PROJECT_DB_FILENAME,
    PROJECT_DB_SCHEMA_VERSION,
    ProjectRepository,
    ProjectStorageContext,
    ProjectStorageError,
    StorageResolver,
)
from app.p20_core.storage_paths import (
    get_audit_root,
    get_books_root,
    get_novel_runs_root,
    get_projects_root,
    get_runs_root,
    get_storage_root,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_project_storage_resolution_is_deterministic(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()

    first = resolver.resolve_book("gap001_book_a")
    second = resolver.resolve_book("gap001_book_a")

    assert first == second
    assert first.project_id == "gap001_book_a"
    assert first.book_id == "gap001_book_a"
    assert first.storage_root == isolated_agentpro_storage.resolve()
    assert first.projects_root == isolated_agentpro_storage / "projects"
    assert first.project_root == isolated_agentpro_storage / "projects" / "gap001_book_a"
    assert first.book_root == isolated_agentpro_storage / "books" / "gap001_book_a"
    assert first.project_db_path == first.project_root / PROJECT_DB_FILENAME


def test_two_projects_get_separate_project_dbs(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()
    context_a = resolver.resolve_book("gap001_book_a")
    context_b = resolver.resolve_book("gap001_book_b")

    assert context_a.project_db_path != context_b.project_db_path
    assert context_a.project_root != context_b.project_root

    repo_a = ProjectRepository(context_a)
    repo_b = ProjectRepository(context_b)
    repo_a.initialize()
    repo_b.initialize()

    assert context_a.project_db_path.exists()
    assert context_b.project_db_path.exists()
    assert context_a.project_db_path.is_relative_to(isolated_agentpro_storage)
    assert context_b.project_db_path.is_relative_to(isolated_agentpro_storage)


def test_project_repository_is_bound_to_one_context(isolated_agentpro_storage) -> None:
    context = StorageResolver().resolve_book("gap001_bound_book")
    repo = ProjectRepository(context)

    repo.initialize()

    assert repo.context == context
    assert repo.db_path == context.project_db_path
    assert isinstance(repo.context, ProjectStorageContext)
    assert repo.get_project_identity() == {
        "project_id": "gap001_bound_book",
        "book_id": "gap001_bound_book",
        "schema_version": PROJECT_DB_SCHEMA_VERSION,
    }

    for method in (repo.set_metadata, repo.get_metadata, repo.list_metadata):
        assert "project_id" not in inspect.signature(method).parameters
        assert "book_id" not in inspect.signature(method).parameters


def test_write_to_project_a_is_not_visible_in_project_b(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()
    repo_a = ProjectRepository(resolver.resolve_book("gap001_isolated_a"))
    repo_b = ProjectRepository(resolver.resolve_book("gap001_isolated_b"))

    repo_a.set_metadata("probe", "project-a-only")

    assert repo_a.get_metadata("probe") == "project-a-only"
    assert repo_b.get_metadata("probe") is None
    assert "probe" not in repo_b.list_metadata()


def test_repository_rejects_project_db_identity_mismatch(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()
    context_a = resolver.resolve_book("gap001_identity_a")
    context_b = resolver.resolve_book("gap001_identity_b")
    repo_a = ProjectRepository(context_a)
    repo_a.initialize()

    mismatched_context = ProjectStorageContext(
        project_id=context_b.project_id,
        book_id=context_b.book_id,
        storage_root=context_a.storage_root,
        projects_root=context_a.projects_root,
        project_root=context_a.project_root,
        book_root=context_b.book_root,
        project_db_path=context_a.project_db_path,
    )

    with pytest.raises(ProjectStorageError, match="identity does not match"):
        ProjectRepository(mismatched_context).initialize()


def test_resolver_respects_test_storage_root(isolated_agentpro_storage) -> None:
    context = StorageResolver().resolve_book("gap001_storage_root")

    assert get_storage_root() == isolated_agentpro_storage
    assert get_projects_root() == isolated_agentpro_storage / "projects"
    assert context.project_root == isolated_agentpro_storage / "projects" / "gap001_storage_root"
    assert context.project_db_path == isolated_agentpro_storage / "projects" / "gap001_storage_root" / "project.db"
    assert not (REPO_ROOT / "projects" / "gap001_storage_root").exists()


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        ".",
        "..",
        "../escape",
        "..\\escape",
        "nested/book",
        "nested\\book",
        "/absolute",
        "C:\\absolute",
    ],
)
def test_resolver_rejects_path_traversal_and_storage_escape(
    isolated_agentpro_storage,
    bad_id,
) -> None:
    with pytest.raises(ProjectStorageError):
        StorageResolver().resolve_book(bad_id)

    assert not (isolated_agentpro_storage / "projects").exists()


def test_project_repository_initializes_sqlite_wal_and_foreign_keys(isolated_agentpro_storage) -> None:
    repo = ProjectRepository(StorageResolver().resolve_book("gap001_sqlite"))

    repo.initialize()

    assert repo.db_path.exists()
    assert repo.get_schema_version() == PROJECT_DB_SCHEMA_VERSION
    assert str(repo.get_pragma("journal_mode")).lower() == "wal"
    assert int(repo.get_pragma("foreign_keys")) == 1


def test_ensure_book_dirs_bootstraps_project_repository(isolated_agentpro_storage) -> None:
    book_id = "gap001_runtime_integration"
    repo_project_dir = REPO_ROOT / "projects" / book_id

    assert not repo_project_dir.exists()

    book_dir = ensure_book_dirs(book_id)
    context = StorageResolver().resolve_book(book_id)
    repo = ProjectRepository(context)

    assert book_dir == isolated_agentpro_storage / "books" / book_id
    assert context.project_db_path.exists()
    assert repo.get_project_identity()["project_id"] == book_id
    assert not repo_project_dir.exists()


def test_existing_storage_paths_contract_stays_unchanged(isolated_agentpro_storage) -> None:
    assert get_storage_root() == isolated_agentpro_storage
    assert get_books_root() == isolated_agentpro_storage / "books"
    assert get_runs_root() == isolated_agentpro_storage / "runs"
    assert get_audit_root() == isolated_agentpro_storage / "audit"
    assert get_novel_runs_root() == isolated_agentpro_storage / "novel_runs"
    assert get_projects_root() == isolated_agentpro_storage / "projects"

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.p20_core.project_repository import (
    ProjectRepository,
    ProjectStorageError,
    ScopeType,
    ScopeValidationError,
    SeriesAccessContext,
    SeriesAccessError,
    SeriesRepository,
    SeriesStorageError,
    StorageResolver,
    StorageScope,
    ensure_series_repository_for_access,
    resolve_optional_project_series_access_context,
    resolve_project_series_access_context,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _reset_sqlite_file(db_path: Path) -> None:
    for path in (
        db_path,
        Path(str(db_path) + "-wal"),
        Path(str(db_path) + "-shm"),
    ):
        path.unlink(missing_ok=True)


def test_scope_type_contract_has_project_and_series_only() -> None:
    assert {scope_type.value for scope_type in ScopeType} == {"PROJECT", "SERIES"}

    with pytest.raises(ScopeValidationError, match="PROJECT or SERIES"):
        StorageScope("GLOBAL", "gap004_global")


def test_storage_scope_is_immutable_hashable_and_deterministically_serialized() -> None:
    first = StorageScope.project("gap004_project_a")
    second = StorageScope.project("gap004_project_a")
    different = StorageScope.project("gap004_project_b")

    assert first == second
    assert first != different
    assert hash(first) == hash(second)
    assert first.to_dict() == {
        "scope_type": "PROJECT",
        "scope_id": "gap004_project_a",
    }

    with pytest.raises(FrozenInstanceError):
        first.scope_id = "mutated"  # type: ignore[misc]


def test_project_and_series_scope_with_same_id_are_distinct() -> None:
    project_scope = StorageScope.project("shared_scope_id")
    series_scope = StorageScope.series("shared_scope_id")

    assert project_scope != series_scope
    assert project_scope.to_dict()["scope_id"] == series_scope.to_dict()["scope_id"]
    assert project_scope.to_dict()["scope_type"] == "PROJECT"
    assert series_scope.to_dict()["scope_type"] == "SERIES"


def test_project_repository_is_bound_to_project_scope(isolated_agentpro_storage) -> None:
    context = StorageResolver().resolve_project("gap004_project_bound")
    repo = ProjectRepository(context)

    repo.set_scoped_metadata(context.scope, "scope", "project")

    assert repo.scope == StorageScope.project("gap004_project_bound")
    assert repo.get_metadata("scope") == "project"
    assert repo.db_path == isolated_agentpro_storage / "projects" / "gap004_project_bound" / "project.db"


def test_series_repository_is_bound_to_series_scope(isolated_agentpro_storage) -> None:
    context = StorageResolver().resolve_series("gap004_series_bound")
    repo = SeriesRepository(context)

    repo.set_scoped_metadata(context.scope, "scope", "series")

    assert repo.scope == StorageScope.series("gap004_series_bound")
    assert repo.get_metadata("scope") == "series"
    assert repo.db_path == isolated_agentpro_storage / "series" / "gap004_series_bound" / "series.db"


def test_project_repository_rejects_cross_project_scope(isolated_agentpro_storage) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("gap004_project_a"))

    with pytest.raises(ProjectStorageError, match="record scope does not match"):
        repo.set_scoped_metadata(StorageScope.project("gap004_project_b"), "bad", "value")


def test_project_repository_rejects_series_scope(isolated_agentpro_storage) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("gap004_project_scope_type"))

    with pytest.raises(ProjectStorageError, match="record scope does not match"):
        repo.set_scoped_metadata(StorageScope.series("gap004_project_scope_type"), "bad", "value")


def test_series_repository_rejects_cross_series_scope(isolated_agentpro_storage) -> None:
    repo = SeriesRepository(StorageResolver().resolve_series("gap004_series_a"))

    with pytest.raises(SeriesStorageError, match="record scope does not match"):
        repo.set_scoped_metadata(StorageScope.series("gap004_series_b"), "bad", "value")


def test_series_repository_rejects_project_scope(isolated_agentpro_storage) -> None:
    repo = SeriesRepository(StorageResolver().resolve_series("gap004_series_scope_type"))

    with pytest.raises(SeriesStorageError, match="record scope does not match"):
        repo.set_scoped_metadata(StorageScope.project("gap004_series_scope_type"), "bad", "value")


def test_independent_project_has_no_series_access(isolated_agentpro_storage) -> None:
    access = resolve_optional_project_series_access_context("gap004_independent_project", None)

    assert access is None
    assert ensure_series_repository_for_access(access) is None
    assert not (isolated_agentpro_storage / "series").exists()


def test_project_series_access_context_is_explicit_and_deterministic(
    isolated_agentpro_storage,
) -> None:
    first = resolve_project_series_access_context("gap004_member_project", "gap004_member_series")
    second = resolve_project_series_access_context("gap004_member_project", "gap004_member_series")
    repo = ensure_series_repository_for_access(first, requested_series_id="gap004_member_series")

    assert first == second
    assert first.project_scope == StorageScope.project("gap004_member_project")
    assert first.series_scope == StorageScope.series("gap004_member_series")
    assert first.to_dict() == {
        "project_scope": {
            "scope_type": "PROJECT",
            "scope_id": "gap004_member_project",
        },
        "series_scope": {
            "scope_type": "SERIES",
            "scope_id": "gap004_member_series",
        },
    }
    assert repo is not None
    assert repo.scope == first.series_scope
    assert repo.db_path == isolated_agentpro_storage / "series" / "gap004_member_series" / "series.db"


def test_project_series_access_rejects_requested_other_series(isolated_agentpro_storage) -> None:
    access = resolve_project_series_access_context("gap004_project_a", "gap004_series_a")

    with pytest.raises(SeriesAccessError, match="not bound to requested series"):
        ensure_series_repository_for_access(access, requested_series_id="gap004_series_b")


def test_series_access_context_rejects_wrong_scope_types() -> None:
    with pytest.raises(SeriesAccessError, match="project_scope must be PROJECT"):
        SeriesAccessContext(
            project_scope=StorageScope.series("gap004_series_a"),
            series_scope=StorageScope.series("gap004_series_a"),
        )

    with pytest.raises(SeriesAccessError, match="series_scope must be SERIES"):
        SeriesAccessContext(
            project_scope=StorageScope.project("gap004_project_a"),
            series_scope=StorageScope.project("gap004_project_a"),
        )


def test_project_domain_records_store_repository_scope(isolated_agentpro_storage) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("gap004_record_scope"))
    _reset_sqlite_file(repo.db_path)

    with repo.domain_transaction() as tx:
        tx.add_fact_record("fact_scope", '{"scope":"project"}', scope=repo.scope)
        tx.add_character_state(
            "state_scope",
            "character_a",
            '{"scope":"project"}',
            scope=repo.scope,
        )

    assert repo.list_fact_record_scopes() == {
        "fact_scope": {
            "scope_type": "PROJECT",
            "scope_id": "gap004_record_scope",
        }
    }
    assert repo.list_character_state_scopes() == {
        "state_scope": {
            "scope_type": "PROJECT",
            "scope_id": "gap004_record_scope",
        }
    }


def test_project_domain_transaction_rejects_mismatched_record_scope(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("gap004_reject_record_scope"))
    _reset_sqlite_file(repo.db_path)

    with pytest.raises(ProjectStorageError, match="record scope does not match"):
        with repo.domain_transaction() as tx:
            tx.add_fact_record("fact_wrong_project", "{}", scope=StorageScope.project("other_project"))

    with pytest.raises(ProjectStorageError, match="record scope does not match"):
        with repo.domain_transaction() as tx:
            tx.add_character_state(
                "state_wrong_type",
                "character_a",
                "{}",
                scope=StorageScope.series("gap004_reject_record_scope"),
            )

    assert repo.list_fact_records() == {}
    assert repo.list_character_states() == {}


def test_scope_resolution_respects_test_storage_root(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()
    project_context = resolver.resolve_project("gap004_storage_project")
    series_context = resolver.resolve_series("gap004_storage_series")
    access = resolver.resolve_series_access("gap004_storage_project", "gap004_storage_series")

    assert project_context.scope == access.project_scope
    assert series_context.scope == access.series_scope
    assert project_context.project_db_path.is_relative_to(isolated_agentpro_storage)
    assert series_context.database_path.is_relative_to(isolated_agentpro_storage)
    assert not (REPO_ROOT / "projects" / "gap004_storage_project").exists()
    assert not (REPO_ROOT / "series" / "gap004_storage_series").exists()

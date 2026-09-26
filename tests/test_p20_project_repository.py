from __future__ import annotations

import inspect
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import app.p20_core.project_repository as project_repository_module
from app.p20_core.canon_service import ensure_book_dirs
from app.p20_core.project_repository import (
    PROJECT_DB_FILENAME,
    PROJECT_DB_SCHEMA_VERSION,
    SERIES_DB_FILENAME,
    SYSTEM_DB_FILENAME,
    ProjectRepository,
    ProjectStorageContext,
    ProjectStorageError,
    SeriesRepository,
    SeriesStorageError,
    StorageResolver,
    SystemRepository,
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


def _create_windows_junction(link: Path, target: Path) -> None:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    assert executable is not None, "PowerShell is required for the Windows junction probe"
    environment = os.environ.copy()
    environment["AGENTPRO_TEST_JUNCTION_LINK"] = str(link)
    environment["AGENTPRO_TEST_JUNCTION_TARGET"] = str(target)
    completed = subprocess.run(
        [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop'; "
            "New-Item -ItemType Junction -Path $env:AGENTPRO_TEST_JUNCTION_LINK "
            "-Target $env:AGENTPRO_TEST_JUNCTION_TARGET | Out-Null",
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


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


def test_resolver_uses_one_storage_root_snapshot(monkeypatch, tmp_path) -> None:
    expected_root = (tmp_path / "expected").resolve()
    foreign_root = (tmp_path / "foreign").resolve()
    monkeypatch.setattr(
        project_repository_module, "get_storage_root", lambda: expected_root
    )
    # These legacy secondary lookups made one resolution observe two roots
    # when the process-wide setting changed concurrently.
    monkeypatch.setattr(
        project_repository_module, "get_projects_root", lambda: foreign_root / "projects", raising=False
    )
    monkeypatch.setattr(
        project_repository_module, "get_books_root", lambda: foreign_root / "books", raising=False
    )
    monkeypatch.setattr(
        project_repository_module, "get_series_root", lambda: foreign_root / "series", raising=False
    )
    monkeypatch.setattr(
        project_repository_module, "get_system_db_path", lambda: foreign_root / "agentpro_system.db", raising=False
    )

    resolver = StorageResolver()
    project = resolver.resolve_project("PROJ-snapshot", book_id="BOOK-snapshot")
    series = resolver.resolve_series("SERIES-snapshot")
    system = resolver.resolve_system()

    assert project.project_db_path == expected_root / "projects" / "PROJ-snapshot" / PROJECT_DB_FILENAME
    assert project.book_root == expected_root / "books" / "BOOK-snapshot"
    assert series.database_path == expected_root / "series" / "SERIES-snapshot" / SERIES_DB_FILENAME
    assert system.database_path == expected_root / "agentpro_system.db"


def test_two_projects_get_separate_project_dbs(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()
    context_a = resolver.resolve_project("PROJ-gap001-a", book_id="BOOK-gap001-a")
    context_b = resolver.resolve_project("PROJ-gap001-b", book_id="BOOK-gap001-b")

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
    context = StorageResolver().resolve_project(
        "PROJ-gap001-bound", book_id="BOOK-gap001-bound"
    )
    repo = ProjectRepository(context)

    repo.initialize()

    assert repo.context == context
    assert repo.db_path == context.project_db_path
    assert isinstance(repo.context, ProjectStorageContext)
    assert repo.get_project_identity() == {
        "project_id": "PROJ-gap001-bound",
        "book_id": "BOOK-gap001-bound",
        "schema_version": PROJECT_DB_SCHEMA_VERSION,
    }

    for method in (repo.set_metadata, repo.get_metadata, repo.list_metadata):
        assert "project_id" not in inspect.signature(method).parameters
        assert "book_id" not in inspect.signature(method).parameters


def test_write_to_project_a_is_not_visible_in_project_b(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()
    repo_a = ProjectRepository(
        resolver.resolve_project("PROJ-gap001-isolated-a", book_id="BOOK-gap001-isolated-a")
    )
    repo_b = ProjectRepository(
        resolver.resolve_project("PROJ-gap001-isolated-b", book_id="BOOK-gap001-isolated-b")
    )

    repo_a.set_metadata("probe", "project-a-only")

    assert repo_a.get_metadata("probe") == "project-a-only"
    assert repo_b.get_metadata("probe") is None
    assert "probe" not in repo_b.list_metadata()


def test_repository_rejects_project_db_identity_mismatch(isolated_agentpro_storage) -> None:
    resolver = StorageResolver()
    context_a = resolver.resolve_project("PROJ-gap001-identity-a", book_id="BOOK-gap001-identity-a")
    context_b = resolver.resolve_project("PROJ-gap001-identity-b", book_id="BOOK-gap001-identity-b")
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


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
@pytest.mark.parametrize("scope", ["PROJECT", "SERIES"])
def test_resolver_rejects_windows_collection_junction_escape(tmp_path, scope: str) -> None:
    storage_root = tmp_path / "storage"
    outside = tmp_path / "outside"
    storage_root.mkdir()
    outside.mkdir()
    collection = storage_root / ("projects" if scope == "PROJECT" else "series")
    _create_windows_junction(collection, outside)

    resolver = StorageResolver(storage_root)
    if scope == "PROJECT":
        with pytest.raises(ProjectStorageError, match="escapes storage root"):
            resolver.resolve_project("PROJ-junction-escape", book_id="BOOK-junction-escape")
        escaped_database = outside / "PROJ-junction-escape" / PROJECT_DB_FILENAME
    else:
        with pytest.raises(SeriesStorageError, match="escapes storage root"):
            resolver.resolve_series("SERIES-junction-escape")
        escaped_database = outside / "SERIES-junction-escape" / SERIES_DB_FILENAME
    assert not escaped_database.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_project_repository_rejects_post_resolution_junction_swap(tmp_path) -> None:
    storage_root = tmp_path / "storage"
    outside = tmp_path / "outside"
    storage_root.mkdir()
    outside.mkdir()
    context = StorageResolver(storage_root).resolve_project(
        "PROJ-post-resolve-junction", book_id="BOOK-post-resolve-junction"
    )
    _create_windows_junction(storage_root / "projects", outside)

    with pytest.raises(ProjectStorageError, match="escapes storage root"):
        ProjectRepository(context).initialize()
    assert not (outside / context.project_id / PROJECT_DB_FILENAME).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_project_repository_rejects_database_symlink_escape_when_supported(tmp_path) -> None:
    storage_root = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    context = StorageResolver(storage_root).resolve_project(
        "PROJ-database-symlink", book_id="BOOK-database-symlink"
    )
    context.project_root.mkdir(parents=True)
    target = outside / "outside.db"
    target.write_bytes(b"neutral-sentinel")
    try:
        context.project_db_path.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Windows symlink creation is unavailable: {exc}")

    with pytest.raises(ProjectStorageError, match="escapes storage root"):
        ProjectRepository(context).initialize()
    assert target.read_bytes() == b"neutral-sentinel"


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-sharing contract")
def test_database_file_cannot_be_swapped_between_validation_and_sqlite_open(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-physical-fence", book_id="BOOK-physical-fence"
    ))
    repository.initialize()
    original_connect = project_repository_module.sqlite3.connect
    moved = repository.db_path.with_name("swapped-project.db")
    observed: dict[str, object] = {}

    def adversarial_connect(*args, **kwargs):
        observed["attempted"] = True
        try:
            os.replace(repository.db_path, moved)
        except OSError as exc:
            observed["blocked"] = exc
        else:
            os.replace(moved, repository.db_path)
            pytest.fail("database replacement succeeded after physical validation")
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(project_repository_module.sqlite3, "connect", adversarial_connect)
    with repository.connect() as connection:
        identity = connection.execute(
            "SELECT project_id,book_id FROM project_identity WHERE id=1"
        ).fetchone()

    assert observed.get("attempted") is True
    assert isinstance(observed.get("blocked"), OSError)
    assert identity["project_id"] == repository.context.project_id
    assert identity["book_id"] == repository.context.book_id
    assert not moved.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path contract")
def test_concurrent_windows_extended_path_representation_remains_contained(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    repository = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-extended-path", book_id="BOOK-extended-path"
    ))
    repository.initialize()
    storage_root = repository.context.storage_root
    original_resolve = Path.resolve

    def extended_resolve(path: Path, strict: bool = False) -> Path:
        resolved = original_resolve(path, strict=strict)
        try:
            resolved.relative_to(storage_root)
        except ValueError:
            return resolved
        return Path("\\\\?\\" + str(resolved))

    monkeypatch.setattr(Path, "resolve", extended_resolve)
    with repository.connect() as connection:
        identity = connection.execute(
            "SELECT project_id,book_id FROM project_identity WHERE id=1"
        ).fetchone()

    assert identity["project_id"] == repository.context.project_id
    assert identity["book_id"] == repository.context.book_id


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-sharing contract")
@pytest.mark.parametrize("scope", ["PROJECT", "SERIES", "SYSTEM"])
def test_inspect_schema_blocks_parent_junction_swap_at_sqlite_open(
    tmp_path, monkeypatch, scope: str,
) -> None:
    storage_root = tmp_path / ("storage-" + scope.lower())
    outside = tmp_path / ("outside-" + scope.lower())
    outside.mkdir()
    resolver = StorageResolver(storage_root)
    if scope == "PROJECT":
        repository = ProjectRepository(resolver.resolve_project(
            "PROJ-inspect-fence", book_id="BOOK-inspect-fence",
        ))
        database_parent = repository.context.project_root
        database_name = PROJECT_DB_FILENAME
        expected_version = PROJECT_DB_SCHEMA_VERSION
    elif scope == "SERIES":
        repository = SeriesRepository(resolver.resolve_series("SERIES-inspect-fence"))
        database_parent = repository.context.series_root
        database_name = SERIES_DB_FILENAME
        expected_version = project_repository_module.SERIES_DB_SCHEMA_VERSION
    else:
        repository = SystemRepository(resolver.resolve_system())
        database_parent = repository.context.storage_root
        database_name = SYSTEM_DB_FILENAME
        expected_version = project_repository_module.SYSTEM_DB_SCHEMA_VERSION
    repository.initialize()
    with project_repository_module.sqlite3.connect(outside / database_name) as connection:
        connection.execute(
            "CREATE TABLE schema_version(id INTEGER PRIMARY KEY,version INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO schema_version(id,version) VALUES(1,777)")

    original_connect = project_repository_module.sqlite3.connect
    moved_parent = database_parent.with_name(database_parent.name + "-pinned")
    observed: dict[str, object] = {}

    def adversarial_connect(*args, **kwargs):
        observed["attempted"] = True
        try:
            os.replace(database_parent, moved_parent)
        except OSError as exc:
            observed["blocked"] = exc
            return original_connect(*args, **kwargs)
        observed["swap_succeeded"] = True
        _create_windows_junction(database_parent, outside)
        try:
            return original_connect(*args, **kwargs)
        finally:
            os.rmdir(database_parent)
            os.replace(moved_parent, database_parent)

    monkeypatch.setattr(project_repository_module.sqlite3, "connect", adversarial_connect)
    status = repository.inspect_schema()
    assert status.current_version == expected_version
    assert observed.get("attempted") is True
    assert isinstance(observed.get("blocked"), OSError)
    assert "swap_succeeded" not in observed


def test_project_repository_initializes_sqlite_wal_and_foreign_keys(isolated_agentpro_storage) -> None:
    repo = ProjectRepository(
        StorageResolver().resolve_project("PROJ-gap001-sqlite", book_id="BOOK-gap001-sqlite")
    )

    repo.initialize()

    assert repo.db_path.exists()
    assert repo.get_schema_version() == PROJECT_DB_SCHEMA_VERSION
    assert str(repo.get_pragma("journal_mode")).lower() == "wal"
    assert int(repo.get_pragma("foreign_keys")) == 1


def test_ensure_book_dirs_bootstraps_project_repository(isolated_agentpro_storage) -> None:
    book_id = "gap001_runtime_integration"
    project_id = "PROJ-gap001-runtime-integration"
    domain_book_id = "BOOK-gap001-runtime-integration"
    repo_project_dir = REPO_ROOT / "projects" / book_id

    assert not repo_project_dir.exists()

    book_dir = ensure_book_dirs(
        book_id, project_id=project_id, domain_book_id=domain_book_id
    )
    context = StorageResolver().resolve_project(project_id, book_id=domain_book_id)
    repo = ProjectRepository(context)

    assert book_dir == isolated_agentpro_storage / "books" / book_id
    assert context.project_db_path.exists()
    assert repo.get_project_identity()["project_id"] == project_id
    assert not repo_project_dir.exists()


def test_existing_storage_paths_contract_stays_unchanged(isolated_agentpro_storage) -> None:
    assert get_storage_root() == isolated_agentpro_storage
    assert get_books_root() == isolated_agentpro_storage / "books"
    assert get_runs_root() == isolated_agentpro_storage / "runs"
    assert get_audit_root() == isolated_agentpro_storage / "audit"
    assert get_novel_runs_root() == isolated_agentpro_storage / "novel_runs"
    assert get_projects_root() == isolated_agentpro_storage / "projects"

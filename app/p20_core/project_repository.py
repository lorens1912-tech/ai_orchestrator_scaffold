from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from app.p20_core.storage_paths import (
    get_books_root,
    get_projects_root,
    get_series_root,
    get_storage_root,
)


PROJECT_DB_SCHEMA_VERSION = 1
PROJECT_DB_FILENAME = "project.db"
SERIES_DB_SCHEMA_VERSION = 1
SERIES_DB_FILENAME = "series.db"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ProjectStorageError(ValueError):
    pass


class SeriesStorageError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectStorageContext:
    project_id: str
    book_id: str
    storage_root: Path
    projects_root: Path
    project_root: Path
    book_root: Path
    project_db_path: Path

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        return {key: str(value) for key, value in data.items()}


@dataclass(frozen=True)
class SeriesStorageContext:
    series_id: str
    storage_root: Path
    series_collection_root: Path
    series_root: Path
    database_path: Path

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        return {key: str(value) for key, value in data.items()}


def _normalize_identifier(
    value: str,
    field_name: str,
    error_type: type[ValueError] = ProjectStorageError,
) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise error_type(f"{field_name} is required")
    if not _SAFE_IDENTIFIER.fullmatch(normalized):
        raise error_type(f"{field_name} contains unsafe path characters")
    if normalized in {".", ".."}:
        raise error_type(f"{field_name} must not be a relative path marker")
    return normalized


def _assert_relative_to(
    path: Path,
    root: Path,
    field_name: str,
    error_type: type[ValueError] = ProjectStorageError,
) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise error_type(f"{field_name} escapes storage root") from exc


class StorageResolver:
    def __init__(self, storage_root: Path | None = None) -> None:
        self._storage_root = storage_root.expanduser().resolve() if storage_root is not None else None

    @property
    def storage_root(self) -> Path:
        return self._storage_root or get_storage_root().resolve()

    def resolve_project(
        self,
        project_id: str,
        *,
        book_id: str | None = None,
    ) -> ProjectStorageContext:
        resolved_project_id = _normalize_identifier(project_id, "project_id")
        resolved_book_id = _normalize_identifier(book_id or resolved_project_id, "book_id")
        storage_root = self.storage_root
        projects_root = get_projects_root().resolve() if self._storage_root is None else storage_root / "projects"
        book_root = get_books_root().resolve() / resolved_book_id if self._storage_root is None else storage_root / "books" / resolved_book_id
        project_root = projects_root / resolved_project_id
        project_db_path = project_root / PROJECT_DB_FILENAME

        _assert_relative_to(projects_root, storage_root, "projects_root")
        _assert_relative_to(project_root, projects_root, "project_root")
        _assert_relative_to(book_root, storage_root, "book_root")
        _assert_relative_to(project_db_path, project_root, "project_db_path")

        return ProjectStorageContext(
            project_id=resolved_project_id,
            book_id=resolved_book_id,
            storage_root=storage_root,
            projects_root=projects_root,
            project_root=project_root,
            book_root=book_root,
            project_db_path=project_db_path,
        )

    def resolve_book(
        self,
        book_id: str,
        *,
        project_id: str | None = None,
    ) -> ProjectStorageContext:
        return self.resolve_project(project_id or book_id, book_id=book_id)

    def resolve_series(self, series_id: str) -> SeriesStorageContext:
        resolved_series_id = _normalize_identifier(series_id, "series_id", SeriesStorageError)
        storage_root = self.storage_root
        series_collection_root = get_series_root().resolve() if self._storage_root is None else storage_root / "series"
        series_root = series_collection_root / resolved_series_id
        database_path = series_root / SERIES_DB_FILENAME

        _assert_relative_to(series_collection_root, storage_root, "series_collection_root", SeriesStorageError)
        _assert_relative_to(series_root, series_collection_root, "series_root", SeriesStorageError)
        _assert_relative_to(database_path, series_root, "database_path", SeriesStorageError)

        return SeriesStorageContext(
            series_id=resolved_series_id,
            storage_root=storage_root,
            series_collection_root=series_collection_root,
            series_root=series_root,
            database_path=database_path,
        )

    def resolve_optional_series(self, series_id: str | None) -> SeriesStorageContext | None:
        if series_id is None or not str(series_id).strip():
            return None
        return self.resolve_series(series_id)


class ProjectRepository:
    def __init__(self, context: ProjectStorageContext) -> None:
        self.context = context

    @property
    def db_path(self) -> Path:
        return self.context.project_db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.context.project_root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.context.project_db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    version INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_identity (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    project_id TEXT NOT NULL,
                    book_id TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, ?)",
                (PROJECT_DB_SCHEMA_VERSION,),
            )
            self._ensure_identity(conn)

    def _ensure_identity(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT project_id, book_id FROM project_identity WHERE id = 1"
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO project_identity (id, project_id, book_id, schema_version)
                VALUES (1, ?, ?, ?)
                """,
                (
                    self.context.project_id,
                    self.context.book_id,
                    PROJECT_DB_SCHEMA_VERSION,
                ),
            )
            return

        if row["project_id"] != self.context.project_id or row["book_id"] != self.context.book_id:
            raise ProjectStorageError("project.db identity does not match repository context")

    def get_schema_version(self) -> int:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            return int(row["version"])

    def get_project_identity(self) -> dict[str, str | int]:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT project_id, book_id, schema_version
                FROM project_identity
                WHERE id = 1
                """
            ).fetchone()
            return {
                "project_id": row["project_id"],
                "book_id": row["book_id"],
                "schema_version": int(row["schema_version"]),
            }

    def set_metadata(self, key: str, value: str) -> None:
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO project_metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(key), str(value)),
            )

    def get_metadata(self, key: str) -> str | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM project_metadata WHERE key = ?",
                (str(key),),
            ).fetchone()
            return None if row is None else str(row["value"])

    def list_metadata(self) -> dict[str, str]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM project_metadata ORDER BY key").fetchall()
            return {str(row["key"]): str(row["value"]) for row in rows}

    def get_pragma(self, name: str) -> str | int:
        with self.connect() as conn:
            row = conn.execute(f"PRAGMA {name}").fetchone()
            return row[0]


class SeriesRepository:
    def __init__(self, context: SeriesStorageContext) -> None:
        self.context = context

    @property
    def db_path(self) -> Path:
        return self.context.database_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.context.series_root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.context.database_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    version INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS series_identity (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    series_id TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS series_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, ?)",
                (SERIES_DB_SCHEMA_VERSION,),
            )
            self._ensure_identity(conn)

    def _ensure_identity(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT series_id FROM series_identity WHERE id = 1"
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO series_identity (id, series_id, schema_version)
                VALUES (1, ?, ?)
                """,
                (
                    self.context.series_id,
                    SERIES_DB_SCHEMA_VERSION,
                ),
            )
            return

        if row["series_id"] != self.context.series_id:
            raise SeriesStorageError("series.db identity does not match repository context")

    def get_schema_version(self) -> int:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            return int(row["version"])

    def get_series_identity(self) -> dict[str, str | int]:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT series_id, schema_version
                FROM series_identity
                WHERE id = 1
                """
            ).fetchone()
            return {
                "series_id": row["series_id"],
                "schema_version": int(row["schema_version"]),
            }

    def set_metadata(self, key: str, value: str) -> None:
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO series_metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(key), str(value)),
            )

    def get_metadata(self, key: str) -> str | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM series_metadata WHERE key = ?",
                (str(key),),
            ).fetchone()
            return None if row is None else str(row["value"])

    def list_metadata(self) -> dict[str, str]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM series_metadata ORDER BY key").fetchall()
            return {str(row["key"]): str(row["value"]) for row in rows}

    def get_pragma(self, name: str) -> str | int:
        with self.connect() as conn:
            row = conn.execute(f"PRAGMA {name}").fetchone()
            return row[0]


def resolve_book_project_context(
    book_id: str,
    *,
    project_id: str | None = None,
) -> ProjectStorageContext:
    return StorageResolver().resolve_book(book_id, project_id=project_id)


def ensure_project_repository_for_book(
    book_id: str,
    *,
    project_id: str | None = None,
) -> ProjectRepository:
    context = resolve_book_project_context(book_id, project_id=project_id)
    repository = ProjectRepository(context)
    repository.initialize()
    return repository


def resolve_series_context(series_id: str) -> SeriesStorageContext:
    return StorageResolver().resolve_series(series_id)


def ensure_series_repository(series_id: str | None) -> SeriesRepository | None:
    context = StorageResolver().resolve_optional_series(series_id)
    if context is None:
        return None
    repository = SeriesRepository(context)
    repository.initialize()
    return repository


__all__ = [
    "PROJECT_DB_FILENAME",
    "PROJECT_DB_SCHEMA_VERSION",
    "SERIES_DB_FILENAME",
    "SERIES_DB_SCHEMA_VERSION",
    "ProjectRepository",
    "ProjectStorageContext",
    "ProjectStorageError",
    "SeriesRepository",
    "SeriesStorageContext",
    "SeriesStorageError",
    "StorageResolver",
    "ensure_project_repository_for_book",
    "ensure_series_repository",
    "resolve_book_project_context",
    "resolve_series_context",
]

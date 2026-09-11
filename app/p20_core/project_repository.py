from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Iterator

from app.p20_core.storage_paths import (
    get_books_root,
    get_projects_root,
    get_series_root,
    get_storage_root,
    get_system_db_path,
)


PROJECT_DB_SCHEMA_VERSION = 1
PROJECT_DB_FILENAME = "project.db"
SERIES_DB_SCHEMA_VERSION = 1
SERIES_DB_FILENAME = "series.db"
SYSTEM_DB_SCHEMA_VERSION = 1
SYSTEM_DB_FILENAME = "agentpro_system.db"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ProjectStorageError(ValueError):
    pass


class SeriesStorageError(ValueError):
    pass


class SystemStorageError(ValueError):
    pass


class SchemaMigrationError(ValueError):
    pass


class ScopeValidationError(ValueError):
    pass


class SeriesAccessError(ValueError):
    pass


class ScopeType(str, Enum):
    PROJECT = "PROJECT"
    SERIES = "SERIES"


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

    @property
    def scope(self) -> StorageScope:
        return StorageScope.project(self.project_id)


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

    @property
    def scope(self) -> StorageScope:
        return StorageScope.series(self.series_id)


@dataclass(frozen=True)
class SystemStorageContext:
    storage_root: Path
    database_path: Path

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        return {key: str(value) for key, value in data.items()}


@dataclass(frozen=True)
class SchemaStatus:
    current_version: int | None
    required_version: int
    initialized: bool
    migration_needed: bool
    newer_than_supported: bool


@dataclass(frozen=True)
class SchemaMigration:
    source_version: int
    target_version: int
    apply: Callable[[sqlite3.Connection], None]
    validate: Callable[[sqlite3.Connection], None] | None = None
    backup: Callable[[sqlite3.Connection], None] | None = None


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


@dataclass(frozen=True)
class StorageScope:
    scope_type: ScopeType
    scope_id: str

    def __post_init__(self) -> None:
        if isinstance(self.scope_type, ScopeType):
            scope_type = self.scope_type
        else:
            try:
                scope_type = ScopeType(str(self.scope_type))
            except ValueError as exc:
                raise ScopeValidationError("scope_type must be PROJECT or SERIES") from exc
        scope_id = _normalize_identifier(
            self.scope_id,
            "scope_id",
            ScopeValidationError,
        )
        object.__setattr__(self, "scope_type", scope_type)
        object.__setattr__(self, "scope_id", scope_id)

    @classmethod
    def project(cls, project_id: str) -> StorageScope:
        return cls(ScopeType.PROJECT, project_id)

    @classmethod
    def series(cls, series_id: str) -> StorageScope:
        return cls(ScopeType.SERIES, series_id)

    def to_dict(self) -> dict[str, str]:
        return {
            "scope_type": self.scope_type.value,
            "scope_id": self.scope_id,
        }


@dataclass(frozen=True)
class SeriesAccessContext:
    project_scope: StorageScope
    series_scope: StorageScope

    def __post_init__(self) -> None:
        project_scope = self.project_scope
        series_scope = self.series_scope
        if not isinstance(project_scope, StorageScope):
            raise SeriesAccessError("project_scope must be a StorageScope")
        if not isinstance(series_scope, StorageScope):
            raise SeriesAccessError("series_scope must be a StorageScope")
        if project_scope.scope_type != ScopeType.PROJECT:
            raise SeriesAccessError("project_scope must be PROJECT")
        if series_scope.scope_type != ScopeType.SERIES:
            raise SeriesAccessError("series_scope must be SERIES")

    @classmethod
    def bind(cls, project_id: str, series_id: str) -> SeriesAccessContext:
        return cls(
            project_scope=StorageScope.project(project_id),
            series_scope=StorageScope.series(series_id),
        )

    @property
    def project_id(self) -> str:
        return self.project_scope.scope_id

    @property
    def series_id(self) -> str:
        return self.series_scope.scope_id

    def require_project(self, project_id: str) -> None:
        if self.project_scope != StorageScope.project(project_id):
            raise SeriesAccessError("project scope is not bound to requested project")

    def require_series(self, series_id: str) -> None:
        if self.series_scope != StorageScope.series(series_id):
            raise SeriesAccessError("project is not bound to requested series scope")

    def to_dict(self) -> dict[str, dict[str, str]]:
        return {
            "project_scope": self.project_scope.to_dict(),
            "series_scope": self.series_scope.to_dict(),
        }


def _normalize_domain_id(
    value: str,
    field_name: str,
    error_type: type[ValueError] = ProjectStorageError,
) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise error_type(f"{field_name} is required")
    return normalized


def _coerce_storage_scope(
    scope: StorageScope,
    error_type: type[ValueError],
) -> StorageScope:
    if not isinstance(scope, StorageScope):
        raise error_type("scope must be a StorageScope")
    return scope


def _require_matching_scope(
    repository_scope: StorageScope,
    record_scope: StorageScope,
    error_type: type[ValueError],
) -> StorageScope:
    resolved_scope = _coerce_storage_scope(record_scope, error_type)
    if resolved_scope != repository_scope:
        raise error_type("record scope does not match repository scope")
    return resolved_scope


def _schema_status(current_version: int | None, required_version: int) -> SchemaStatus:
    return SchemaStatus(
        current_version=current_version,
        required_version=required_version,
        initialized=current_version is not None,
        migration_needed=current_version is not None and current_version < required_version,
        newer_than_supported=current_version is not None and current_version > required_version,
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _has_user_tables(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    return bool(rows)


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        )
        """
    )


def _read_schema_version(conn: sqlite3.Connection) -> int | None:
    if not _table_exists(conn, "schema_version"):
        return None
    row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    if row is None:
        return None
    return int(row["version"] if isinstance(row, sqlite3.Row) else row[0])


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    _ensure_schema_version_table(conn)
    conn.execute(
        """
        INSERT INTO schema_version (id, version)
        VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET version = excluded.version
        """,
        (int(version),),
    )


def _initialize_schema_version(
    conn: sqlite3.Connection,
    *,
    required_version: int,
    database_name: str,
    error_type: type[ValueError],
) -> None:
    current_version = _read_schema_version(conn)
    if current_version is None:
        if _has_user_tables(conn):
            raise error_type(f"{database_name} schema_version is missing")
        _set_schema_version(conn, required_version)
        return

    if current_version < required_version:
        raise error_type(f"{database_name} schema_version requires controlled migration")
    if current_version > required_version:
        raise error_type(f"{database_name} schema_version is newer than supported")


def _inspect_database_schema(database_path: Path, required_version: int) -> SchemaStatus:
    if not database_path.exists():
        return _schema_status(None, required_version)
    conn = sqlite3.connect(str(database_path))
    conn.row_factory = sqlite3.Row
    try:
        return _schema_status(_read_schema_version(conn), required_version)
    finally:
        conn.close()


class SchemaMigrationRunner:
    def __init__(
        self,
        *,
        target_version: int,
        migrations: Iterable[SchemaMigration] = (),
    ) -> None:
        if int(target_version) < 1:
            raise SchemaMigrationError("target schema version must be positive")
        self.target_version = int(target_version)
        self._migrations: dict[int, SchemaMigration] = {}
        for migration in migrations:
            source_version = int(migration.source_version)
            target = int(migration.target_version)
            if target != source_version + 1:
                raise SchemaMigrationError("migrations must advance exactly one schema version")
            if source_version in self._migrations:
                raise SchemaMigrationError(f"duplicate migration from schema version {source_version}")
            self._migrations[source_version] = migration

    def inspect(self, conn: sqlite3.Connection) -> SchemaStatus:
        return _schema_status(_read_schema_version(conn), self.target_version)

    def migrate(self, conn: sqlite3.Connection) -> SchemaStatus:
        status = self.inspect(conn)
        if status.current_version is None:
            raise SchemaMigrationError("schema_version is missing")
        if status.newer_than_supported:
            raise SchemaMigrationError("database schema is newer than supported")
        if not status.migration_needed:
            return status
        if conn.in_transaction:
            raise SchemaMigrationError("migration requires a clean connection")

        conn.execute("BEGIN")
        try:
            version = int(status.current_version)
            while version < self.target_version:
                migration = self._migrations.get(version)
                if migration is None:
                    raise SchemaMigrationError(
                        f"missing migration from schema version {version} to {version + 1}"
                    )
                if migration.backup is not None:
                    migration.backup(conn)
                migration.apply(conn)
                if migration.validate is not None:
                    migration.validate(conn)
                _set_schema_version(conn, migration.target_version)
                version = int(migration.target_version)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        return self.inspect(conn)


class ProjectDomainTransaction:
    def __init__(self, conn: sqlite3.Connection, scope: StorageScope) -> None:
        self._conn = conn
        self._scope = _coerce_storage_scope(scope, ProjectStorageError)

    @property
    def scope(self) -> StorageScope:
        return self._scope

    def add_fact_record(
        self,
        fact_id: str,
        payload_json: str,
        *,
        scope: StorageScope | None = None,
    ) -> None:
        record_scope = self._scope if scope is None else _require_matching_scope(
            self._scope,
            scope,
            ProjectStorageError,
        )
        self._conn.execute(
            """
            INSERT INTO project_fact_records (scope_type, scope_id, fact_id, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                record_scope.scope_type.value,
                record_scope.scope_id,
                _normalize_domain_id(fact_id, "fact_id"),
                str(payload_json),
            ),
        )

    def add_character_state(
        self,
        state_id: str,
        character_id: str,
        payload_json: str,
        *,
        scope: StorageScope | None = None,
    ) -> None:
        record_scope = self._scope if scope is None else _require_matching_scope(
            self._scope,
            scope,
            ProjectStorageError,
        )
        self._conn.execute(
            """
            INSERT INTO project_character_states (scope_type, scope_id, state_id, character_id, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                record_scope.scope_type.value,
                record_scope.scope_id,
                _normalize_domain_id(state_id, "state_id"),
                _normalize_domain_id(character_id, "character_id"),
                str(payload_json),
            ),
        )


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

    def resolve_series_access(
        self,
        project_id: str,
        series_id: str,
    ) -> SeriesAccessContext:
        project_context = self.resolve_project(project_id)
        series_context = self.resolve_series(series_id)
        return SeriesAccessContext(
            project_scope=project_context.scope,
            series_scope=series_context.scope,
        )

    def resolve_optional_series_access(
        self,
        project_id: str,
        series_id: str | None,
    ) -> SeriesAccessContext | None:
        if series_id is None or not str(series_id).strip():
            return None
        return self.resolve_series_access(project_id, series_id)

    def resolve_system(self) -> SystemStorageContext:
        storage_root = self.storage_root
        database_path = get_system_db_path().resolve() if self._storage_root is None else storage_root / SYSTEM_DB_FILENAME

        _assert_relative_to(database_path, storage_root, "database_path", SystemStorageError)

        return SystemStorageContext(
            storage_root=storage_root,
            database_path=database_path,
        )


class ProjectRepository:
    def __init__(self, context: ProjectStorageContext) -> None:
        self.context = context

    @property
    def db_path(self) -> Path:
        return self.context.project_db_path

    @property
    def scope(self) -> StorageScope:
        return self.context.scope

    def require_scope(self, scope: StorageScope) -> None:
        _require_matching_scope(self.scope, scope, ProjectStorageError)

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
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            _initialize_schema_version(
                conn,
                required_version=PROJECT_DB_SCHEMA_VERSION,
                database_name="project.db",
                error_type=ProjectStorageError,
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
                """
                CREATE TABLE IF NOT EXISTS project_fact_records (
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    fact_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_character_states (
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    state_id TEXT PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
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

    def set_scoped_metadata(self, scope: StorageScope, key: str, value: str) -> None:
        self.require_scope(scope)
        self.set_metadata(key, value)

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

    def inspect_schema(self) -> SchemaStatus:
        return _inspect_database_schema(self.context.project_db_path, PROJECT_DB_SCHEMA_VERSION)

    def migrate_schema(
        self,
        migrations: Iterable[SchemaMigration],
        *,
        target_version: int | None = None,
    ) -> SchemaStatus:
        self.context.project_root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.context.project_db_path))
        conn.row_factory = sqlite3.Row
        try:
            return SchemaMigrationRunner(
                target_version=target_version or PROJECT_DB_SCHEMA_VERSION,
                migrations=migrations,
            ).migrate(conn)
        finally:
            conn.close()

    @contextmanager
    def domain_transaction(self) -> Iterator[ProjectDomainTransaction]:
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN")
            tx = ProjectDomainTransaction(conn, self.scope)
            try:
                yield tx
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def list_fact_records(self) -> dict[str, str]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT fact_id, payload_json FROM project_fact_records ORDER BY fact_id"
            ).fetchall()
            return {str(row["fact_id"]): str(row["payload_json"]) for row in rows}

    def list_fact_record_scopes(self) -> dict[str, dict[str, str]]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT fact_id, scope_type, scope_id
                FROM project_fact_records
                ORDER BY fact_id
                """
            ).fetchall()
            return {
                str(row["fact_id"]): {
                    "scope_type": str(row["scope_type"]),
                    "scope_id": str(row["scope_id"]),
                }
                for row in rows
            }

    def list_character_states(self) -> dict[str, dict[str, str]]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT state_id, character_id, payload_json
                FROM project_character_states
                ORDER BY state_id
                """
            ).fetchall()
            return {
                str(row["state_id"]): {
                    "character_id": str(row["character_id"]),
                    "payload_json": str(row["payload_json"]),
                }
                for row in rows
            }

    def list_character_state_scopes(self) -> dict[str, dict[str, str]]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT state_id, scope_type, scope_id
                FROM project_character_states
                ORDER BY state_id
                """
            ).fetchall()
            return {
                str(row["state_id"]): {
                    "scope_type": str(row["scope_type"]),
                    "scope_id": str(row["scope_id"]),
                }
                for row in rows
            }


class SeriesRepository:
    def __init__(self, context: SeriesStorageContext) -> None:
        self.context = context

    @property
    def db_path(self) -> Path:
        return self.context.database_path

    @property
    def scope(self) -> StorageScope:
        return self.context.scope

    def require_scope(self, scope: StorageScope) -> None:
        _require_matching_scope(self.scope, scope, SeriesStorageError)

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
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            _initialize_schema_version(
                conn,
                required_version=SERIES_DB_SCHEMA_VERSION,
                database_name="series.db",
                error_type=SeriesStorageError,
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

    def set_scoped_metadata(self, scope: StorageScope, key: str, value: str) -> None:
        self.require_scope(scope)
        self.set_metadata(key, value)

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

    def inspect_schema(self) -> SchemaStatus:
        return _inspect_database_schema(self.context.database_path, SERIES_DB_SCHEMA_VERSION)

    def migrate_schema(
        self,
        migrations: Iterable[SchemaMigration],
        *,
        target_version: int | None = None,
    ) -> SchemaStatus:
        self.context.series_root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.context.database_path))
        conn.row_factory = sqlite3.Row
        try:
            return SchemaMigrationRunner(
                target_version=target_version or SERIES_DB_SCHEMA_VERSION,
                migrations=migrations,
            ).migrate(conn)
        finally:
            conn.close()


class SystemRepository:
    def __init__(self, context: SystemStorageContext) -> None:
        self.context = context

    @property
    def db_path(self) -> Path:
        return self.context.database_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.context.storage_root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.context.database_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            _initialize_schema_version(
                conn,
                required_version=SYSTEM_DB_SCHEMA_VERSION,
                database_name="agentpro_system.db",
                error_type=SystemStorageError,
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_identity (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    schema_version INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO system_identity (id, schema_version)
                VALUES (1, ?)
                """,
                (SYSTEM_DB_SCHEMA_VERSION,),
            )

    def get_schema_version(self) -> int:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            return int(row["version"])

    def set_metadata(self, key: str, value: str) -> None:
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO system_metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(key), str(value)),
            )

    def get_metadata(self, key: str) -> str | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM system_metadata WHERE key = ?",
                (str(key),),
            ).fetchone()
            return None if row is None else str(row["value"])

    def list_metadata(self) -> dict[str, str]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM system_metadata ORDER BY key").fetchall()
            return {str(row["key"]): str(row["value"]) for row in rows}

    def get_pragma(self, name: str) -> str | int:
        with self.connect() as conn:
            row = conn.execute(f"PRAGMA {name}").fetchone()
            return row[0]

    def inspect_schema(self) -> SchemaStatus:
        return _inspect_database_schema(self.context.database_path, SYSTEM_DB_SCHEMA_VERSION)

    def migrate_schema(
        self,
        migrations: Iterable[SchemaMigration],
        *,
        target_version: int | None = None,
    ) -> SchemaStatus:
        self.context.storage_root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.context.database_path))
        conn.row_factory = sqlite3.Row
        try:
            return SchemaMigrationRunner(
                target_version=target_version or SYSTEM_DB_SCHEMA_VERSION,
                migrations=migrations,
            ).migrate(conn)
        finally:
            conn.close()


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


def resolve_project_series_access_context(
    project_id: str,
    series_id: str,
) -> SeriesAccessContext:
    return StorageResolver().resolve_series_access(project_id, series_id)


def resolve_optional_project_series_access_context(
    project_id: str,
    series_id: str | None,
) -> SeriesAccessContext | None:
    return StorageResolver().resolve_optional_series_access(project_id, series_id)


def ensure_series_repository_for_access(
    access_context: SeriesAccessContext | None,
    *,
    requested_series_id: str | None = None,
) -> SeriesRepository | None:
    if access_context is None:
        return None
    if not isinstance(access_context, SeriesAccessContext):
        raise SeriesAccessError("access_context must be a SeriesAccessContext")
    if requested_series_id is not None:
        access_context.require_series(requested_series_id)
    return ensure_series_repository(access_context.series_id)


def resolve_system_context() -> SystemStorageContext:
    return StorageResolver().resolve_system()


def ensure_system_repository() -> SystemRepository:
    repository = SystemRepository(resolve_system_context())
    repository.initialize()
    return repository


__all__ = [
    "PROJECT_DB_FILENAME",
    "PROJECT_DB_SCHEMA_VERSION",
    "SERIES_DB_FILENAME",
    "SERIES_DB_SCHEMA_VERSION",
    "SYSTEM_DB_FILENAME",
    "SYSTEM_DB_SCHEMA_VERSION",
    "ProjectRepository",
    "ProjectStorageContext",
    "ProjectStorageError",
    "ProjectDomainTransaction",
    "SchemaMigration",
    "SchemaMigrationError",
    "SchemaMigrationRunner",
    "SchemaStatus",
    "ScopeType",
    "ScopeValidationError",
    "SeriesRepository",
    "SeriesAccessContext",
    "SeriesAccessError",
    "SeriesStorageContext",
    "SeriesStorageError",
    "StorageResolver",
    "StorageScope",
    "SystemRepository",
    "SystemStorageContext",
    "SystemStorageError",
    "ensure_project_repository_for_book",
    "ensure_series_repository",
    "ensure_series_repository_for_access",
    "ensure_system_repository",
    "resolve_book_project_context",
    "resolve_optional_project_series_access_context",
    "resolve_project_series_access_context",
    "resolve_series_context",
    "resolve_system_context",
]

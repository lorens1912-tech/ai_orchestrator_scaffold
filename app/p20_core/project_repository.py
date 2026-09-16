from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import deque
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from app.p20_core.domain_records import EdgeRecord
    from app.p20_core.project_graph import (
        DependencyTraversalPolicy, DependencyTraversalStep, GraphNodeRef,
    )

from app.p20_core.domain_mutation_guard import (
    DEFAULT_MUTATION_POLICY,
    DomainMutationGuard,
    MutationContext,
    MutationPolicy,
    MutationSource,
    MutationType,
)
from app.p20_core.storage_paths import (
    get_books_root,
    get_projects_root,
    get_series_root,
    get_storage_root,
    get_system_db_path,
)


PROJECT_DB_SCHEMA_VERSION = 5
PROJECT_DB_FILENAME = "project.db"
SERIES_DB_SCHEMA_VERSION = 3
SERIES_DB_FILENAME = "series.db"
SYSTEM_DB_SCHEMA_VERSION = 1
SYSTEM_DB_FILENAME = "agentpro_system.db"
PROJECT_REGISTRY_METADATA_KEY = "project_registry.v1"
_DOMAIN_DB_BUSY_TIMEOUT_MS = 30_000
_SYSTEM_DB_BUSY_TIMEOUT_MS = 30_000
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


def _create_project_structured_memory_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_structured_memory_records (
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            record_type TEXT NOT NULL,
            record_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, record_type, record_id)
        )
        """
    )


def _apply_project_schema_v1_to_v2(conn: sqlite3.Connection) -> None:
    _create_project_structured_memory_table(conn)
    if _table_exists(conn, "project_identity"):
        conn.execute(
            """
            UPDATE project_identity
            SET schema_version = ?
            WHERE id = 1
            """,
            (2,),
        )


def _validate_project_schema_v2(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "project_structured_memory_records"):
        raise SchemaMigrationError("project structured memory table is missing")


def _create_project_edges_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS edges (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            edge_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, edge_id)
        )
        """
    )
    for side in ("source", "target"):
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS edges_{side} "
            f"ON edges(scope_type, scope_id, {side}_id, edge_id)"
        )


def _apply_project_schema_v2_to_v3(conn: sqlite3.Connection) -> None:
    _create_project_edges_table(conn)
    if _table_exists(conn, "project_identity"):
        conn.execute("UPDATE project_identity SET schema_version = 3 WHERE id = 1")


def _validate_project_schema_v3(conn: sqlite3.Connection) -> None:
    conn.execute(
        "SELECT scope_type, scope_id, edge_id, source_id, target_id, relation_type, "
        "payload_json FROM edges LIMIT 0"
    )


def _create_context_packages_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS context_packages (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            context_package_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            context_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, context_package_id),
            UNIQUE (scope_type, scope_id, operation_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS context_packages_run_step "
        "ON context_packages(scope_type, scope_id, run_id, step_id)"
    )


def _apply_project_schema_v3_to_v4(conn: sqlite3.Connection) -> None:
    _create_context_packages_table(conn)
    if _table_exists(conn, "project_identity"):
        conn.execute("UPDATE project_identity SET schema_version = 4 WHERE id = 1")


def _validate_project_schema_v4(conn: sqlite3.Connection) -> None:
    conn.execute(
        "SELECT scope_type, scope_id, context_package_id, operation_id, run_id, "
        "step_id, context_hash, payload_json FROM context_packages LIMIT 0"
    )


def _create_adaptive_style_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS style_library_profiles (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, profile_id, version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_style_dna (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            book_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, book_id, version)
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS book_style_dna_active "
        "ON book_style_dna(scope_type, scope_id, book_id) WHERE is_active = 1"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_style_recipes (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            recipe_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, recipe_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS style_evaluations (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            style_evaluation_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, style_evaluation_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS style_performance_records (
            scope_type TEXT NOT NULL CHECK (scope_type = 'PROJECT'),
            scope_id TEXT NOT NULL,
            performance_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, performance_id)
        )
        """
    )


def _apply_project_schema_v4_to_v5(conn: sqlite3.Connection) -> None:
    _create_adaptive_style_tables(conn)
    if _table_exists(conn, "project_identity"):
        conn.execute("UPDATE project_identity SET schema_version = 5 WHERE id = 1")


def _validate_project_schema_v5(conn: sqlite3.Connection) -> None:
    for table in (
        "style_library_profiles",
        "book_style_dna",
        "scene_style_recipes",
        "style_evaluations",
        "style_performance_records",
    ):
        conn.execute(f"SELECT payload_json FROM {table} LIMIT 0")


PROJECT_DB_MIGRATIONS = (
    SchemaMigration(
        source_version=1,
        target_version=2,
        apply=_apply_project_schema_v1_to_v2,
        validate=_validate_project_schema_v2,
    ),
    SchemaMigration(
        source_version=2,
        target_version=3,
        apply=_apply_project_schema_v2_to_v3,
        validate=_validate_project_schema_v3,
    ),
    SchemaMigration(
        source_version=3,
        target_version=4,
        apply=_apply_project_schema_v3_to_v4,
        validate=_validate_project_schema_v4,
    ),
    SchemaMigration(
        source_version=4,
        target_version=5,
        apply=_apply_project_schema_v4_to_v5,
        validate=_validate_project_schema_v5,
    ),
)


def _create_series_memory_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_memberships (
            project_id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_state_records (
            scope_type TEXT NOT NULL CHECK (scope_type = 'SERIES'),
            scope_id TEXT NOT NULL,
            state_kind TEXT NOT NULL,
            record_type TEXT NOT NULL,
            record_id TEXT NOT NULL,
            source_project_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, state_kind, record_type, record_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS volume_closing_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL CHECK (scope_type = 'SERIES'),
            scope_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            book_id TEXT NOT NULL,
            source_state_version INTEGER NOT NULL,
            semantic_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE (scope_type, scope_id, project_id, book_id, semantic_hash)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_operations (
            operation_id TEXT PRIMARY KEY,
            semantic_hash TEXT NOT NULL,
            result_type TEXT NOT NULL,
            result_id TEXT NOT NULL,
            result_payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS series_snapshots_lookup
        ON volume_closing_snapshots (
            scope_type, scope_id, source_state_version DESC, snapshot_id
        )
        """
    )


def _apply_series_schema_v1_to_v2(conn: sqlite3.Connection) -> None:
    _create_series_memory_tables(conn)
    if _table_exists(conn, "series_identity"):
        conn.execute("UPDATE series_identity SET schema_version = 2 WHERE id = 1")


def _validate_series_schema_v2(conn: sqlite3.Connection) -> None:
    conn.execute("SELECT project_id, book_id, payload_json FROM series_memberships LIMIT 0")
    conn.execute(
        "SELECT scope_type, scope_id, state_kind, record_type, record_id, "
        "source_project_id, payload_json FROM series_state_records LIMIT 0"
    )
    conn.execute(
        "SELECT snapshot_id, scope_type, scope_id, project_id, book_id, "
        "source_state_version, semantic_hash, payload_json "
        "FROM volume_closing_snapshots LIMIT 0"
    )
    conn.execute(
        "SELECT operation_id, semantic_hash, result_type, result_id, "
        "result_payload_json FROM series_operations LIMIT 0"
    )


def _create_series_edges_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS edges (
            scope_type TEXT NOT NULL CHECK (scope_type = 'SERIES'),
            scope_id TEXT NOT NULL,
            edge_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, edge_id)
        )
        """
    )
    for side in ("source", "target"):
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS series_edges_{side} "
            f"ON edges(scope_type, scope_id, {side}_id, edge_id)"
        )


def _apply_series_schema_v2_to_v3(conn: sqlite3.Connection) -> None:
    _create_series_edges_table(conn)
    if _table_exists(conn, "series_identity"):
        conn.execute("UPDATE series_identity SET schema_version = 3 WHERE id = 1")


def _validate_series_schema_v3(conn: sqlite3.Connection) -> None:
    conn.execute(
        "SELECT scope_type, scope_id, edge_id, source_id, target_id, relation_type, "
        "payload_json FROM edges LIMIT 0"
    )


SERIES_DB_MIGRATIONS = (
    SchemaMigration(
        source_version=1,
        target_version=2,
        apply=_apply_series_schema_v1_to_v2,
        validate=_validate_series_schema_v2,
    ),
    SchemaMigration(
        source_version=2,
        target_version=3,
        apply=_apply_series_schema_v2_to_v3,
        validate=_validate_series_schema_v3,
    ),
)


def _insert_scoped_edge(
    conn: sqlite3.Connection,
    repository_scope: StorageScope,
    record: EdgeRecord,
    *,
    source_scope: StorageScope,
    target_scope: StorageScope,
    error_type: type[ValueError],
) -> None:
    from app.p20_core.domain_records import EdgeRecord

    if not isinstance(record, EdgeRecord):
        raise error_type("graph write requires an EdgeRecord")
    record = EdgeRecord(**record.to_dict())
    for scope in (record.scope, source_scope, target_scope):
        _require_matching_scope(repository_scope, scope, error_type)
    conn.execute(
        "INSERT INTO edges (scope_type, scope_id, edge_id, source_id, target_id, "
        "relation_type, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (record.scope_type, record.scope_id, record.edge_id, str(record.source_id),
         str(record.target_id), record.relation_type.value, record.to_json()),
    )


def _decode_scoped_edge(repository: Any, row: sqlite3.Row, error_type: type[ValueError]) -> EdgeRecord:
    from app.p20_core.domain_records import EdgeRecord

    try:
        record = EdgeRecord(**json.loads(row["payload_json"]))
        repository.require_scope(record.scope)
        serialized = record.to_dict()
        for key in ("scope_type", "scope_id", "edge_id", "source_id", "target_id", "relation_type"):
            if serialized[key] != row[key]:
                raise error_type(f"edge index/payload mismatch: {key}")
        return record
    except (TypeError, ValueError, KeyError) as exc:
        raise error_type("malformed scoped edge") from exc


def _traverse_scoped_dependencies(
    repository: Any,
    start: GraphNodeRef,
    policy: DependencyTraversalPolicy | None,
    *,
    error_type: type[ValueError],
    read_connection: Callable[[], Any],
) -> tuple[DependencyTraversalStep, ...]:
    from app.p20_core.project_graph import (
        DependencyTraversalPolicy, DependencyTraversalStep, GraphNodeRef,
        GraphTraversalLimitError, TraversalDirection,
    )

    if not isinstance(start, GraphNodeRef):
        raise error_type("traversal requires a scoped GraphNodeRef")
    repository.require_scope(start.scope)
    policy = DependencyTraversalPolicy() if policy is None else policy
    if not isinstance(policy, DependencyTraversalPolicy):
        raise error_type("traversal requires a DependencyTraversalPolicy")
    if not policy.read_only:
        repository.initialize()
    pending = deque([(start.node_id, 0)])
    visited_nodes = {str(start.node_id)}
    visited_edges: set[str] = set()
    examined_edges: set[str] = set()
    result = []
    connection = read_connection() if policy.read_only else repository.connect()
    directions = None if policy.relation_directions is None else dict(policy.relation_directions)
    with connection as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN")
        while pending:
            node, depth = pending.popleft()
            if depth >= policy.max_depth or policy.relation_types == ():
                continue
            params: list[Any] = [repository.scope.scope_type.value, repository.scope.scope_id]
            if policy.direction == TraversalDirection.BOTH:
                predicate = "(source_id = ? OR target_id = ?)"
                params.extend([str(node), str(node)])
            else:
                side = "target" if policy.direction == TraversalDirection.INCOMING else "source"
                predicate = f"{side}_id = ?"
                params.append(str(node))
            if policy.relation_types is not None:
                predicate += " AND relation_type IN (" + ",".join("?" for _ in policy.relation_types) + ")"
                params.extend(relation.value for relation in policy.relation_types)
            params.append(policy.max_edges + 1)
            rows = conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? AND "
                + predicate + " ORDER BY edge_id LIMIT ?", params,
            )
            for row in rows:
                edge = repository._decode_edge(row)
                if edge.edge_id in visited_edges:
                    continue
                if edge.edge_id not in examined_edges:
                    if len(examined_edges) >= policy.max_edges:
                        raise GraphTraversalLimitError("dependency traversal max_edges exceeded")
                    examined_edges.add(edge.edge_id)
                if directions is not None:
                    direction = directions.get(edge.relation_type)
                    outgoing = str(edge.source_id) == str(node)
                    if (direction is None
                            or (direction == TraversalDirection.OUTGOING and not outgoing)
                            or (direction == TraversalDirection.INCOMING and outgoing)):
                        continue
                if len(visited_edges) >= policy.max_edges:
                    raise GraphTraversalLimitError("dependency traversal max_edges exceeded")
                visited_edges.add(edge.edge_id)
                target = edge.target_id if str(edge.source_id) == str(node) else edge.source_id
                result.append(DependencyTraversalStep(edge, depth + 1, node, target))
                if str(target) not in visited_nodes:
                    visited_nodes.add(str(target))
                    pending.append((target, depth + 1))
    return tuple(result)


class ProjectDomainTransaction:
    def __init__(self, conn: sqlite3.Connection, scope: StorageScope) -> None:
        self._conn = conn
        self._scope = _coerce_storage_scope(scope, ProjectStorageError)

    @property
    def scope(self) -> StorageScope:
        return self._scope

    def add_edge(
        self, record: EdgeRecord, *, source_scope: StorageScope, target_scope: StorageScope,
    ) -> None:
        _insert_scoped_edge(
            self._conn, self.scope, record, source_scope=source_scope,
            target_scope=target_scope, error_type=ProjectStorageError,
        )

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

    def add_structured_memory_record(
        self,
        record: Any,
        *,
        scope: StorageScope | None = None,
        mutation_source: MutationSource | str = MutationSource.AUTOMATION,
        actor_id: str | None = None,
        mutation_policy: MutationPolicy = DEFAULT_MUTATION_POLICY,
    ) -> None:
        record_scope = self._scope if scope is None else _require_matching_scope(
            self._scope,
            scope,
            ProjectStorageError,
        )
        declared_scope = getattr(record, "scope", None)
        if declared_scope is not None:
            _require_matching_scope(record_scope, declared_scope, ProjectStorageError)
        if hasattr(record, "require_project_scope"):
            record.require_project_scope(record_scope)

        record_type = _normalize_domain_id(
            getattr(record, "memory_record_type", ""),
            "record_type",
        )
        record_id = _normalize_domain_id(
            str(getattr(record, "record_id", "")),
            "record_id",
        )
        to_json = getattr(record, "to_json", None)
        if not callable(to_json):
            raise ProjectStorageError("structured memory record must provide to_json")
        payload_json = str(to_json())
        existing = self._conn.execute(
            """
            SELECT payload_json
            FROM project_structured_memory_records
            WHERE scope_type = ?
              AND scope_id = ?
              AND record_type = ?
              AND record_id = ?
            """,
            (
                record_scope.scope_type.value,
                record_scope.scope_id,
                record_type,
                record_id,
            ),
        ).fetchone()
        mutation_type = MutationType.CREATE if existing is None else MutationType.UPDATE
        decision = DomainMutationGuard(mutation_policy).evaluate(
            current_payload=None if existing is None else str(existing["payload_json"]),
            proposed_payload=payload_json,
            context=MutationContext(
                project_id=record_scope.scope_id,
                record_type=record_type,
                record_id=record_id,
                mutation_type=mutation_type,
                source=mutation_source,
                actor_id=actor_id,
            ),
        )
        if decision.denied:
            raise ProjectStorageError(
                f"domain mutation denied: {decision.reason_code.value}"
            )

        self._conn.execute(
            """
            INSERT INTO project_structured_memory_records (
                scope_type,
                scope_id,
                record_type,
                record_id,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope_type, scope_id, record_type, record_id)
            DO UPDATE SET payload_json = excluded.payload_json
            """,
            (
                record_scope.scope_type.value,
                record_scope.scope_id,
                record_type,
                record_id,
                payload_json,
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
        conn = sqlite3.connect(
            str(self.context.project_db_path),
            timeout=_DOMAIN_DB_BUSY_TIMEOUT_MS / 1000,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {_DOMAIN_DB_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        # Re-applying journal_mode requires a write lock. Concurrent retry
        # owners only need to verify the already-established mode.
        current_journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        if current_journal_mode != "wal":
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
            _create_project_structured_memory_table(conn)
            _create_project_edges_table(conn)
            _create_context_packages_table(conn)
            _create_adaptive_style_tables(conn)
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

    @contextmanager
    def canonical_proposal_transaction(self, proposal_id: str, *, include_writer: bool = False):
        """Serialize proposal/decision writes with the current project state.

        Process records live in existing project metadata, never system storage.
        The snapshot includes edges so changed dependencies invalidate a review.
        """
        proposal_id = _normalize_identifier(proposal_id, "proposal_id")
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            key = "canonical_proposal.v1:" + proposal_id
            row = conn.execute("SELECT value FROM project_metadata WHERE key = ?", (key,)).fetchone()
            document = {} if row is None else json.loads(row["value"])
            records = [dict(r) for r in conn.execute(
                "SELECT record_type, record_id, payload_json FROM project_structured_memory_records "
                "WHERE scope_type = ? AND scope_id = ? ORDER BY record_type, record_id",
                (self.scope.scope_type.value, self.scope.scope_id),
            )]
            edges = [dict(r) for r in conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? ORDER BY edge_id",
                (self.scope.scope_type.value, self.scope.scope_id),
            )]
            snapshot = {"records": records, "edges": edges}
            if include_writer:
                yield document, snapshot, conn
            else:
                yield document, snapshot
            if document:
                conn.execute(
                    "INSERT INTO project_metadata (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)),
                )

    @contextmanager
    def canonical_pipeline_operation(self, operation_id: str):
        """Serialize one accepted-artifact operation, including its durable result."""
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            key = "canonical_pipeline.v1:" + operation_id
            row = conn.execute("SELECT value FROM project_metadata WHERE key = ?", (key,)).fetchone()
            state = {} if row is None else json.loads(row["value"])
            yield state
            conn.execute("INSERT INTO project_metadata (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, json.dumps(state, sort_keys=True, allow_nan=False)))

    @contextmanager
    def cross_store_operation_transaction(self, operation_id: str):
        """Serialize one durable cross-store operation owned by project.db."""
        operation_id = _normalize_identifier(operation_id, "operation_id")
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            key = "cross_store_operation.v1:" + operation_id
            row = conn.execute(
                "SELECT value FROM project_metadata WHERE key = ?",
                (key,),
            ).fetchone()
            state = {} if row is None else json.loads(str(row["value"]))
            yield state, conn
            if state:
                conn.execute(
                    "INSERT INTO project_metadata (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (
                        key,
                        json.dumps(
                            state,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                    ),
                )

    def get_cross_store_operation(self, operation_id: str) -> dict[str, Any] | None:
        operation_id = _normalize_identifier(operation_id, "operation_id")
        value = self.get_metadata("cross_store_operation.v1:" + operation_id)
        return None if value is None else dict(json.loads(value))

    def list_cross_store_operations(self) -> tuple[dict[str, Any], ...]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT value FROM project_metadata "
                "WHERE key LIKE 'cross_store_operation.v1:%' ORDER BY key"
            ).fetchall()
        return tuple(dict(json.loads(str(row["value"]))) for row in rows)

    def apply_canonical_record_set(self, connection, proposal: dict, snapshot: dict,
                                   *, impact: dict, approval: dict | None) -> dict:
        """Called only under the proposal transaction after final guard validation.

        Version history, current records, invalidation and commit receipt share
        this connection. No filesystem or Series writes participate.
        """
        self.require_scope(StorageScope(proposal["scope_type"], proposal["scope_id"]))
        decision = DomainMutationGuard().evaluate_canonical(
            proposal=proposal, snapshot=snapshot, impact=impact, approval=approval)
        if decision["outcome"] != "ALLOW":
            raise ProjectStorageError("canonical mutation denied: " + decision["reason"])
        current = {(r["record_type"], r["record_id"]): r["payload_json"] for r in snapshot["records"]}
        for mutation in proposal["proposed_mutations"]:
            kind, identity = mutation["target_entity_type"], mutation["target_entity_id"]
            payload = json.dumps(mutation["proposed_state"], sort_keys=True, separators=(",", ":"), allow_nan=False)
            history_key = "canonical_versions.v1:" + kind + ":" + identity
            previous = connection.execute("SELECT value FROM project_metadata WHERE key=?", (history_key,)).fetchone()
            history = [] if previous is None else json.loads(previous["value"])
            if not history and (kind, identity) in current:
                history.append(json.loads(current[(kind, identity)]))
            history.append(mutation["proposed_state"])
            connection.execute("INSERT INTO project_metadata(key,value) VALUES (?,?) "
                               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (history_key, json.dumps(history)))
            connection.execute("INSERT INTO project_structured_memory_records "
                "(scope_type,scope_id,record_type,record_id,payload_json) VALUES (?,?,?,?,?) "
                "ON CONFLICT(scope_type,scope_id,record_type,record_id) DO UPDATE SET payload_json=excluded.payload_json",
                ("PROJECT", self.scope.scope_id, kind, identity, payload))
        return decision

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
        migrations: Iterable[SchemaMigration] | None = None,
        *,
        target_version: int | None = None,
    ) -> SchemaStatus:
        self.context.project_root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.context.project_db_path))
        conn.row_factory = sqlite3.Row
        try:
            return SchemaMigrationRunner(
                target_version=target_version or PROJECT_DB_SCHEMA_VERSION,
                migrations=PROJECT_DB_MIGRATIONS if migrations is None else migrations,
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

    def _decode_edge(self, row: sqlite3.Row) -> EdgeRecord:
        return _decode_scoped_edge(self, row, ProjectStorageError)

    def get_edge(self, edge_id: str) -> EdgeRecord | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? AND edge_id = ?",
                (self.scope.scope_type.value, self.scope.scope_id, edge_id),
            ).fetchone()
            return None if row is None else self._decode_edge(row)

    def list_edges(self) -> tuple[EdgeRecord, ...]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? ORDER BY edge_id",
                (self.scope.scope_type.value, self.scope.scope_id),
            ).fetchall()
            return tuple(self._decode_edge(row) for row in rows)

    @contextmanager
    def _graph_read_connection(self) -> Iterator[sqlite3.Connection]:
        # Analysis must not initialize a database or run a schema migration.
        conn = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            if _read_schema_version(conn) != PROJECT_DB_SCHEMA_VERSION:
                raise ProjectStorageError("graph analysis requires current schema; use controlled migration")
            identity = conn.execute("SELECT project_id, book_id FROM project_identity WHERE id = 1").fetchone()
            if identity is None or identity["project_id"] != self.context.project_id or identity["book_id"] != self.context.book_id:
                raise ProjectStorageError("project.db identity does not match repository context")
            yield conn
        finally:
            conn.close()

    def traverse_dependencies(
        self, start: GraphNodeRef, policy: DependencyTraversalPolicy | None = None,
    ) -> tuple[DependencyTraversalStep, ...]:
        return _traverse_scoped_dependencies(
            self, start, policy, error_type=ProjectStorageError,
            read_connection=self._graph_read_connection,
        )

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

    def list_structured_memory_records(
        self,
        record_type: str | None = None,
    ) -> dict[str, str]:
        self.initialize()
        with self.connect() as conn:
            if record_type is None:
                rows = conn.execute(
                    """
                    SELECT record_id, payload_json
                    FROM project_structured_memory_records
                    ORDER BY record_type, record_id
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT record_id, payload_json
                    FROM project_structured_memory_records
                    WHERE record_type = ?
                    ORDER BY record_id
                    """,
                    (_normalize_domain_id(record_type, "record_type"),),
                ).fetchall()
            return {str(row["record_id"]): str(row["payload_json"]) for row in rows}

    def list_structured_memory_record_scopes(self) -> dict[str, dict[str, str]]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT record_id, record_type, scope_type, scope_id
                FROM project_structured_memory_records
                ORDER BY record_type, record_id
                """
            ).fetchall()
            return {
                str(row["record_id"]): {
                    "record_type": str(row["record_type"]),
                    "scope_type": str(row["scope_type"]),
                    "scope_id": str(row["scope_id"]),
                }
                for row in rows
            }

    @staticmethod
    def _decode_context_package(payload_json: str) -> Any:
        from app.p20_core.context_builder import ContextPackage

        try:
            return ContextPackage.from_dict(json.loads(payload_json))
        except (TypeError, ValueError, KeyError) as exc:
            raise ProjectStorageError("malformed persisted context package") from exc

    def save_context_package(self, package: Any, *, operation_id: str) -> Any:
        from app.p20_core.context_builder import ContextPackage

        if not isinstance(package, ContextPackage):
            raise ProjectStorageError("context package write requires ContextPackage")
        if package.project_id != self.context.project_id:
            raise ProjectStorageError("context package project scope does not match repository")
        if package.book_id != self.context.book_id:
            raise ProjectStorageError("context package book scope does not match repository")
        operation_id = _normalize_domain_id(operation_id, "operation_id")
        self.initialize()
        payload_json = package.to_json()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT payload_json FROM context_packages "
                "WHERE scope_type = ? AND scope_id = ? AND operation_id = ?",
                (ScopeType.PROJECT.value, self.context.project_id, operation_id),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_json"]) != payload_json:
                    raise ProjectStorageError(
                        "context operation identity was already used for a different package"
                    )
                return self._decode_context_package(str(existing["payload_json"]))
            conn.execute(
                """
                INSERT INTO context_packages (
                    scope_type, scope_id, context_package_id, operation_id,
                    run_id, step_id, context_hash, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ScopeType.PROJECT.value,
                    self.context.project_id,
                    package.context_package_id,
                    operation_id,
                    package.run_id,
                    package.step_id,
                    package.context_hash,
                    payload_json,
                ),
            )
        return package

    def get_context_package(self, context_package_id: str) -> Any | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM context_packages "
                "WHERE scope_type = ? AND scope_id = ? AND context_package_id = ?",
                (
                    ScopeType.PROJECT.value,
                    self.context.project_id,
                    _normalize_domain_id(context_package_id, "context_package_id"),
                ),
            ).fetchone()
        return None if row is None else self._decode_context_package(str(row["payload_json"]))

    def get_context_package_for_operation(self, operation_id: str) -> Any | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM context_packages "
                "WHERE scope_type = ? AND scope_id = ? AND operation_id = ?",
                (
                    ScopeType.PROJECT.value,
                    self.context.project_id,
                    _normalize_domain_id(operation_id, "operation_id"),
                ),
            ).fetchone()
        return None if row is None else self._decode_context_package(str(row["payload_json"]))

    def list_context_packages(self) -> tuple[Any, ...]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM context_packages "
                "WHERE scope_type = ? AND scope_id = ? "
                "ORDER BY run_id, step_id, context_package_id",
                (ScopeType.PROJECT.value, self.context.project_id),
            ).fetchall()
        return tuple(
            self._decode_context_package(str(row["payload_json"]))
            for row in rows
        )


class SeriesDomainTransaction:
    def __init__(self, conn: sqlite3.Connection, scope: StorageScope) -> None:
        self._conn = conn
        self._scope = _coerce_storage_scope(scope, SeriesStorageError)

    @property
    def scope(self) -> StorageScope:
        return self._scope

    def add_edge(
        self, record: EdgeRecord, *, source_scope: StorageScope, target_scope: StorageScope,
    ) -> None:
        _insert_scoped_edge(
            self._conn, self.scope, record, source_scope=source_scope,
            target_scope=target_scope, error_type=SeriesStorageError,
        )


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
        conn = sqlite3.connect(
            str(self.context.database_path),
            timeout=_DOMAIN_DB_BUSY_TIMEOUT_MS / 1000,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {_DOMAIN_DB_BUSY_TIMEOUT_MS}")
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
            _create_series_memory_tables(conn)
            _create_series_edges_table(conn)
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

    def _require_access_context(self, access: SeriesAccessContext) -> None:
        if not isinstance(access, SeriesAccessContext):
            raise SeriesAccessError("access must be a SeriesAccessContext")
        access.require_series(self.context.series_id)

    def get_metadata_readonly(
        self, access: SeriesAccessContext, key: str,
    ) -> str | None:
        """Locate SERIES process metadata without initialization or migration."""
        self._require_access_context(access)
        conn = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
            identity = conn.execute(
                "SELECT series_id FROM series_identity WHERE id = 1"
            ).fetchone()
            if identity is None or identity["series_id"] != self.context.series_id:
                raise SeriesStorageError("series.db identity does not match repository context")
            self._require_registered_member(conn, access)
            row = conn.execute(
                "SELECT value FROM series_metadata WHERE key = ?", (str(key),)
            ).fetchone()
            return None if row is None else str(row["value"])
        finally:
            conn.close()

    @contextmanager
    def canonical_proposal_transaction(
        self,
        access: SeriesAccessContext,
        proposal_id: str,
        *,
        include_writer: bool = False,
    ):
        """Serialize one SERIES proposal with its canonical and graph basis."""
        self._require_access_context(access)
        proposal_id = _normalize_identifier(proposal_id, "proposal_id", SeriesStorageError)
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_registered_member(conn, access)
            key = "canonical_proposal.v1:" + proposal_id
            row = conn.execute(
                "SELECT value FROM series_metadata WHERE key = ?", (key,)
            ).fetchone()
            document = {} if row is None else json.loads(str(row["value"]))
            persisted = conn.execute(
                "SELECT record_type, record_id, payload_json FROM series_state_records "
                "WHERE scope_type = ? AND scope_id = ? AND state_kind = ? "
                "ORDER BY record_type, record_id",
                (ScopeType.SERIES.value, self.scope.scope_id, "SERIES_CANON"),
            ).fetchall()
            records = []
            for stored in persisted:
                wrapper = json.loads(str(stored["payload_json"]))
                records.append({
                    "record_type": str(stored["record_type"]),
                    "record_id": str(stored["record_id"]),
                    "payload_json": json.dumps(
                        wrapper["state"], sort_keys=True, separators=(",", ":"), allow_nan=False,
                    ),
                    "series_payload_json": str(stored["payload_json"]),
                })
            edges = [dict(item) for item in conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? ORDER BY edge_id",
                (self.scope.scope_type.value, self.scope.scope_id),
            )]
            snapshot = {"records": records, "edges": edges}
            if include_writer:
                yield document, snapshot, conn
            else:
                yield document, snapshot
            if document:
                conn.execute(
                    "INSERT INTO series_metadata (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)),
                )

    @contextmanager
    def canonical_pipeline_operation(
        self, access: SeriesAccessContext, operation_id: str,
    ):
        """Serialize accepted-artifact processing entirely inside series.db."""
        self._require_access_context(access)
        operation_id = _normalize_identifier(operation_id, "operation_id", SeriesStorageError)
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_registered_member(conn, access)
            key = "canonical_pipeline.v1:" + operation_id
            row = conn.execute(
                "SELECT value FROM series_metadata WHERE key = ?", (key,)
            ).fetchone()
            state = {} if row is None else json.loads(str(row["value"]))
            yield state
            conn.execute(
                "INSERT INTO series_metadata (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)),
            )

    def apply_canonical_record_set(
        self,
        access: SeriesAccessContext,
        connection: sqlite3.Connection,
        proposal: dict,
        snapshot: dict,
        *,
        impact: dict,
        approval: dict | None,
    ) -> dict:
        """Apply SERIES canon, history and guard result in the caller transaction."""
        from app.p20_core.series_memory import SeriesStateKind, SeriesStateRecord

        self._require_access_context(access)
        access.require_project(proposal["project_id"])
        self.require_scope(StorageScope(proposal["scope_type"], proposal["scope_id"]))
        decision = DomainMutationGuard().evaluate_canonical(
            proposal=proposal, snapshot=snapshot, impact=impact, approval=approval,
        )
        if decision["outcome"] != "ALLOW":
            raise SeriesStorageError("canonical mutation denied: " + decision["reason"])
        current = {
            (record["record_type"], record["record_id"]): record
            for record in snapshot["records"]
        }
        for mutation in proposal["proposed_mutations"]:
            kind = mutation["target_entity_type"]
            identity = mutation["target_entity_id"]
            proposed_state = mutation["proposed_state"]
            existing = current.get((kind, identity))
            history_key = "canonical_versions.v1:" + kind + ":" + identity
            previous = connection.execute(
                "SELECT value FROM series_metadata WHERE key = ?", (history_key,)
            ).fetchone()
            history = [] if previous is None else json.loads(str(previous["value"]))
            if not history and existing is not None:
                history.append(json.loads(existing["payload_json"]))
            history.append(proposed_state)
            connection.execute(
                "INSERT INTO series_metadata(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (history_key, json.dumps(history, sort_keys=True, separators=(",", ":"), allow_nan=False)),
            )
            existing_wrapper = (
                None if existing is None else json.loads(existing["series_payload_json"])
            )
            operation_digest = hashlib.sha256(
                f"{proposal['proposal_hash']}|{kind}|{identity}".encode("utf-8")
            ).hexdigest()
            wrapper = SeriesStateRecord(
                series_id=self.scope.scope_id,
                state_kind=SeriesStateKind.CANON,
                source_project_id=proposal["project_id"],
                source_book_id=proposal["book_id"],
                record_type=kind,
                record_id=identity,
                source_version=proposed_state["version"],
                source_ref=proposal["source_artifact_ref"],
                provenance_refs=(
                    proposal["source_artifact_ref"],
                    proposal["extraction_candidate_set_id"],
                    proposal["verification_ref"]["id"],
                ),
                transfer_reason="CANONICAL_CHANGE",
                state=proposed_state,
                operation_id="series-canonical-" + operation_digest,
                version=proposed_state["version"],
                frozen=proposed_state["frozen"],
                author_locked=proposed_state["author_locked"],
                created_at=(
                    proposal["created_at"]
                    if existing_wrapper is None
                    else existing_wrapper["created_at"]
                ),
                updated_at=proposal["created_at"],
            )
            connection.execute(
                "INSERT INTO series_state_records (scope_type, scope_id, state_kind, "
                "record_type, record_id, source_project_id, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(scope_type, scope_id, state_kind, record_type, record_id) "
                "DO UPDATE SET source_project_id=excluded.source_project_id, "
                "payload_json=excluded.payload_json",
                (ScopeType.SERIES.value, self.scope.scope_id, SeriesStateKind.CANON.value,
                 kind, identity, proposal["project_id"], wrapper.to_json()),
            )
        return decision

    @contextmanager
    def domain_transaction(
        self, access: SeriesAccessContext,
    ) -> Iterator[SeriesDomainTransaction]:
        self._require_access_context(access)
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN")
            self._require_registered_member(conn, access)
            tx = SeriesDomainTransaction(conn, self.scope)
            try:
                yield tx
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _decode_edge(self, row: sqlite3.Row) -> EdgeRecord:
        return _decode_scoped_edge(self, row, SeriesStorageError)

    def get_edge(self, access: SeriesAccessContext, edge_id: str) -> EdgeRecord | None:
        self.require_registered_member(access)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? AND edge_id = ?",
                (self.scope.scope_type.value, self.scope.scope_id, edge_id),
            ).fetchone()
        return None if row is None else self._decode_edge(row)

    def list_edges(self, access: SeriesAccessContext) -> tuple[EdgeRecord, ...]:
        self.require_registered_member(access)
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM edges WHERE scope_type = ? AND scope_id = ? ORDER BY edge_id",
                (self.scope.scope_type.value, self.scope.scope_id),
            ).fetchall()
        return tuple(self._decode_edge(row) for row in rows)

    @contextmanager
    def _graph_read_connection(
        self, access: SeriesAccessContext,
    ) -> Iterator[sqlite3.Connection]:
        self._require_access_context(access)
        conn = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            if _read_schema_version(conn) != SERIES_DB_SCHEMA_VERSION:
                raise SeriesStorageError(
                    "graph analysis requires current schema; use controlled migration"
                )
            identity = conn.execute(
                "SELECT series_id FROM series_identity WHERE id = 1"
            ).fetchone()
            if identity is None or identity["series_id"] != self.context.series_id:
                raise SeriesStorageError("series.db identity does not match repository context")
            self._require_registered_member(conn, access)
            yield conn
        finally:
            conn.close()

    def traverse_dependencies(
        self,
        access: SeriesAccessContext,
        start: GraphNodeRef,
        policy: DependencyTraversalPolicy | None = None,
    ) -> tuple[DependencyTraversalStep, ...]:
        self._require_access_context(access)
        if policy is None or not getattr(policy, "read_only", False):
            self.require_registered_member(access)
        return _traverse_scoped_dependencies(
            self, start, policy, error_type=SeriesStorageError,
            read_connection=lambda: self._graph_read_connection(access),
        )

    def register_member(self, access: SeriesAccessContext, membership: Any) -> Any:
        from app.p20_core.series_memory import SeriesMembershipRecord

        self._require_access_context(access)
        if not isinstance(membership, SeriesMembershipRecord):
            raise SeriesStorageError("membership must be a SeriesMembershipRecord")
        membership.require_access(access)
        self.initialize()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT payload_json FROM series_memberships WHERE project_id = ?",
                (access.project_id,),
            ).fetchone()
            payload_json = membership.to_json()
            if existing is not None:
                if str(existing["payload_json"]) != payload_json:
                    raise SeriesStorageError("series membership identity cannot be changed")
                return membership
            conn.execute(
                "INSERT INTO series_memberships (project_id, book_id, payload_json) "
                "VALUES (?, ?, ?)",
                (access.project_id, str(membership.book_id), payload_json),
            )
        return membership

    def _require_registered_member(
        self,
        conn: sqlite3.Connection,
        access: SeriesAccessContext,
    ) -> Any:
        from app.p20_core.series_memory import SeriesMembershipRecord

        self._require_access_context(access)
        row = conn.execute(
            "SELECT payload_json FROM series_memberships WHERE project_id = ?",
            (access.project_id,),
        ).fetchone()
        if row is None:
            raise SeriesAccessError("project is not a registered member of the series")
        membership = SeriesMembershipRecord(**json.loads(str(row["payload_json"])))
        membership.require_access(access)
        return membership

    def require_registered_member(
        self,
        access: SeriesAccessContext,
        *,
        book_id: str | None = None,
    ) -> Any:
        self.initialize()
        with self.connect() as conn:
            membership = self._require_registered_member(conn, access)
        if book_id is not None and str(membership.book_id) != str(book_id):
            raise SeriesAccessError("series membership book does not match requested book")
        return membership

    @staticmethod
    def _read_operation(
        conn: sqlite3.Connection,
        *,
        operation_id: str,
        semantic_hash: str,
        result_type: str,
    ) -> str | None:
        row = conn.execute(
            "SELECT semantic_hash, result_type, result_payload_json "
            "FROM series_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        if (
            str(row["semantic_hash"]) != semantic_hash
            or str(row["result_type"]) != result_type
        ):
            raise SeriesStorageError("operation identity was already used for different input")
        return str(row["result_payload_json"])

    @staticmethod
    def _record_operation(
        conn: sqlite3.Connection,
        *,
        operation_id: str,
        semantic_hash: str,
        result_type: str,
        result_id: str,
        result_payload_json: str,
    ) -> None:
        conn.execute(
            "INSERT INTO series_operations (operation_id, semantic_hash, result_type, "
            "result_id, result_payload_json) VALUES (?, ?, ?, ?, ?)",
            (operation_id, semantic_hash, result_type, result_id, result_payload_json),
        )

    def _upsert_series_state_record(
        self,
        conn: sqlite3.Connection,
        *,
        access: SeriesAccessContext,
        record: Any,
        mutation_source: MutationSource | str,
        actor_id: str | None,
        mutation_policy: MutationPolicy,
    ) -> None:
        from app.p20_core.series_memory import SeriesStateRecord

        if not isinstance(record, SeriesStateRecord):
            raise SeriesStorageError("series state write requires a SeriesStateRecord")
        record.require_access(access)
        _require_matching_scope(self.scope, record.scope, SeriesStorageError)
        existing = conn.execute(
            "SELECT payload_json FROM series_state_records "
            "WHERE scope_type = ? AND scope_id = ? AND state_kind = ? "
            "AND record_type = ? AND record_id = ?",
            (
                ScopeType.SERIES.value,
                self.context.series_id,
                record.state_kind.value,
                record.record_type,
                str(record.record_id),
            ),
        ).fetchone()
        decision = DomainMutationGuard(mutation_policy).evaluate(
            current_payload=None if existing is None else str(existing["payload_json"]),
            proposed_payload=record.to_json(),
            context=MutationContext(
                project_id=access.project_id,
                record_type=record.record_type,
                record_id=str(record.record_id),
                mutation_type=MutationType.CREATE if existing is None else MutationType.UPDATE,
                source=mutation_source,
                actor_id=actor_id,
            ),
        )
        if decision.denied:
            raise SeriesStorageError(
                f"domain mutation denied: {decision.reason_code.value}"
            )
        conn.execute(
            """
            INSERT INTO series_state_records (
                scope_type, scope_id, state_kind, record_type, record_id,
                source_project_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_type, scope_id, state_kind, record_type, record_id)
            DO UPDATE SET
                source_project_id = excluded.source_project_id,
                payload_json = excluded.payload_json
            """,
            (
                ScopeType.SERIES.value,
                self.context.series_id,
                record.state_kind.value,
                record.record_type,
                str(record.record_id),
                str(record.source_project_id),
                record.to_json(),
            ),
        )

    def _save_series_state(
        self,
        access: SeriesAccessContext,
        record: Any,
        *,
        expected_kind: Any,
        mutation_source: MutationSource | str,
        actor_id: str | None,
        mutation_policy: MutationPolicy,
    ) -> Any:
        from app.p20_core.series_memory import SeriesStateRecord

        self._require_access_context(access)
        if not isinstance(record, SeriesStateRecord):
            raise SeriesStorageError("series state write requires a SeriesStateRecord")
        if record.state_kind != expected_kind:
            raise SeriesStorageError(f"record must use {expected_kind.value} state kind")
        record.require_access(access)
        self.initialize()
        with self.connect() as conn:
            self._require_registered_member(conn, access)
            replay = self._read_operation(
                conn,
                operation_id=record.operation_id,
                semantic_hash=record.semantic_identity,
                result_type=record.state_kind.value,
            )
            if replay is not None:
                return SeriesStateRecord(**json.loads(replay))
            self._upsert_series_state_record(
                conn,
                access=access,
                record=record,
                mutation_source=mutation_source,
                actor_id=actor_id,
                mutation_policy=mutation_policy,
            )
            self._record_operation(
                conn,
                operation_id=record.operation_id,
                semantic_hash=record.semantic_identity,
                result_type=record.state_kind.value,
                result_id=str(record.record_id),
                result_payload_json=record.to_json(),
            )
        return record

    def save_series_canon(
        self,
        access: SeriesAccessContext,
        record: Any,
        *,
        mutation_source: MutationSource | str = MutationSource.AUTHOR,
        actor_id: str | None = None,
        mutation_policy: MutationPolicy = DEFAULT_MUTATION_POLICY,
    ) -> Any:
        from app.p20_core.series_memory import SeriesStateKind

        return self._save_series_state(
            access,
            record,
            expected_kind=SeriesStateKind.CANON,
            mutation_source=mutation_source,
            actor_id=actor_id,
            mutation_policy=mutation_policy,
        )

    def save_series_memory(
        self,
        access: SeriesAccessContext,
        record: Any,
        *,
        mutation_source: MutationSource | str = MutationSource.AUTOMATION,
        actor_id: str | None = None,
        mutation_policy: MutationPolicy = DEFAULT_MUTATION_POLICY,
    ) -> Any:
        from app.p20_core.series_memory import SeriesStateKind

        return self._save_series_state(
            access,
            record,
            expected_kind=SeriesStateKind.MEMORY,
            mutation_source=mutation_source,
            actor_id=actor_id,
            mutation_policy=mutation_policy,
        )

    def _list_series_state(
        self,
        access: SeriesAccessContext,
        state_kind: Any,
    ) -> tuple[Any, ...]:
        from app.p20_core.series_memory import SeriesStateRecord

        self.initialize()
        with self.connect() as conn:
            self._require_registered_member(conn, access)
            rows = conn.execute(
                "SELECT payload_json FROM series_state_records "
                "WHERE scope_type = ? AND scope_id = ? AND state_kind = ? "
                "ORDER BY record_type, record_id",
                (ScopeType.SERIES.value, self.context.series_id, state_kind.value),
            ).fetchall()
        return tuple(
            SeriesStateRecord(**json.loads(str(row["payload_json"])))
            for row in rows
        )

    def list_series_canon(self, access: SeriesAccessContext) -> tuple[Any, ...]:
        from app.p20_core.series_memory import SeriesStateKind

        return self._list_series_state(access, SeriesStateKind.CANON)

    def list_series_memory(self, access: SeriesAccessContext) -> tuple[Any, ...]:
        from app.p20_core.series_memory import SeriesStateKind

        return self._list_series_state(access, SeriesStateKind.MEMORY)

    def close_volume(
        self,
        access: SeriesAccessContext,
        snapshot: Any,
        *,
        mutation_source: MutationSource | str = MutationSource.AUTOMATION,
        actor_id: str | None = None,
        mutation_policy: MutationPolicy = DEFAULT_MUTATION_POLICY,
    ) -> Any:
        from app.p20_core.series_memory import (
            VolumeClosingSnapshot,
            VolumeTransferTarget,
            series_state_from_snapshot_item,
        )

        self._require_access_context(access)
        if not isinstance(snapshot, VolumeClosingSnapshot):
            raise SeriesStorageError("close_volume requires a VolumeClosingSnapshot")
        snapshot.require_access(access)
        _require_matching_scope(self.scope, snapshot.scope, SeriesStorageError)
        self.initialize()
        with self.connect() as conn:
            membership = self._require_registered_member(conn, access)
            if str(membership.book_id) != str(snapshot.book_id):
                raise SeriesAccessError("snapshot book is not bound to the project membership")
            replay = self._read_operation(
                conn,
                operation_id=snapshot.operation_id,
                semantic_hash=snapshot.semantic_identity,
                result_type="VOLUME_CLOSING_SNAPSHOT",
            )
            if replay is not None:
                return VolumeClosingSnapshot(**json.loads(replay))

            duplicate = conn.execute(
                "SELECT payload_json FROM volume_closing_snapshots "
                "WHERE scope_type = ? AND scope_id = ? AND project_id = ? "
                "AND book_id = ? AND semantic_hash = ?",
                (
                    ScopeType.SERIES.value,
                    self.context.series_id,
                    access.project_id,
                    str(snapshot.book_id),
                    snapshot.semantic_identity,
                ),
            ).fetchone()
            if duplicate is not None:
                existing = VolumeClosingSnapshot(**json.loads(str(duplicate["payload_json"])))
                self._record_operation(
                    conn,
                    operation_id=snapshot.operation_id,
                    semantic_hash=snapshot.semantic_identity,
                    result_type="VOLUME_CLOSING_SNAPSHOT",
                    result_id=existing.snapshot_id,
                    result_payload_json=existing.to_json(),
                )
                return existing

            conflicting = conn.execute(
                "SELECT semantic_hash FROM volume_closing_snapshots WHERE snapshot_id = ?",
                (snapshot.snapshot_id,),
            ).fetchone()
            if conflicting is not None:
                raise SeriesStorageError("snapshot_id was already used for different input")

            conn.execute(
                "INSERT INTO volume_closing_snapshots (snapshot_id, scope_type, scope_id, "
                "project_id, book_id, source_state_version, semantic_hash, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot.snapshot_id,
                    ScopeType.SERIES.value,
                    self.context.series_id,
                    access.project_id,
                    str(snapshot.book_id),
                    snapshot.source_state_version,
                    snapshot.semantic_identity,
                    snapshot.to_json(),
                ),
            )
            for item in snapshot.items:
                if item.transfer_target == VolumeTransferTarget.SNAPSHOT_ONLY:
                    continue
                self._upsert_series_state_record(
                    conn,
                    access=access,
                    record=series_state_from_snapshot_item(snapshot, item),
                    mutation_source=mutation_source,
                    actor_id=actor_id,
                    mutation_policy=mutation_policy,
                )
            self._record_operation(
                conn,
                operation_id=snapshot.operation_id,
                semantic_hash=snapshot.semantic_identity,
                result_type="VOLUME_CLOSING_SNAPSHOT",
                result_id=snapshot.snapshot_id,
                result_payload_json=snapshot.to_json(),
            )
        return snapshot

    def get_latest_volume_snapshot(
        self,
        access: SeriesAccessContext,
        *,
        book_id: str | None = None,
    ) -> Any | None:
        from app.p20_core.series_memory import VolumeClosingSnapshot

        self.initialize()
        with self.connect() as conn:
            self._require_registered_member(conn, access)
            params: list[Any] = [ScopeType.SERIES.value, self.context.series_id]
            where = "scope_type = ? AND scope_id = ?"
            if book_id is not None:
                where += " AND book_id = ?"
                params.append(str(book_id))
            row = conn.execute(
                f"SELECT payload_json FROM volume_closing_snapshots WHERE {where} "
                "ORDER BY source_state_version DESC, snapshot_id DESC LIMIT 1",
                tuple(params),
            ).fetchone()
        if row is None:
            return None
        return VolumeClosingSnapshot(**json.loads(str(row["payload_json"])))

    def list_volume_snapshots(self, access: SeriesAccessContext) -> tuple[Any, ...]:
        from app.p20_core.series_memory import VolumeClosingSnapshot

        self.initialize()
        with self.connect() as conn:
            self._require_registered_member(conn, access)
            rows = conn.execute(
                "SELECT payload_json FROM volume_closing_snapshots "
                "WHERE scope_type = ? AND scope_id = ? "
                "ORDER BY source_state_version, snapshot_id",
                (ScopeType.SERIES.value, self.context.series_id),
            ).fetchall()
        return tuple(
            VolumeClosingSnapshot(**json.loads(str(row["payload_json"])))
            for row in rows
        )

    def get_opening_state(self, access: SeriesAccessContext) -> Any:
        from app.p20_core.series_memory import SeriesOpeningState

        return SeriesOpeningState(
            series_id=self.context.series_id,
            project_id=access.project_id,
            series_canon=self.list_series_canon(access),
            series_memory=self.list_series_memory(access),
            latest_snapshot=self.get_latest_volume_snapshot(access),
        )

    def get_pragma(self, name: str) -> str | int:
        with self.connect() as conn:
            row = conn.execute(f"PRAGMA {name}").fetchone()
            return row[0]

    def inspect_schema(self) -> SchemaStatus:
        return _inspect_database_schema(self.context.database_path, SERIES_DB_SCHEMA_VERSION)

    def migrate_schema(
        self,
        migrations: Iterable[SchemaMigration] = SERIES_DB_MIGRATIONS,
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
        conn = sqlite3.connect(
            str(self.context.database_path),
            timeout=_SYSTEM_DB_BUSY_TIMEOUT_MS / 1000,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {_SYSTEM_DB_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            # Concurrent first opens can race when changing journal mode, which
            # may return SQLITE_BUSY immediately despite busy_timeout. No domain
            # transaction has begun: retry only this idempotent initialization.
            import time
            deadline = time.monotonic() + _SYSTEM_DB_BUSY_TIMEOUT_MS / 1000
            while True:
                try:
                    conn.execute("PRAGMA journal_mode = WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_BUSY or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
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

    @contextmanager
    def local_operator_transaction(self):
        """Hold credential/registry stable through an operator decision.

        A decision only reads system state and writes its project DB. This is
        not a cross-store write transaction or recovery protocol.
        """
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            key = "local_operator.v1"
            row = conn.execute("SELECT value FROM system_metadata WHERE key = ?", (key,)).fetchone()
            state = {} if row is None else json.loads(row["value"])
            original = json.dumps(state, sort_keys=True)
            registry = self._read_project_registry(conn)
            yield state, registry
            if json.dumps(state, sort_keys=True) != original:
                conn.execute(
                    "INSERT INTO system_metadata (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(state, sort_keys=True, separators=(",", ":"))),
                )

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

    @staticmethod
    def _decode_project_registry(raw: str | None) -> dict[str, str]:
        if raw is None:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise SystemStorageError("project registry metadata is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise SystemStorageError("project registry metadata must be an object")

        registry: dict[str, str] = {}
        for project_id, book_id in payload.items():
            normalized_project = _normalize_identifier(
                project_id,
                "project_id",
                SystemStorageError,
            )
            normalized_book = _normalize_identifier(
                book_id,
                "book_id",
                SystemStorageError,
            )
            registry[normalized_project] = normalized_book
        if len(set(registry.values())) != len(registry):
            raise SystemStorageError("project registry contains ambiguous book bindings")
        return registry

    @staticmethod
    def _read_project_registry(conn: sqlite3.Connection) -> dict[str, str]:
        row = conn.execute(
            "SELECT value FROM system_metadata WHERE key = ?",
            (PROJECT_REGISTRY_METADATA_KEY,),
        ).fetchone()
        raw = None if row is None else str(row["value"])
        return SystemRepository._decode_project_registry(raw)

    @staticmethod
    def _write_project_registry(
        conn: sqlite3.Connection,
        registry: dict[str, str],
    ) -> None:
        payload = json.dumps(registry, sort_keys=True, separators=(",", ":"))
        conn.execute(
            """
            INSERT INTO system_metadata (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (PROJECT_REGISTRY_METADATA_KEY, payload),
        )

    def list_project_bindings(self) -> dict[str, str]:
        self.initialize()
        with self.connect() as conn:
            return self._read_project_registry(conn)

    def resolve_project_id_for_book(self, book_id: str) -> str | None:
        normalized_book = _normalize_identifier(book_id, "book_id", SystemStorageError)
        matches = [
            project_id
            for project_id, bound_book_id in self.list_project_bindings().items()
            if bound_book_id == normalized_book
        ]
        if len(matches) > 1:
            raise SystemStorageError("project registry contains ambiguous book bindings")
        return matches[0] if matches else None

    def resolve_book_id_for_project(self, project_id: str) -> str | None:
        normalized_project = _normalize_identifier(
            project_id,
            "project_id",
            SystemStorageError,
        )
        return self.list_project_bindings().get(normalized_project)

    def bind_project(self, project_id: str, book_id: str) -> None:
        normalized_project = _normalize_identifier(
            project_id,
            "project_id",
            SystemStorageError,
        )
        normalized_book = _normalize_identifier(book_id, "book_id", SystemStorageError)
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            registry = self._read_project_registry(conn)
            existing_book = registry.get(normalized_project)
            if existing_book is not None and existing_book != normalized_book:
                raise SystemStorageError("project_id is already bound to a different book")
            existing_project = next(
                (
                    bound_project
                    for bound_project, bound_book in registry.items()
                    if bound_book == normalized_book
                ),
                None,
            )
            if existing_project is not None and existing_project != normalized_project:
                raise SystemStorageError("book_id is already bound to a different project")
            registry[normalized_project] = normalized_book
            self._write_project_registry(conn, registry)

    def resolve_or_bind_project(self, project_id: str, book_id: str) -> str:
        normalized_project = _normalize_identifier(
            project_id,
            "project_id",
            SystemStorageError,
        )
        normalized_book = _normalize_identifier(book_id, "book_id", SystemStorageError)
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            registry = self._read_project_registry(conn)
            existing_project = next(
                (
                    bound_project
                    for bound_project, bound_book in registry.items()
                    if bound_book == normalized_book
                ),
                None,
            )
            if existing_project is not None:
                return existing_project
            if normalized_project in registry:
                raise SystemStorageError("project_id is already bound to a different book")
            registry[normalized_project] = normalized_book
            self._write_project_registry(conn, registry)
            return normalized_project

    def discover_project_bindings(self, book_id: str) -> tuple[str, ...]:
        normalized_book = _normalize_identifier(book_id, "book_id", SystemStorageError)
        projects_root = self.context.storage_root / "projects"
        if not projects_root.exists():
            return ()

        matches: list[str] = []
        for database_path in sorted(projects_root.glob(f"*/{PROJECT_DB_FILENAME}")):
            try:
                conn = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                try:
                    if not _table_exists(conn, "project_identity"):
                        continue
                    row = conn.execute(
                        "SELECT project_id, book_id FROM project_identity WHERE id = 1"
                    ).fetchone()
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                raise SystemStorageError(
                    f"cannot inspect project identity: {database_path}"
                ) from exc
            if row is not None and str(row["book_id"]) == normalized_book:
                matches.append(str(row["project_id"]))
        return tuple(sorted(set(matches)))

    def discover_series_memberships(
        self,
        project_id: str,
    ) -> tuple[tuple[str, str], ...]:
        normalized_project = _normalize_identifier(
            project_id,
            "project_id",
            SystemStorageError,
        )
        series_root = self.context.storage_root / "series"
        if not series_root.exists():
            return ()

        memberships: list[tuple[str, str]] = []
        for database_path in sorted(series_root.glob(f"*/{SERIES_DB_FILENAME}")):
            try:
                conn = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                try:
                    if not _table_exists(conn, "series_identity") or not _table_exists(
                        conn, "series_memberships"
                    ):
                        continue
                    identity = conn.execute(
                        "SELECT series_id FROM series_identity WHERE id = 1"
                    ).fetchone()
                    membership = conn.execute(
                        "SELECT book_id FROM series_memberships WHERE project_id = ?",
                        (normalized_project,),
                    ).fetchone()
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                raise SystemStorageError(
                    f"cannot inspect series membership: {database_path}"
                ) from exc
            if identity is not None and membership is not None:
                memberships.append(
                    (str(identity["series_id"]), str(membership["book_id"]))
                )
        return tuple(sorted(set(memberships)))

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
    "PROJECT_DB_MIGRATIONS",
    "PROJECT_DB_SCHEMA_VERSION",
    "SERIES_DB_FILENAME",
    "SERIES_DB_MIGRATIONS",
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

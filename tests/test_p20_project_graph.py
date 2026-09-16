from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from app.p20_core.domain_records import (
    DomainContractError, DomainId, DomainNamespace, EdgeRecord, EdgeRelationType,
)
from app.p20_core.project_graph import (
    DependencyTraversalPolicy, GraphNodeRef, GraphTraversalLimitError,
)
from app.p20_core.project_repository import (
    PROJECT_DB_MIGRATIONS, PROJECT_DB_SCHEMA_VERSION, ProjectRepository,
    ProjectStorageError, SchemaMigration, SERIES_DB_SCHEMA_VERSION,
    SeriesAccessContext, SeriesAccessError, SeriesRepository, StorageResolver,
    StorageScope, SystemRepository,
)
from app.p20_core.series_memory import SeriesMembershipRecord


@pytest.fixture
def repo(isolated_agentpro_storage):
    return ProjectRepository(StorageResolver().resolve_project("PROJ-graph"))


def edge(edge_id="dependency-1", source="EVENT-a", target="EVENT-b", **changes):
    data = dict(
        edge_id=edge_id, scope_type="PROJECT", scope_id="PROJ-graph",
        source_type=DomainId.parse(source).namespace.name, source_id=source,
        target_type=DomainId.parse(target).namespace.name, target_id=target,
        relation_type="DEPENDS_ON", valid_from="001", valid_to=None,
        confidence=1.0, source_ref="SCENE-001#artifact-1:p2", version=1,
    )
    data.update(changes)
    return EdgeRecord(**data)


def write(repo, *records):
    with repo.domain_transaction() as tx:
        for record in records:
            tx.add_edge(record, source_scope=repo.scope, target_scope=repo.scope)


def traverse(repo, node="EVENT-a", **policy):
    return repo.traverse_dependencies(
        GraphNodeRef(repo.scope, node), DependencyTraversalPolicy(**policy),
    )


def ids(result):
    return [(step.edge.edge_id, step.depth, str(step.to_node)) for step in result]


def series_edge(edge_id="series-dependency-1", source="EVENT-series-a",
                target="EVENT-series-b", series_id="SERIES-graph"):
    return EdgeRecord(
        edge_id=edge_id, scope_type="SERIES", scope_id=series_id,
        source_type="EVENT", source_id=source, target_type="EVENT", target_id=target,
        relation_type="DEPENDS_ON", valid_from="001", valid_to=None,
        confidence=1, source_ref="synthetic:series-graph", version=1,
    )


def registered_series(series_id="SERIES-graph", project_id="PROJ-series-member",
                      book_id="BOOK-series-member"):
    repository = SeriesRepository(StorageResolver().resolve_series(series_id))
    repository.initialize()
    access = SeriesAccessContext.bind(project_id, series_id)
    repository.register_member(access, SeriesMembershipRecord(
        series_id=series_id, project_id=project_id, book_id=book_id,
        source_ref="synthetic:series-membership", version=1,
        created_at="2026-09-16T00:00:00Z",
    ))
    return repository, access


def test_edge_contract_stable_ids_provenance_and_canonical_serialization():
    record = edge()
    assert isinstance(record.source_id, DomainId)
    assert record.scope == StorageScope.project("PROJ-graph")
    assert record.source_ref == "SCENE-001#artifact-1:p2"
    assert record.to_json() == EdgeRecord(**json.loads(record.to_json())).to_json()
    assert record.to_json() == json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"))
    revised = replace(record, version=2, source_ref="SCENE-001#artifact-2:p2")
    assert revised.edge_id == record.edge_id
    assert (revised.source_id, revised.target_id) == (record.source_id, record.target_id)
    assert "EDGE" not in DomainNamespace.__members__


@pytest.mark.parametrize("changes", [
    {"source_id": ""}, {"target_id": ""}, {"source_id": "Ada"},
    {"target_id": "EVENT-../escape"}, {"source_id": "EVENT-.."},
    {"source_type": "FACT"}, {"target_type": "SCENE"},
    {"edge_id": "../edge"}, {"edge_id": ""},
    {"scope_type": "SYSTEM"}, {"relation_type": "INVENTED"},
    {"valid_from": "002", "valid_to": "001"},
    {"confidence": float("nan")}, {"confidence": 1.1},
    {"source_ref": ""}, {"version": 0},
])
def test_invalid_edge_rejected(changes):
    with pytest.raises(ValueError):
        edge(**changes)


def test_architecture_relations_and_nullable_source_ref():
    assert edge(relation_type="PART_OF", source_ref=None).relation_type == EdgeRelationType.PART_OF
    assert edge(source="CHAR-ada", source_type="CHAR").source_type == "CHARACTER"


def test_project_endpoint_cannot_reference_other_project():
    with pytest.raises(DomainContractError, match="cross-project"):
        edge(target="PROJ-other")


def test_repository_roundtrip_and_no_overwrite(repo, isolated_agentpro_storage):
    write(repo, edge())
    assert repo.get_edge("dependency-1") == edge()
    assert repo.get_edge("missing") is None
    assert repo.list_edges() == (edge(),)
    assert repo.db_path == isolated_agentpro_storage / "projects" / "PROJ-graph" / "project.db"
    with pytest.raises(sqlite3.IntegrityError):
        write(repo, edge(target="EVENT-c", version=2))
    assert repo.get_edge("dependency-1") == edge()


def test_series_edge_persistence_traversal_reopen_and_rollback(isolated_agentpro_storage):
    repository, access = registered_series()
    first = series_edge()
    second = series_edge("series-dependency-2", "EVENT-series-b", "EVENT-series-c")
    with repository.domain_transaction(access) as tx:
        tx.add_edge(first, source_scope=repository.scope, target_scope=repository.scope)
        tx.add_edge(second, source_scope=repository.scope, target_scope=repository.scope)
    reopened = SeriesRepository(StorageResolver().resolve_series("SERIES-graph"))
    assert reopened.list_edges(access) == (first, second)
    traversal = reopened.traverse_dependencies(
        access, GraphNodeRef(reopened.scope, "EVENT-series-a"),
        DependencyTraversalPolicy(max_depth=2),
    )
    assert ids(traversal) == [
        ("series-dependency-1", 1, "EVENT-series-b"),
        ("series-dependency-2", 2, "EVENT-series-c"),
    ]
    with pytest.raises(RuntimeError, match="abort"):
        with reopened.domain_transaction(access) as tx:
            tx.add_edge(
                series_edge("series-rolled-back", "EVENT-series-c", "EVENT-series-d"),
                source_scope=reopened.scope, target_scope=reopened.scope,
            )
            raise RuntimeError("abort")
    assert reopened.get_edge(access, "series-rolled-back") is None


def test_series_graph_access_and_cross_series_isolation(isolated_agentpro_storage):
    first, access = registered_series("SERIES-graph-a")
    second, second_access = registered_series("SERIES-graph-b")
    with first.domain_transaction(access) as tx:
        tx.add_edge(
            series_edge(series_id="SERIES-graph-a"),
            source_scope=first.scope, target_scope=first.scope,
        )
    assert second.list_edges(second_access) == ()
    with pytest.raises(SeriesAccessError):
        first.list_edges(second_access)
    with pytest.raises(SeriesAccessError):
        first.traverse_dependencies(
            second_access, GraphNodeRef(second.scope, "EVENT-series-a")
        )


def test_controlled_series_v2_to_v3_graph_migration_preserves_membership(isolated_agentpro_storage):
    repository, access = registered_series("SERIES-graph-migration")
    with repository.connect() as conn:
        conn.execute("DROP TABLE edges")
        conn.execute("UPDATE schema_version SET version=2")
        conn.execute("UPDATE series_identity SET schema_version=2")
    assert repository.inspect_schema().migration_needed
    with pytest.raises(ValueError, match="controlled migration"):
        repository.list_edges(access)
    status = repository.migrate_schema()
    assert status.current_version == SERIES_DB_SCHEMA_VERSION == 3
    assert repository.require_registered_member(access).project_id.value == access.project_id
    with repository.domain_transaction(access) as tx:
        tx.add_edge(
            series_edge(series_id="SERIES-graph-migration"),
            source_scope=repository.scope, target_scope=repository.scope,
        )
    assert len(repository.list_edges(access)) == 1


@pytest.mark.parametrize("foreign", ["record", "source", "target"])
def test_cross_project_edge_write_rejected(repo, foreign):
    other = StorageScope.project("PROJ-other")
    with pytest.raises(ProjectStorageError, match="scope"):
        with repo.domain_transaction() as tx:
            tx.add_edge(
                edge(scope_id=other.scope_id) if foreign == "record" else edge(),
                source_scope=other if foreign == "source" else repo.scope,
                target_scope=other if foreign == "target" else repo.scope,
            )
    assert repo.list_edges() == ()


def test_cross_project_traversal_rejected(repo):
    with pytest.raises(ProjectStorageError, match="scope"):
        repo.traverse_dependencies(GraphNodeRef(StorageScope.project("PROJ-other"), "EVENT-a"))
    with pytest.raises(ProjectStorageError, match="scoped"):
        repo.traverse_dependencies("EVENT-a")


def test_graph_node_requires_valid_scoped_id():
    assert GraphNodeRef(StorageScope.series("SERIES-one"), "EVENT-a").scope == StorageScope.series("SERIES-one")
    with pytest.raises(DomainContractError):
        GraphNodeRef(StorageScope.project("PROJ-one"), "EVENT-../a")
    with pytest.raises(DomainContractError):
        GraphNodeRef(StorageScope.project("PROJ-one"), "PROJ-other")
    with pytest.raises(DomainContractError):
        GraphNodeRef(StorageScope.series("SERIES-one"), "SERIES-other")


def test_whole_domain_transaction_rolls_back(repo):
    with pytest.raises(RuntimeError, match="abort"):
        with repo.domain_transaction() as tx:
            tx.add_fact_record("FACT-probe", "{}")
            tx.add_edge(edge(), source_scope=repo.scope, target_scope=repo.scope)
            raise RuntimeError("abort")
    assert repo.list_edges() == ()
    assert repo.list_fact_records() == {}


def test_direct_multihop_depth_and_cycle_safety(repo):
    write(repo, edge("1"), edge("2", "EVENT-b", "EVENT-c"), edge("3", "EVENT-c", "EVENT-a"))
    assert traverse(repo, max_depth=0) == ()
    assert ids(traverse(repo, max_depth=1)) == [("1", 1, "EVENT-b")]
    assert ids(traverse(repo, max_depth=2)) == [("1", 1, "EVENT-b"), ("2", 2, "EVENT-c")]
    assert ids(traverse(repo, max_depth=100)) == [
        ("1", 1, "EVENT-b"), ("2", 2, "EVENT-c"), ("3", 3, "EVENT-a"),
    ]


def test_self_edge_is_allowed_and_finite(repo):
    write(repo, edge(source="EVENT-a", target="EVENT-a"))
    assert ids(traverse(repo, max_depth=100)) == [("dependency-1", 1, "EVENT-a")]


def test_deterministic_breadth_first_order_independent_of_insertion(repo):
    records = [edge("c", "EVENT-b", "EVENT-d"), edge("b", target="EVENT-c"), edge("a")]
    other = ProjectRepository(StorageResolver().resolve_project("PROJ-other"))
    write(repo, *records)
    write(other, *(replace(e, scope_id=other.scope.scope_id) for e in reversed(records)))
    expected = [("a", 1, "EVENT-b"), ("b", 1, "EVENT-c"), ("c", 2, "EVENT-d")]
    assert ids(traverse(repo)) == ids(traverse(other)) == expected
    assert traverse(repo) == traverse(repo)


def test_relation_filter_applies_to_each_hop(repo):
    write(repo, edge("1"), edge("2", "EVENT-b", "EVENT-c", relation_type="CAUSES"))
    assert ids(traverse(repo, relation_types=("DEPENDS_ON",))) == [("1", 1, "EVENT-b")]
    assert traverse(repo, relation_types=()) == ()
    assert ids(traverse(repo, relation_types=("CAUSES", "DEPENDS_ON"))) == [
        ("1", 1, "EVENT-b"), ("2", 2, "EVENT-c"),
    ]


def test_incoming_and_both_directions(repo):
    write(repo, edge("1"), edge("2", "EVENT-c", "EVENT-b"))
    assert ids(traverse(repo, "EVENT-b", direction="INCOMING")) == [
        ("1", 1, "EVENT-a"), ("2", 1, "EVENT-c"),
    ]
    assert ids(traverse(repo, direction="BOTH")) == [("1", 1, "EVENT-b"), ("2", 2, "EVENT-c")]


def test_work_bound_raises_instead_of_returning_incomplete_result(repo):
    write(repo, edge("1"), edge("2", target="EVENT-c"))
    with pytest.raises(GraphTraversalLimitError, match="max_edges"):
        traverse(repo, max_edges=1)


@pytest.mark.parametrize("policy", [
    {"max_depth": -1}, {"max_depth": True}, {"max_edges": 0},
    {"direction": "SIDEWAYS"}, {"relation_types": "CAUSES"},
    {"relation_types": ("INVENTED",)},
])
def test_invalid_traversal_policy(policy):
    with pytest.raises(DomainContractError):
        DependencyTraversalPolicy(**policy)


@pytest.mark.parametrize("corruption", [
    '{"source_id":"unsafe"}', "not json",
    edge(target="EVENT-c").to_json(), edge(scope_id="PROJ-other").to_json(),
])
def test_malformed_stored_graph_fails_closed(repo, corruption):
    write(repo, edge())
    with repo.connect() as conn:
        conn.execute("UPDATE edges SET payload_json = ?", (corruption,))
    with pytest.raises(ProjectStorageError, match="malformed"):
        traverse(repo)
    with pytest.raises(ProjectStorageError, match="malformed"):
        repo.get_edge("dependency-1")


def test_project_isolation_and_other_stores_unchanged(repo):
    resolver = StorageResolver()
    other = ProjectRepository(resolver.resolve_project("PROJ-other"))
    series = SeriesRepository(resolver.resolve_series("SERIES-graph"))
    system = SystemRepository(resolver.resolve_system())
    series.initialize()
    system.initialize()
    other.initialize()
    before = {r.db_path: r.db_path.read_bytes() for r in (other, series, system)}
    write(repo, edge())
    assert len(traverse(repo)) == 1
    assert other.list_edges() == ()
    assert traverse(other) == ()
    assert {p: p.read_bytes() for p in before} == before
    with series.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0
    with system.connect() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'edges'").fetchone() is None


def old_schema(repo):
    repo.initialize()
    with repo.connect() as conn:
        conn.execute("DROP TABLE edges")
        conn.execute("UPDATE schema_version SET version = 2")
        conn.execute("UPDATE project_identity SET schema_version = 2")


def test_controlled_v2_to_current_migration_preserves_data_and_read_never_migrates(repo):
    repo.set_metadata("preserved", "yes")
    old_schema(repo)
    before = repo.db_path.read_bytes()
    assert repo.inspect_schema().migration_needed
    assert repo.inspect_schema().current_version == 2
    with pytest.raises(ProjectStorageError, match="controlled migration"):
        repo.list_edges()
    assert repo.db_path.read_bytes() == before
    assert repo.migrate_schema().current_version == PROJECT_DB_SCHEMA_VERSION == 5
    assert repo.get_project_identity()["schema_version"] == 5
    assert repo.get_metadata("preserved") == "yes"
    write(repo, edge())
    assert repo.get_edge("dependency-1") == edge()
    assert not repo.migrate_schema().migration_needed


def test_migration_rollback_and_existing_backup_hook(repo):
    old_schema(repo)
    backup = sqlite3.connect(":memory:")
    observed = []

    def backup_before_change(conn):
        conn.backup(backup)
        observed.append(backup.execute("SELECT version FROM schema_version").fetchone()[0])

    def fail_validation(conn):
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='edges'").fetchone()
        raise RuntimeError("migration rejected")

    migration = next(item for item in PROJECT_DB_MIGRATIONS if item.source_version == 2)
    try:
        with pytest.raises(RuntimeError, match="migration rejected"):
            repo.migrate_schema((SchemaMigration(2, 3, migration.apply, fail_validation, backup_before_change),))
        assert observed == [2]
        assert repo.inspect_schema().current_version == 2
        with repo.connect() as conn:
            assert conn.execute("SELECT name FROM sqlite_master WHERE name='edges'").fetchone() is None
            assert conn.execute("SELECT schema_version FROM project_identity").fetchone()[0] == 2
    finally:
        backup.close()

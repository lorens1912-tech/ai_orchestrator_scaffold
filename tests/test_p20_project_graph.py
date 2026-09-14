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
    ProjectStorageError, SchemaMigration, SeriesRepository, StorageResolver,
    StorageScope, SystemRepository,
)


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
    {"scope_type": "SERIES"}, {"relation_type": "INVENTED"},
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


def test_graph_node_requires_valid_project_scoped_id():
    with pytest.raises(DomainContractError):
        GraphNodeRef(StorageScope.series("SERIES-one"), "EVENT-a")
    with pytest.raises(DomainContractError):
        GraphNodeRef(StorageScope.project("PROJ-one"), "EVENT-../a")
    with pytest.raises(DomainContractError):
        GraphNodeRef(StorageScope.project("PROJ-one"), "PROJ-other")


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


def test_project_isolation_and_series_system_unchanged(repo):
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
    for r in (series, system):
        with r.connect() as conn:
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
    assert repo.migrate_schema().current_version == PROJECT_DB_SCHEMA_VERSION == 4
    assert repo.get_project_identity()["schema_version"] == 4
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

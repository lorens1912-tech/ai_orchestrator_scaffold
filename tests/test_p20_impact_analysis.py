from __future__ import annotations

import json
import sqlite3

import pytest

from app.p20_core.domain_records import DomainContractError, DomainId, EdgeRecord, EdgeRelationType
from app.p20_core.impact_analysis import (
    IMPACT_RELATION_DIRECTIONS, ImpactClass, ImpactRequest, ImpactScopeUnsupportedError,
    analyze_impact,
)
from app.p20_core.project_graph import DependencyTraversalPolicy, GraphTraversalLimitError, TraversalDirection
from app.p20_core.project_repository import (
    ProjectRepository, ProjectStorageError, SeriesAccessContext, SeriesAccessError,
    SeriesRepository, StorageResolver,
)


@pytest.fixture
def repo(isolated_agentpro_storage):
    repository = ProjectRepository(StorageResolver().resolve_project("PROJ-impact"))
    repository.initialize()
    return repository


def edge(key, source, target, relation="DEPENDS_ON", project="PROJ-impact"):
    return EdgeRecord(
        edge_id=key, scope_type="PROJECT", scope_id=project,
        source_type=DomainId.parse(source).namespace.name, source_id=source,
        target_type=DomainId.parse(target).namespace.name, target_id=target,
        relation_type=relation, valid_from=None, valid_to=None, confidence=1,
        source_ref="SCENE-source#artifact:v1", version=1,
    )


def write(repo, *edges):
    with repo.domain_transaction() as tx:
        for record in edges:
            tx.add_edge(record, source_scope=repo.scope, target_scope=repo.scope)


def request(node="FACT-a", project="PROJ-impact", **changes):
    data = dict(project_id=project, scope_type="PROJECT", scope_id=project,
                entity_id=node, entity_type=DomainId.parse(node).namespace.name)
    data.update(changes)
    return ImpactRequest(**data)


def test_direct_transitive_derived_and_provenance(repo):
    write(repo, edge("1", "SCENE-a", "FACT-a", "USES"),
          edge("2", "SCENE-a", "CHAPTER-a", "PART_OF"),
          edge("3", "EVALUATION-a", "CHAPTER-a"),
          edge("4", "FACT-unrelated", "FACT-else"))
    result = analyze_impact(repo, request())
    assert [i.entity_id for i in result.impacts] == ["SCENE-a", "CHAPTER-a", "EVALUATION-a"]
    assert [i.impact_class for i in result.impacts] == [ImpactClass.DIRECT, ImpactClass.TRANSITIVE, ImpactClass.DERIVED]
    assert [i.depth for i in result.impacts] == [1, 2, 3]
    path = result.impacts[-1].path
    assert [h.edge_id for h in path] == ["1", "2", "3"]
    assert [h.relation_type for h in path] == ["USES", "PART_OF", "DEPENDS_ON"]
    assert [h.from_id for h in path] == ["FACT-a", "SCENE-a", "CHAPTER-a"]
    assert all(h.source_ref == "SCENE-source#artifact:v1" and h.edge_version == 1 for h in path)
    assert all(i.scope == repo.scope for i in result.impacts)
    assert result.to_dict()["impacts"][-1]["dependency_class"] == "TRANSITIVE"
    assert result.to_dict()["source"]["entity_id"] == "FACT-a"


@pytest.mark.parametrize("relation", list(EdgeRelationType))
def test_every_relation_has_explicit_direction(repo, relation):
    assert set(IMPACT_RELATION_DIRECTIONS) == set(EdgeRelationType)
    write(repo, edge("1", "FACT-a", "FACT-b", relation))
    direction = IMPACT_RELATION_DIRECTIONS[relation]
    outgoing = analyze_impact(repo, request("FACT-a")).impacts
    incoming = analyze_impact(repo, request("FACT-b")).impacts
    assert bool(outgoing) == (direction != TraversalDirection.INCOMING)
    assert bool(incoming) == (direction != TraversalDirection.OUTGOING)


def test_bounds_and_filtered_work_limit_fail_closed(repo):
    write(repo, edge("1", "FACT-b", "FACT-a"), edge("2", "FACT-c", "FACT-b"))
    assert analyze_impact(repo, request(), max_depth=0).impacts == ()
    assert [i.entity_id for i in analyze_impact(repo, request(), max_depth=1).impacts] == ["FACT-b"]
    with pytest.raises(GraphTraversalLimitError):
        analyze_impact(repo, request(), max_edges=1)
    write(repo, edge("3", "FACT-c", "FACT-d"))
    with pytest.raises(GraphTraversalLimitError):
        analyze_impact(repo, request("FACT-c"), max_edges=1)


def test_fact_change_reaches_knowledge_scene_and_chapter(repo):
    write(repo, edge("1", "CHAR-one", "FACT-a", "KNOWS"),
          edge("2", "CHAR-one", "SCENE-one", "PRESENT_IN"),
          edge("3", "SCENE-one", "CHAPTER-one", "PART_OF"))
    assert [i.entity_id for i in analyze_impact(repo, request()).impacts] == [
        "CHAR-one", "SCENE-one", "CHAPTER-one",
    ]


def test_dependency_does_not_propagate_to_prerequisite_but_cause_propagates_forward(repo):
    write(repo, edge("1", "FACT-a", "FACT-required", "REQUIRES"),
          edge("2", "FACT-a", "EVENT-effect", "CAUSES"))
    assert [i.entity_id for i in analyze_impact(repo, request()).impacts] == ["EVENT-effect"]


def test_cycle_diamond_self_edge_dedup_and_stable_shortest_path(repo):
    write(repo, edge("a", "FACT-b", "FACT-a"), edge("b", "FACT-c", "FACT-a"),
          edge("c", "FACT-d", "FACT-b"), edge("d", "FACT-d", "FACT-c"),
          edge("e", "FACT-a", "FACT-d"), edge("f", "FACT-a", "FACT-a"))
    result = analyze_impact(repo, request(), max_depth=100)
    assert [i.entity_id for i in result.impacts] == ["FACT-b", "FACT-c", "FACT-d"]
    assert [h.edge_id for h in result.impacts[-1].path] == ["a", "c"]
    assert result == analyze_impact(repo, request(), max_depth=100)


def test_insertion_order_does_not_change_result(repo):
    records = [edge("z", "FACT-c", "FACT-a"), edge("a", "FACT-b", "FACT-a")]
    write(repo, *records)
    first = json.dumps(analyze_impact(repo, request()).to_dict(), sort_keys=True)
    # A second isolated physical root with the same logical project and graph.
    other = ProjectRepository(StorageResolver(repo.context.storage_root / "other").resolve_project("PROJ-impact"))
    write(other, *reversed(records))
    assert json.dumps(analyze_impact(other, request()).to_dict(), sort_keys=True) == first


def test_cross_project_scope_and_same_local_ids(repo):
    write(repo, edge("1", "FACT-b", "FACT-a"))
    other = ProjectRepository(StorageResolver().resolve_project("PROJ-other"))
    write(other, edge("2", "FACT-secret", "FACT-a", project="PROJ-other"))
    assert [i.entity_id for i in analyze_impact(repo, request()).impacts] == ["FACT-b"]
    with pytest.raises(ProjectStorageError):
        analyze_impact(other, request())
    with pytest.raises(DomainContractError):
        request(scope_id="PROJ-other")


@pytest.mark.parametrize("access", [None, SeriesAccessContext.bind("PROJ-other", "SERIES-one"),
                                    SeriesAccessContext.bind("PROJ-impact", "SERIES-other")])
def test_series_requires_exact_access_before_any_read(repo, access, monkeypatch):
    monkeypatch.setattr(repo, "traverse_dependencies", lambda *args: pytest.fail("unexpected graph read"))
    with pytest.raises(SeriesAccessError):
        analyze_impact(repo, request(scope_type="SERIES", scope_id="SERIES-one"), series_access=access)


def test_authorized_series_is_explicitly_unsupported_not_global_or_empty(repo):
    series = SeriesRepository(StorageResolver().resolve_series("SERIES-one"))
    series.initialize()
    before = series.db_path.read_bytes()
    with pytest.raises(ImpactScopeUnsupportedError, match="PROJECT only"):
        analyze_impact(repo, request(scope_type="SERIES", scope_id="SERIES-one"),
                       series_access=SeriesAccessContext.bind("PROJ-impact", "SERIES-one"))
    assert series.db_path.read_bytes() == before


def test_analysis_reuses_traversal_once_and_does_not_mutate(repo, monkeypatch):
    write(repo, edge("1", "FACT-b", "FACT-a"))
    with repo.domain_transaction() as tx:
        tx.add_fact_record("FACT-a", '{"frozen":true}')
    before = repo.db_path.read_bytes()
    calls = []
    original = repo.traverse_dependencies

    def traced(start, policy):
        calls.append(policy)
        return original(start, policy)

    monkeypatch.setattr(repo, "traverse_dependencies", traced)
    monkeypatch.setattr(repo, "initialize", lambda: pytest.fail("analysis must not initialize"))
    monkeypatch.setattr(repo, "connect", lambda: pytest.fail("analysis must not use write-capable connection"))
    assert len(analyze_impact(repo, request()).impacts) == 1
    assert len(calls) == 1 and calls[0].read_only
    assert calls[0].relation_directions is not None
    assert repo.db_path.read_bytes() == before


def test_missing_database_not_created(isolated_agentpro_storage):
    repo = ProjectRepository(StorageResolver().resolve_project("PROJ-impact"))
    with pytest.raises(sqlite3.OperationalError):
        analyze_impact(repo, request())
    assert not repo.context.project_root.exists()


def test_old_schema_and_wrong_identity_rejected_without_migration(repo):
    with repo.connect() as conn:
        conn.execute("UPDATE schema_version SET version=2")
    before = repo.db_path.read_bytes()
    with pytest.raises(ProjectStorageError, match="controlled migration"):
        analyze_impact(repo, request())
    assert repo.db_path.read_bytes() == before
    with repo.connect() as conn:
        conn.execute("UPDATE schema_version SET version=3")
        conn.execute("UPDATE project_identity SET project_id='PROJ-other'")
    with pytest.raises(ProjectStorageError, match="identity"):
        analyze_impact(repo, request())


@pytest.mark.parametrize("changes", [{"entity_type": "SCENE"}, {"entity_id": "unsafe"},
                                     {"project_id": "not-a-project"}, {"operation_type": ""}])
def test_input_contract(changes):
    with pytest.raises(ValueError):
        request(**changes)


def test_optional_operation_and_invalid_bounds(repo):
    assert analyze_impact(repo, request(operation_type="RETCON")).source.operation_type == "RETCON"
    for kwargs in ({"max_depth": -1}, {"max_depth": True}, {"max_edges": 0}):
        with pytest.raises(ValueError):
            analyze_impact(repo, request(), **kwargs)


@pytest.mark.parametrize("kwargs", [
    {"read_only": "yes"}, {"relation_directions": (("UNKNOWN", "INCOMING"),)},
    {"relation_directions": (("USES", "UNKNOWN"),)},
    {"relation_directions": (("USES", "INCOMING"), ("USES", "OUTGOING"))},
])
def test_extended_traversal_policy_fails_closed(kwargs):
    with pytest.raises(DomainContractError):
        DependencyTraversalPolicy(**kwargs)

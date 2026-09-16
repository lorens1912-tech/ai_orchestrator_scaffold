from __future__ import annotations

import hashlib
import inspect
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from app.p20_core.context_builder import (
    ContextBuildRequest,
    ContextBuilder,
    ContextBuilderError,
    ContextCandidate,
    ContextLayer,
    ContextOverflowError,
    ContextPolicy,
    ContextRole,
    MissingMandatoryContextError,
    OverflowBehavior,
    RepresentationType,
    STRUCTURED_FIRST_LAYER_ORDER,
    SemanticProvenance,
    default_context_profiles,
)
from app.p20_core.domain_records import EdgeRecord, FactRecord, SceneContract
from app.p20_core.project_graph import GraphNodeRef
from app.p20_core.project_repository import (
    PROJECT_DB_SCHEMA_VERSION,
    ProjectRepository,
    ProjectStorageError,
    SeriesAccessContext,
    SeriesRepository,
    StorageResolver,
)
from app.p20_core.series_memory import (
    SeriesMembershipRecord,
    SeriesStateKind,
    SeriesStateRecord,
)


PROJECT_ID = "PROJ-context"
BOOK_ID = "BOOK-context"
SERIES_ID = "SERIES-context"


class ExactWordCounter:
    tokenizer_id = "test-word-tokenizer"
    tokenizer_version = "1"

    def count_tokens(self, text: str, *, model: str) -> int:
        assert model == "test-model"
        return len(text.split())


class SemanticProvider:
    embedding_model = "embedding-test"
    index_version = "index-v1"

    def __init__(self, *candidates: ContextCandidate) -> None:
        self.candidates = candidates
        self.calls = 0

    def retrieve(self, query: str, *, project_id: str, limit: int):
        assert query == "hidden door"
        assert project_id == PROJECT_ID
        assert limit == 10
        self.calls += 1
        return self.candidates


@pytest.fixture
def repo(isolated_agentpro_storage) -> ProjectRepository:
    repository = ProjectRepository(
        StorageResolver().resolve_project(PROJECT_ID, book_id=BOOK_ID)
    )
    repository.initialize()
    return repository


def policy(
    *,
    available: int = 128,
    version: int = 1,
    profile_id: str = "P20_WRITER",
    weights: dict[str, float] | None = None,
    graph_depth: int = 2,
    graph_edges: int = 100,
) -> ContextPolicy:
    return ContextPolicy(
        policy_id="NOVEL_WRITE",
        version=version,
        profile_id=profile_id,
        context_window=available + 12,
        reserved_output_tokens=5,
        reserved_system_tokens=4,
        technical_overhead_tokens=3,
        layer_priorities=STRUCTURED_FIRST_LAYER_ORDER,
        ranking_weights=weights or {
            "graph_proximity": 1,
            "task_relevance": 1,
            "narrative_recency": 1,
            "confidence": 1,
            "semantic_similarity": 1,
            "story_importance": 1,
        },
        graph_max_depth=graph_depth,
        graph_max_edges=graph_edges,
        overflow_behavior=OverflowBehavior.ESCALATE,
        allowed_representations=(
            RepresentationType.FULL,
            RepresentationType.STRUCTURED,
            RepresentationType.COMPRESSED,
            RepresentationType.SUMMARY,
            RepresentationType.WARNING,
        ),
    )


def scene(*, facts=(), events=(), threads=(), states=()) -> SceneContract:
    return SceneContract(
        scene_id="SCENE-context",
        project_id=PROJECT_ID,
        chapter_id="CHAPTER-context",
        order=1,
        narrative_order=50,
        pov_character_id="CHAR-ada",
        participant_character_ids=("CHAR-ada",),
        location_id=None,
        route_id=None,
        time_start="day-1",
        time_end="day-1",
        purpose="investigate",
        goal="open-door",
        obstacle="lock",
        conflict="deadline",
        stakes="truth",
        outcome="opened",
        state_change="evidence",
        facts_required=(),
        facts_created=(),
        facts_revealed=(),
        must_include_facts=facts,
        must_include_events=events,
        must_include_threads=threads,
        must_include_character_states=states,
        threads_opened=(),
        threads_progressed=(),
        threads_closed=(),
        setups_created=(),
        payoffs_completed=(),
        reader_knowledge_added=(),
        target_tension=0.7,
        target_pace=0.6,
        status="PLANNED",
        version=1,
    )


def candidate(
    entity_id: str,
    *,
    entity_type: str = "FACT",
    layer: ContextLayer = ContextLayer.STRUCTURED_MEMORY,
    content: str = "one",
    structured: str | None = None,
    mandatory: bool = False,
    source_version: int = 1,
    project_id: str = PROJECT_ID,
    **scores,
) -> ContextCandidate:
    representations = {RepresentationType.FULL: content}
    if structured is not None:
        representations[RepresentationType.STRUCTURED] = structured
    return ContextCandidate(
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        layer=layer,
        reason=f"test candidate {entity_id}",
        representations=representations,
        source_version=source_version,
        source_ref="SCENE-source#artifact",
        mandatory=mandatory,
        **scores,
    )


def baseline_candidates(*extra: ContextCandidate) -> tuple[ContextCandidate, ...]:
    return (
        candidate(
            "CONTEXT-canon",
            entity_type="CANON",
            layer=ContextLayer.CANON,
            content="canon",
        ),
        candidate(
            "BOOK-bible",
            entity_type="BOOK_BIBLE",
            layer=ContextLayer.BOOK_BIBLE,
            content="bible",
        ),
        *extra,
    )


def request(
    *extra: ContextCandidate,
    package_id: str = "CONTEXT-package-1",
    operation_id: str = "context-operation-1",
    created_at: str = "2026-09-14T12:00:00Z",
    active_scene: SceneContract | None = None,
    series_id: str | None = None,
    graph_starts=(),
    semantic_query: str | None = None,
) -> ContextBuildRequest:
    return ContextBuildRequest(
        context_package_id=package_id,
        operation_id=operation_id,
        project_id=PROJECT_ID,
        book_id=BOOK_ID,
        series_id=series_id,
        run_id="run-context",
        step_id="step-context",
        role=ContextRole.WRITER,
        mode="WRITE",
        effective_model="test-model",
        canon_version=3,
        book_bible_version=2,
        style_version=None,
        memory_snapshot_id=None,
        graph_version=1,
        created_at=created_at,
        direct_candidates=baseline_candidates(*extra),
        scene_contract=active_scene or scene(),
        graph_starts=graph_starts,
        semantic_query=semantic_query,
    )


def build(repo: ProjectRepository, req: ContextBuildRequest, **kwargs):
    builder = ContextBuilder(repo, ExactWordCounter(), **kwargs)
    return builder.build(req, policy(), default_context_profiles()[ContextRole.WRITER])


def item_ids(package) -> list[str]:
    return [item.entity_id for item in package.included_items]


def fact(record_id: str, *, frozen: bool = False, version: int = 1) -> FactRecord:
    return FactRecord(
        fact_id=record_id,
        project_id=PROJECT_ID,
        subject_id="CHAR-ada",
        predicate="knows",
        object_type="TEXT",
        object_id=None,
        object_value="the hidden door exists",
        reality_status="TRUE",
        verification_status="VERIFIED",
        confidence=1,
        frozen=frozen,
        author_locked=False,
        valid_from="day-1",
        valid_to=None,
        established_event_id=None,
        established_scene_id="SCENE-source",
        source_artifact_ref="books/BOOK-context/chapter.json",
        source_refs=("SCENE-source#artifact",),
        canon_version=1,
        version=version,
        created_at="2026-09-14T10:00:00Z",
        updated_at="2026-09-14T10:00:00Z",
    )


def persist_memory(repo: ProjectRepository, *records) -> None:
    with repo.domain_transaction() as transaction:
        for record in records:
            transaction.add_structured_memory_record(record)


def test_mandatory_fact_from_gap007_always_enters(repo) -> None:
    persist_memory(repo, fact("FACT-required"))
    package = build(repo, request(active_scene=scene(facts=("FACT-required",))))
    item = next(item for item in package.included_items if item.entity_id == "FACT-required")
    assert item.mandatory
    assert item.layer == ContextLayer.MUST_INCLUDE


def test_irrelevant_frozen_fact_does_not_have_to_enter(repo) -> None:
    persist_memory(repo, fact("FACT-frozen", frozen=True))
    req = request()
    package = ContextBuilder(repo, ExactWordCounter()).build(
        req,
        policy(available=3),
        default_context_profiles()[ContextRole.WRITER],
    )
    assert "FACT-frozen" not in item_ids(package)
    assert package.selection_summary.excluded_count == 1


@pytest.mark.parametrize(
    ("field", "entity_id"),
    [
        ("facts", "FACT-required"),
        ("events", "EVENT-required"),
        ("threads", "THREAD-required"),
        ("states", "CONTEXT-required"),
    ],
)
def test_every_scene_must_include_kind_is_mandatory(repo, field, entity_id) -> None:
    extra = candidate(entity_id, entity_type=entity_id.split("-", 1)[0])
    package = build(repo, request(extra, active_scene=scene(**{field: (entity_id,)})))
    item = next(item for item in package.included_items if item.entity_id == entity_id)
    assert item.mandatory and item.score is None


def test_missing_must_include_is_explicit_failure(repo) -> None:
    with pytest.raises(MissingMandatoryContextError, match="FACT-missing"):
        build(repo, request(active_scene=scene(facts=("FACT-missing",))))


def test_relevant_conflict_is_mandatory_warning(repo) -> None:
    conflict = candidate(
        "CONFLICT-door",
        entity_type="CONFLICT",
        layer=ContextLayer.CONFLICT,
        content='{"versions":["locked","open"],"status":"DISPUTED"}',
    )
    package = build(repo, request(conflict))
    item = next(item for item in package.included_items if item.entity_id == "CONFLICT-door")
    assert item.mandatory
    assert item.representation_type == RepresentationType.WARNING
    assert "unconfirmed" in item.content


def test_direct_cross_project_candidate_is_rejected(repo) -> None:
    foreign = candidate("FACT-foreign", project_id="PROJ-other")
    with pytest.raises(ContextBuilderError, match="cross-project"):
        build(repo, request(foreign))


def test_project_repository_does_not_retrieve_other_project_memory(
    repo, isolated_agentpro_storage
) -> None:
    other = ProjectRepository(
        StorageResolver().resolve_project("PROJ-other", book_id="BOOK-other")
    )
    persist_memory(other, replace(fact("FACT-foreign"), project_id="PROJ-other"))
    package = build(repo, request())
    assert "FACT-foreign" not in item_ids(package)


def series_repository() -> tuple[SeriesRepository, SeriesAccessContext]:
    repository = SeriesRepository(StorageResolver().resolve_series(SERIES_ID))
    access = SeriesAccessContext.bind(PROJECT_ID, SERIES_ID)
    repository.initialize()
    repository.register_member(
        access,
        SeriesMembershipRecord(
            series_id=SERIES_ID,
            project_id=PROJECT_ID,
            book_id=BOOK_ID,
            source_ref="author-series-membership",
            version=1,
            created_at="2026-09-14T10:00:00Z",
        ),
    )
    repository.save_series_memory(
        access,
        SeriesStateRecord(
            series_id=SERIES_ID,
            state_kind=SeriesStateKind.MEMORY,
            source_project_id=PROJECT_ID,
            source_book_id=BOOK_ID,
            record_type="FACT",
            record_id="FACT-series",
            source_version=1,
            source_ref="SCENE-volume-one",
            provenance_refs=("SCENE-volume-one#artifact",),
            transfer_reason="required in next volume",
            state={"importance": 1, "value": "family oath"},
            operation_id="series-context-record",
            version=1,
            frozen=False,
            author_locked=False,
            created_at="2026-09-14T10:00:00Z",
            updated_at="2026-09-14T10:00:00Z",
        ),
    )
    return repository, access


def test_series_memory_is_available_only_through_registered_access(repo) -> None:
    series_repo, access = series_repository()
    package = ContextBuilder(
        repo, ExactWordCounter(), series_repository=series_repo
    ).build(
        request(series_id=SERIES_ID),
        policy(),
        default_context_profiles()[ContextRole.WRITER],
        series_access=access,
    )
    assert "FACT-series" in item_ids(package)
    assert next(
        item for item in package.included_items if item.entity_id == "FACT-series"
    ).layer == ContextLayer.SERIES_MEMORY


def test_independent_project_cannot_receive_series_memory(repo) -> None:
    series_repo, access = series_repository()
    builder = ContextBuilder(repo, ExactWordCounter(), series_repository=series_repo)
    with pytest.raises(ContextBuilderError, match="independent project"):
        builder.build(
            request(),
            policy(),
            default_context_profiles()[ContextRole.WRITER],
            series_access=access,
        )


def test_cross_series_access_is_rejected(repo) -> None:
    series_repo, _ = series_repository()
    with pytest.raises(ValueError, match="requested series"):
        ContextBuilder(repo, ExactWordCounter(), series_repository=series_repo).build(
            request(series_id=SERIES_ID),
            policy(),
            default_context_profiles()[ContextRole.WRITER],
            series_access=SeriesAccessContext.bind(PROJECT_ID, "SERIES-other"),
        )


def test_older_important_fact_beats_newer_irrelevant_fact(repo) -> None:
    older = candidate(
        "FACT-old-important", story_importance=1, narrative_recency=0.1
    )
    newer = candidate(
        "FACT-new-irrelevant", story_importance=0, narrative_recency=1
    )
    package = ContextBuilder(repo, ExactWordCounter()).build(
        request(older, newer),
        policy(
            available=4,
            weights={"story_importance": 1, "narrative_recency": 0.1},
        ),
        default_context_profiles()[ContextRole.WRITER],
    )
    assert "FACT-old-important" in item_ids(package)
    assert "FACT-new-irrelevant" not in item_ids(package)


def test_recency_uses_explicit_metric_not_entity_id(repo) -> None:
    early_id = candidate("FACT-999999", narrative_recency=0.1)
    late_id = candidate("FACT-000001", narrative_recency=0.9)
    package = ContextBuilder(repo, ExactWordCounter()).build(
        request(early_id, late_id),
        policy(available=4, weights={"narrative_recency": 1}),
        default_context_profiles()[ContextRole.WRITER],
    )
    assert "FACT-000001" in item_ids(package)
    assert "FACT-999999" not in item_ids(package)


def test_overflow_downgrades_but_never_cuts_critical_canon(repo) -> None:
    canon = candidate(
        "CONTEXT-canon-long",
        entity_type="CANON",
        layer=ContextLayer.CANON,
        content="one two three four five",
        structured="canon",
    )
    req = replace(request(), direct_candidates=(
        canon,
        baseline_candidates()[1],
    ))
    package = ContextBuilder(repo, ExactWordCounter()).build(
        req,
        policy(available=3),
        default_context_profiles()[ContextRole.WRITER],
    )
    selected = next(item for item in package.included_items if item.entity_id == "CONTEXT-canon-long")
    assert selected.mandatory
    assert selected.representation_type == RepresentationType.STRUCTURED


def test_mandatory_over_budget_returns_typed_escalation(repo) -> None:
    long_canon = candidate(
        "CONTEXT-canon-long",
        entity_type="CANON",
        layer=ContextLayer.CANON,
        content="one two three four",
    )
    req = replace(request(), direct_candidates=(long_canon, baseline_candidates()[1]))
    with pytest.raises(ContextOverflowError) as captured:
        ContextBuilder(repo, ExactWordCounter()).build(
            req,
            policy(available=3),
            default_context_profiles()[ContextRole.WRITER],
        )
    assert captured.value.to_dict()["status"] == "ESCALATION_REQUIRED"


def test_technical_retry_reuses_exact_persisted_package_without_retrieval(repo) -> None:
    semantic = candidate(
        "FACT-semantic",
        layer=ContextLayer.SEMANTIC,
        semantic_similarity=1,
    )
    semantic = replace(
        semantic,
        semantic_provenance=SemanticProvenance(
            "embedding-test", "index-v1", 1, "source-sha"
        ),
    )
    provider = SemanticProvider(semantic)
    builder = ContextBuilder(repo, ExactWordCounter(), semantic_provider=provider)
    first = builder.build(
        request(semantic_query="hidden door"),
        policy(),
        default_context_profiles()[ContextRole.WRITER],
    )
    replay_request = replace(
        request(semantic_query="hidden door"),
        context_package_id="CONTEXT-package-retry-ignored",
        created_at="2026-09-14T13:00:00Z",
    )
    second = builder.build(
        replay_request,
        policy(),
        default_context_profiles()[ContextRole.WRITER],
    )
    assert second == first
    assert provider.calls == 1
    assert builder.reuse_for_retry(
        project_id=PROJECT_ID, operation_id="context-operation-1"
    ) == first


def test_new_creative_attempt_may_have_new_identity_and_same_hash(repo) -> None:
    first = build(repo, request())
    second = build(repo, request(
        package_id="CONTEXT-package-2",
        operation_id="context-operation-2",
        created_at="2026-09-14T14:00:00Z",
    ))
    assert first.context_package_id != second.context_package_id
    assert first.context_hash == second.context_hash


def test_policy_version_changes_context_hash(repo) -> None:
    first = build(repo, request())
    second = ContextBuilder(repo, ExactWordCounter()).build(
        request(
            package_id="CONTEXT-package-2",
            operation_id="context-operation-2",
        ),
        policy(version=2),
        default_context_profiles()[ContextRole.WRITER],
    )
    assert first.context_hash != second.context_hash


@pytest.mark.parametrize(
    "changed",
    [
        candidate("FACT-versioned", source_version=2),
        candidate("FACT-versioned", content="changed content"),
    ],
)
def test_source_version_or_content_change_changes_hash(repo, changed) -> None:
    first = build(repo, request(candidate("FACT-versioned")))
    second = build(repo, request(
        changed,
        package_id="CONTEXT-package-2",
        operation_id="context-operation-2",
    ))
    assert first.context_hash != second.context_hash


def test_created_at_does_not_change_context_hash(repo) -> None:
    first = build(repo, request())
    second = build(repo, request(
        package_id="CONTEXT-package-2",
        operation_id="context-operation-2",
        created_at="2099-01-01T00:00:00Z",
    ))
    assert first.context_hash == second.context_hash


def test_context_trace_explains_selected_and_rejected_candidates(repo) -> None:
    accepted = candidate("FACT-selected", task_relevance=1)
    rejected = candidate("FACT-rejected", task_relevance=0)
    package = ContextBuilder(repo, ExactWordCounter()).build(
        request(accepted, rejected),
        policy(available=4, weights={"task_relevance": 1}),
        default_context_profiles()[ContextRole.WRITER],
    )
    selected = next(item for item in package.included_items if item.entity_id == "FACT-selected")
    assert selected.reason and selected.score_breakdown["task_relevance"] == 1
    assert package.selection_summary.candidate_count == 5
    assert package.selection_summary.top_rejected[0]["entity_id"] == "FACT-rejected"


def add_graph(repo: ProjectRepository, *, chain: bool = False) -> None:
    records = [
        EdgeRecord(
            edge_id="edge-context-1",
            scope_type="PROJECT",
            scope_id=PROJECT_ID,
            source_type="SCENE",
            source_id="SCENE-context",
            relation_type="REQUIRES",
            target_type="FACT",
            target_id="FACT-graph-1",
            valid_from=None,
            valid_to=None,
            confidence=1,
            source_ref="SCENE-context#contract",
            version=1,
        )
    ]
    if chain:
        records.append(EdgeRecord(
            edge_id="edge-context-2",
            scope_type="PROJECT",
            scope_id=PROJECT_ID,
            source_type="FACT",
            source_id="FACT-graph-1",
            relation_type="DEPENDS_ON",
            target_type="FACT",
            target_id="FACT-graph-2",
            valid_from=None,
            valid_to=None,
            confidence=1,
            source_ref="SCENE-context#contract",
            version=1,
        ))
    with repo.domain_transaction() as transaction:
        for record in records:
            transaction.add_edge(record, source_scope=repo.scope, target_scope=repo.scope)


def test_graph_retrieval_reuses_gap010_traversal(repo, monkeypatch) -> None:
    add_graph(repo)
    calls = 0
    original = repo.traverse_dependencies

    def observed(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(repo, "traverse_dependencies", observed)
    package = build(repo, request(
        graph_starts=(GraphNodeRef(repo.scope, "SCENE-context"),)
    ))
    assert calls == 1
    assert "FACT-graph-1" in item_ids(package)


def test_graph_retrieval_obeys_policy_depth_bound(repo) -> None:
    add_graph(repo, chain=True)
    package = ContextBuilder(repo, ExactWordCounter()).build(
        request(graph_starts=(GraphNodeRef(repo.scope, "SCENE-context"),)),
        policy(graph_depth=1),
        default_context_profiles()[ContextRole.WRITER],
    )
    assert "FACT-graph-1" in item_ids(package)
    assert "FACT-graph-2" not in item_ids(package)


def test_graph_retrieval_remains_cycle_safe(repo) -> None:
    add_graph(repo, chain=True)
    cycle = EdgeRecord(
        edge_id="edge-context-cycle",
        scope_type="PROJECT",
        scope_id=PROJECT_ID,
        source_type="FACT",
        source_id="FACT-graph-2",
        relation_type="DEPENDS_ON",
        target_type="SCENE",
        target_id="SCENE-context",
        valid_from=None,
        valid_to=None,
        confidence=1,
        source_ref="SCENE-context#contract",
        version=1,
    )
    with repo.domain_transaction() as transaction:
        transaction.add_edge(cycle, source_scope=repo.scope, target_scope=repo.scope)
    package = ContextBuilder(repo, ExactWordCounter()).build(
        request(graph_starts=(GraphNodeRef(repo.scope, "SCENE-context"),)),
        policy(graph_depth=8, graph_edges=10),
        default_context_profiles()[ContextRole.WRITER],
    )
    assert item_ids(package).count("FACT-graph-1") == 1
    assert item_ids(package).count("FACT-graph-2") == 1


def semantic_candidate(*, entity_id="FACT-semantic", project_id=PROJECT_ID):
    return replace(
        candidate(
            entity_id,
            project_id=project_id,
            layer=ContextLayer.SEMANTIC,
            semantic_similarity=1,
        ),
        semantic_provenance=SemanticProvenance(
            "embedding-test", "index-v1", 4, "semantic-source-sha"
        ),
    )


def test_no_semantic_provider_does_not_break_structured_builder(repo) -> None:
    package = build(repo, request(semantic_query="hidden door"))
    assert package.selection_summary.semantic_retrieval_used is False


def test_semantic_retrieval_is_late_supporting_layer_with_provenance(repo) -> None:
    provider = SemanticProvider(semantic_candidate())
    package = ContextBuilder(
        repo, ExactWordCounter(), semantic_provider=provider
    ).build(
        request(semantic_query="hidden door"),
        policy(),
        default_context_profiles()[ContextRole.WRITER],
    )
    item = next(item for item in package.included_items if item.entity_id == "FACT-semantic")
    assert item.layer == ContextLayer.SEMANTIC
    assert not item.mandatory
    assert item.semantic_provenance.embedding_model == "embedding-test"
    assert package.selection_summary.semantic_retrieval_used


def test_semantic_candidate_cannot_satisfy_mandatory_truth(repo) -> None:
    provider = SemanticProvider(semantic_candidate(entity_id="FACT-required"))
    with pytest.raises(MissingMandatoryContextError, match="FACT-required"):
        ContextBuilder(repo, ExactWordCounter(), semantic_provider=provider).build(
            request(
                active_scene=scene(facts=("FACT-required",)),
                semantic_query="hidden door",
            ),
            policy(),
            default_context_profiles()[ContextRole.WRITER],
        )


def test_semantic_cross_project_result_is_rejected(repo) -> None:
    provider = SemanticProvider(semantic_candidate(project_id="PROJ-other"))
    with pytest.raises(ContextBuilderError, match="cross-project"):
        ContextBuilder(repo, ExactWordCounter(), semantic_provider=provider).build(
            request(semantic_query="hidden door"),
            policy(),
            default_context_profiles()[ContextRole.WRITER],
        )


def test_budget_is_computed_before_context_retrieval(repo, monkeypatch) -> None:
    events = []
    builder = ContextBuilder(repo, ExactWordCounter())
    original_budget = builder._calculate_budget
    original_records = repo.list_structured_memory_records

    def observed_budget(value):
        events.append("budget")
        return original_budget(value)

    def observed_records():
        events.append("retrieval")
        return original_records()

    monkeypatch.setattr(builder, "_calculate_budget", observed_budget)
    monkeypatch.setattr(repo, "list_structured_memory_records", observed_records)
    builder.build(request(), policy(), default_context_profiles()[ContextRole.WRITER])
    assert events[:2] == ["budget", "retrieval"]


def test_lower_semantic_layer_cannot_displace_mandatory_context(repo) -> None:
    provider = SemanticProvider(semantic_candidate())
    package = ContextBuilder(repo, ExactWordCounter(), semantic_provider=provider).build(
        request(semantic_query="hidden door"),
        policy(available=3),
        default_context_profiles()[ContextRole.WRITER],
    )
    assert set(item_ids(package)) == {"SCENE-context", "CONTEXT-canon", "BOOK-bible"}


def test_context_package_persists_across_repository_reopen(repo) -> None:
    package = build(repo, request())
    reopened = ProjectRepository(repo.context)
    assert reopened.get_context_package(package.context_package_id) == package
    assert reopened.get_context_package_for_operation("context-operation-1") == package
    assert reopened.list_context_packages() == (package,)


def test_operation_identity_cannot_be_reused_for_different_package(repo) -> None:
    package = build(repo, request())
    changed = replace(package, created_at="2026-09-14T15:00:00Z")
    with pytest.raises(ProjectStorageError, match="different package"):
        repo.save_context_package(changed, operation_id="context-operation-1")


def test_context_build_does_not_mutate_canon_or_structured_memory(repo) -> None:
    persist_memory(repo, fact("FACT-preserved"))
    before = repo.list_structured_memory_records()
    book_root = repo.context.book_root
    before_files = sorted(
        path.relative_to(book_root).as_posix()
        for path in book_root.rglob("*")
    ) if book_root.exists() else []
    build(repo, request())
    assert repo.list_structured_memory_records() == before
    after_files = sorted(
        path.relative_to(book_root).as_posix()
        for path in book_root.rglob("*")
    ) if book_root.exists() else []
    assert after_files == before_files


def test_role_profiles_are_distinct_and_policy_bound(repo) -> None:
    profiles = default_context_profiles()
    assert set(profiles) == {
        ContextRole.WRITER,
        ContextRole.CRITIC,
        ContextRole.CONTINUITY,
        ContextRole.CANON,
    }
    assert len({tuple(profile.allowed_layers) for profile in profiles.values()}) > 1
    with pytest.raises(ContextBuilderError, match="role"):
        ContextBuilder(repo, ExactWordCounter()).build(
            request(), policy(profile_id="P20_CRITIC"), profiles[ContextRole.CRITIC]
        )


def test_tokenizer_identity_and_exact_counts_are_auditable(repo) -> None:
    package = build(repo, request(candidate("FACT-counted", content="one two three")))
    item = next(item for item in package.included_items if item.entity_id == "FACT-counted")
    assert item.token_count == 3
    assert package.tokenizer_id == "test-word-tokenizer"
    assert package.tokenizer_version == "1"
    assert package.available_context_tokens == 128


def test_context_hash_is_deterministic_for_same_state_and_input(repo) -> None:
    first = build(repo, request())
    reopened = ProjectRepository(repo.context)
    second = build(reopened, request(
        package_id="CONTEXT-package-2",
        operation_id="context-operation-2",
    ))
    assert first.context_hash == second.context_hash
    assert [item.to_dict() for item in first.included_items] == [
        item.to_dict() for item in second.included_items
    ]


def test_project_schema_v3_migrates_to_context_package_storage(repo) -> None:
    with repo.connect() as connection:
        connection.execute("DROP TABLE context_packages")
        connection.execute("UPDATE schema_version SET version = 3")
        connection.execute("UPDATE project_identity SET schema_version = 3")
    before = repo.inspect_schema()
    assert before.current_version == 3 and before.migration_needed
    assert repo.migrate_schema().current_version == PROJECT_DB_SCHEMA_VERSION == 4
    package = build(repo, request())
    assert repo.get_context_package(package.context_package_id) == package


def test_gap013_creates_no_second_builder_traversal_or_storage() -> None:
    module_root = Path(__file__).resolve().parents[1] / "app" / "p20_core"
    builder_definitions = []
    traversal_definitions = []
    for path in module_root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "class ContextBuilder" in source:
            builder_definitions.append(path.name)
        if "def traverse_dependencies" in source:
            traversal_definitions.append(path.name)
    assert builder_definitions == ["context_builder.py"]
    assert traversal_definitions == ["project_repository.py"]
    assert not (Path(os.environ["AGENTPRO_STORAGE_ROOT"]) / "context.db").exists()
    source = inspect.getsource(ContextBuilder._apply_graph_retrieval)
    assert ".traverse_dependencies(" in source


def test_context_package_serialization_is_canonical_and_round_trips(repo) -> None:
    package = build(repo, request())
    serialized = package.to_json()
    assert serialized == json.dumps(
        package.to_dict(), sort_keys=True, separators=(",", ":")
    )
    assert repo._decode_context_package(serialized) == package


def test_f007_preexisting_context_package_hash_remains_readable(repo) -> None:
    package = build(repo, request())
    legacy_payload = package.to_dict()
    legacy_semantic_payload = package.semantic_payload()
    for item in legacy_payload["included_items"]:
        item.pop("logical_identity")
        item.pop("provenance")
        item.pop("reasons")
    legacy_payload["selection_summary"].pop("deduplication_trace")
    for item in legacy_semantic_payload["included_items"]:
        item.pop("logical_identity", None)
        item.pop("provenance", None)
        item.pop("reasons", None)
    legacy_payload["context_hash"] = hashlib.sha256(
        json.dumps(
            legacy_semantic_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    restored = repo._decode_context_package(json.dumps(legacy_payload))

    assert restored.context_hash == legacy_payload["context_hash"]
    assert restored.compute_context_hash() == restored.context_hash
    assert restored.selection_summary.deduplication_trace == ()


def f007_series_repository(
    *records: FactRecord,
    overrides: dict[str, dict] | None = None,
) -> tuple[SeriesRepository, SeriesAccessContext]:
    repository = SeriesRepository(StorageResolver().resolve_series(SERIES_ID))
    access = SeriesAccessContext.bind(PROJECT_ID, SERIES_ID)
    repository.initialize()
    repository.register_member(
        access,
        SeriesMembershipRecord(
            series_id=SERIES_ID,
            project_id=PROJECT_ID,
            book_id=BOOK_ID,
            source_ref="synthetic-f007-membership",
            version=1,
            created_at="2026-09-16T10:00:00Z",
        ),
    )
    for index, record in enumerate(records, start=1):
        values = dict((overrides or {}).get(str(record.record_id), {}))
        state_kind = values.pop("state_kind", SeriesStateKind.MEMORY)
        state = values.pop("state", record.to_dict())
        series_record = SeriesStateRecord(
            series_id=SERIES_ID,
            state_kind=state_kind,
            source_project_id=PROJECT_ID,
            source_book_id=BOOK_ID,
            record_type=record.memory_record_type,
            record_id=record.record_id,
            source_version=values.pop("source_version", record.version),
            source_ref=f"SCENE-f007-series-{index}",
            provenance_refs=(f"SCENE-f007-series-{index}#artifact",),
            transfer_reason="synthetic F-007 continuity proof",
            state=state,
            operation_id=f"series-f007-record-{index}",
            version=values.pop("version", record.version),
            frozen=values.pop("frozen", record.frozen),
            author_locked=values.pop("author_locked", record.author_locked),
            created_at="2026-09-16T10:00:00Z",
            updated_at="2026-09-16T10:00:00Z",
            **values,
        )
        if state_kind == SeriesStateKind.CANON:
            repository.save_series_canon(access, series_record)
        else:
            repository.save_series_memory(access, series_record)
    return repository, access


def build_f007_series_package(
    repo: ProjectRepository,
    series_repo: SeriesRepository,
    access: SeriesAccessContext,
    *,
    req: ContextBuildRequest | None = None,
):
    return ContextBuilder(
        repo,
        ExactWordCounter(),
        series_repository=series_repo,
    ).build(
        req or request(series_id=SERIES_ID),
        policy(),
        default_context_profiles()[ContextRole.WRITER],
        series_access=access,
    )


def test_f007_exact_project_series_duplicate_is_one_item_with_both_provenances(
    repo,
) -> None:
    record = fact("FACT-f007-exact")
    persist_memory(repo, record)
    series_repo, access = f007_series_repository(record)

    package = build_f007_series_package(repo, series_repo, access)
    project_only = build(
        repo,
        request(
            package_id="CONTEXT-f007-project-only",
            operation_id="context-f007-project-only",
        ),
    )

    items = [
        item for item in package.included_items
        if item.entity_id == str(record.fact_id)
    ]
    assert len(items) == 1
    assert {item.source_scope for item in items[0].provenance} == {"PROJECT", "SERIES"}
    assert package.total_tokens == project_only.total_tokens
    assert package.selection_summary.candidate_count == project_only.selection_summary.candidate_count
    trace = package.selection_summary.deduplication_trace
    assert len(trace) == 1
    assert trace[0].action == "MERGED_EXACT"
    assert trace[0].logical_identity == f"FACT:{record.fact_id}"
    assert len(trace[0].candidate_refs) == 2


def test_f007_mandatory_project_optional_series_duplicate_stays_mandatory(repo) -> None:
    record = fact("FACT-f007-mandatory")
    persist_memory(repo, record)
    series_repo, access = f007_series_repository(record)

    package = build_f007_series_package(
        repo,
        series_repo,
        access,
        req=request(
            series_id=SERIES_ID,
            active_scene=scene(facts=(str(record.fact_id),)),
        ),
    )

    items = [
        item for item in package.included_items
        if item.entity_id == str(record.fact_id)
    ]
    assert len(items) == 1
    assert items[0].mandatory
    assert items[0].layer == ContextLayer.MUST_INCLUDE
    assert set(items[0].reasons) == {
        "SceneContract mandatory reference",
        "project structured memory",
        "authorized Series Scope state",
    }
    trace = package.selection_summary.deduplication_trace[0]
    assert trace.mandatory_preserved
    assert trace.mandatory_promoted


@pytest.mark.parametrize(
    ("difference", "override", "expected_reason"),
    [
        ("version", {"source_version": 2, "version": 2}, "different source_version"),
        (
            "hash",
            {"state": {**fact("FACT-f007-conflict").to_dict(), "object_value": "changed"}},
            "different content_hash",
        ),
        ("authority", {"state_kind": SeriesStateKind.CANON}, "different authority"),
        ("protection", {"frozen": True}, "different frozen"),
    ],
)
def test_f007_different_variant_is_preserved_with_warning(
    repo,
    difference,
    override,
    expected_reason,
) -> None:
    del difference
    record = fact("FACT-f007-conflict")
    persist_memory(repo, record)
    series_repo, access = f007_series_repository(
        record,
        overrides={str(record.fact_id): override},
    )

    package = build_f007_series_package(repo, series_repo, access)
    variants = [
        item for item in package.included_items
        if item.entity_id == str(record.fact_id)
    ]
    warnings = [item for item in variants if item.layer == ContextLayer.CONFLICT]
    sources = [item for item in variants if item.layer != ContextLayer.CONFLICT]

    assert len(sources) == 2
    assert all(item.mandatory for item in sources)
    assert len(warnings) == 1
    assert warnings[0].mandatory
    assert warnings[0].representation_type == RepresentationType.WARNING
    assert "UNRESOLVED LOGICAL RECORD VARIANTS" in warnings[0].content
    conflict_trace = next(
        item
        for item in package.selection_summary.deduplication_trace
        if item.action == "PRESERVED_CONFLICT"
    )
    assert expected_reason in conflict_trace.not_merged_reasons


def test_f007_different_ids_with_same_text_are_not_merged(repo) -> None:
    first = fact("FACT-f007-same-text-a")
    second = fact("FACT-f007-same-text-b")
    persist_memory(repo, first, second)
    series_repo, access = f007_series_repository(first, second)

    package = build_f007_series_package(repo, series_repo, access)

    assert item_ids(package).count(str(first.fact_id)) == 1
    assert item_ids(package).count(str(second.fact_id)) == 1
    assert {
        item.logical_identity
        for item in package.included_items
        if item.entity_id in {str(first.fact_id), str(second.fact_id)}
    } == {
        f"FACT:{first.fact_id}",
        f"FACT:{second.fact_id}",
    }


def test_f007_order_independent_hash_items_and_trace(
    repo,
    monkeypatch,
) -> None:
    first_record = fact("FACT-f007-order-a")
    second_record = fact("FACT-f007-order-b")
    persist_memory(repo, first_record, second_record)
    series_repo, access = f007_series_repository(first_record, second_record)
    first = build_f007_series_package(repo, series_repo, access)

    project_records = repo.list_structured_memory_records()
    project_scopes = repo.list_structured_memory_record_scopes()
    series_records = series_repo.list_series_memory(access)
    monkeypatch.setattr(
        repo,
        "list_structured_memory_records",
        lambda: dict(reversed(tuple(project_records.items()))),
    )
    monkeypatch.setattr(
        repo,
        "list_structured_memory_record_scopes",
        lambda: dict(reversed(tuple(project_scopes.items()))),
    )
    monkeypatch.setattr(
        series_repo,
        "list_series_memory",
        lambda _access: tuple(reversed(series_records)),
    )
    second = build_f007_series_package(
        repo,
        series_repo,
        access,
        req=request(
            series_id=SERIES_ID,
            package_id="CONTEXT-f007-order-two",
            operation_id="context-f007-order-two",
        ),
    )

    assert first.context_hash == second.context_hash
    assert [item.to_dict() for item in first.included_items] == [
        item.to_dict() for item in second.included_items
    ]
    assert [item.to_dict() for item in first.selection_summary.deduplication_trace] == [
        item.to_dict() for item in second.selection_summary.deduplication_trace
    ]


def test_f007_retry_and_reopen_preserve_deduplicated_package(repo) -> None:
    record = fact("FACT-f007-retry")
    persist_memory(repo, record)
    series_repo, access = f007_series_repository(record)
    builder = ContextBuilder(repo, ExactWordCounter(), series_repository=series_repo)
    first_request = request(series_id=SERIES_ID)
    first = builder.build(
        first_request,
        policy(),
        default_context_profiles()[ContextRole.WRITER],
        series_access=access,
    )
    retry = builder.build(
        replace(first_request, context_package_id="CONTEXT-f007-retry-ignored"),
        policy(),
        default_context_profiles()[ContextRole.WRITER],
        series_access=access,
    )
    reopened = ProjectRepository(repo.context)

    assert retry == first
    assert item_ids(first).count(str(record.fact_id)) == 1
    assert reopened.get_context_package(first.context_package_id) == first
    assert reopened.get_context_package_for_operation(first_request.operation_id) == first
    assert next(
        item for item in first.included_items if item.entity_id == str(record.fact_id)
    ).layer == ContextLayer.STRUCTURED_MEMORY

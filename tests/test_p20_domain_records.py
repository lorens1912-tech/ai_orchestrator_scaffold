from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.p20_core.domain_records import (
    ActRecord,
    BookRecord,
    ChapterRecord,
    DomainContractError,
    DomainId,
    DomainNamespace,
    ProjectRecord,
    SceneContract,
    SequenceRecord,
    SeriesRecord,
    build_domain_id,
    parse_domain_id,
)
from app.p20_core.project_repository import ProjectRepository, StorageResolver, StorageScope


STAMP = "2026-09-11T00:00:00Z"


def _project_record(**overrides):
    data = {
        "project_id": "PROJ-alpha",
        "book_id": "BOOK-alpha",
        "series_id": None,
        "title": "Initial title",
        "project_type": "novel",
        "source_language": "pl",
        "target_market": "US",
        "status": "draft",
        "canon_version": 1,
        "book_bible_version": 1,
        "style_profile_version": 1,
        "active_run_id": None,
        "storage_namespace": "alpha_project",
        "schema_version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return ProjectRecord(**data)


def _series_record(**overrides):
    data = {
        "series_id": "SERIES-alpha",
        "name": "Series Alpha",
        "description": "Shared series scope",
        "series_canon_version": 1,
        "status": "draft",
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return SeriesRecord(**data)


def _book_record(**overrides):
    data = {
        "book_id": "BOOK-alpha",
        "project_id": "PROJ-alpha",
        "series_id": None,
        "volume_number": None,
        "title": "Book Alpha",
        "source_language": "pl",
        "target_market": "US",
        "opening_state_ref": None,
        "closing_state_ref": None,
        "canon_version": 1,
        "style_profile_version": 1,
        "status": "draft",
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return BookRecord(**data)


def _act_record(**overrides):
    data = {
        "act_id": "ACT-001",
        "book_id": "BOOK-alpha",
        "order": 1,
        "name": None,
        "purpose": "Open the story engine",
        "opening_state": {"tension": "low"},
        "closing_state": {"tension": "higher"},
        "main_conflict": "The promise meets resistance",
        "turning_point": "The door closes",
        "target_tension_start": 2,
        "target_tension_end": 5,
        "status": "planned",
        "version": 1,
    }
    data.update(overrides)
    return ActRecord(**data)


def _sequence_record(**overrides):
    data = {
        "sequence_id": "SEQ-001",
        "act_id": "ACT-001",
        "order": 1,
        "purpose": "Move the protagonist into trouble",
        "main_goal": "Reach the archive",
        "main_conflict": "The route is blocked",
        "entry_state": {"location": "street"},
        "exit_state": {"location": "archive"},
        "turning_point": "A hidden route opens",
        "target_tension": 4,
        "target_pace": 3,
        "status": "planned",
        "version": 1,
    }
    data.update(overrides)
    return SequenceRecord(**data)


def _chapter_record(**overrides):
    data = {
        "chapter_id": "CHAPTER-001",
        "project_id": "PROJ-alpha",
        "book_id": "BOOK-alpha",
        "sequence_id": "SEQ-001",
        "order": 1,
        "title": "First Door",
        "purpose": "Establish the promise",
        "entry_state": {"promise": "unmet"},
        "exit_state": {"promise": "pressing"},
        "scene_ids": ["SCENE-001"],
        "status": "draft",
        "current_version": 1,
        "canon_status": "pending",
        "style_status": "pending",
        "quality_status": "pending",
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return ChapterRecord(**data)


def _scene_contract(**overrides):
    data = {
        "scene_id": "SCENE-001",
        "project_id": "PROJ-alpha",
        "chapter_id": "CHAPTER-001",
        "order": 1,
        "narrative_order": 42,
        "pov_character_id": "CHAR-ada",
        "participant_character_ids": ["CHAR-ada", "CHAR-bo"],
        "location_id": "PLACE-archive",
        "route_id": "ROUTE-west",
        "time_start": "day-1 dawn",
        "time_end": "day-1 morning",
        "purpose": "Expose the core problem",
        "goal": "Find the first clue",
        "obstacle": "The shelf has been emptied",
        "conflict": "Ada must trust Bo",
        "stakes": "The archive may close",
        "outcome": "They find a marker",
        "state_change": "Ada knows the map is incomplete",
        "facts_required": ["FACT-map-exists"],
        "facts_created": ["FACT-marker-found"],
        "facts_revealed": ["FACT-archive-breach"],
        "must_include_facts": ["FACT-map-exists"],
        "must_include_events": ["EVENT-breach"],
        "must_include_threads": ["THREAD-archive"],
        "must_include_character_states": ["CONTEXT-ada-trust"],
        "threads_opened": ["THREAD-archive"],
        "threads_progressed": ["THREAD-archive"],
        "threads_closed": [],
        "setups_created": ["SETUP-marker"],
        "payoffs_completed": ["PAYOFF-old-map"],
        "reader_knowledge_added": ["CONTEXT-reader-marker"],
        "target_tension": 5,
        "target_pace": 4,
        "status": "planned",
        "version": 1,
    }
    data.update(overrides)
    return SceneContract(**data)


@pytest.mark.parametrize("namespace", list(DomainNamespace))
def test_valid_domain_id_for_each_implemented_namespace(namespace: DomainNamespace) -> None:
    domain_id = build_domain_id(namespace, "000001")

    assert domain_id.namespace == namespace
    assert domain_id.value == f"{namespace.value}-000001"
    assert parse_domain_id(domain_id.value) == domain_id
    assert domain_id.to_dict() == {
        "namespace": namespace.value,
        "value": f"{namespace.value}-000001",
    }


@pytest.mark.parametrize("bad_value", ["", " PROJ-1", "PROJ-1 ", "PROJ-", "PROJ-../x", "PROJ-a/b", "PROJ-a\\b"])
def test_domain_id_rejects_empty_and_unsafe_values(bad_value: str) -> None:
    with pytest.raises(DomainContractError):
        DomainId(DomainNamespace.PROJECT, bad_value)


def test_domain_id_rejects_wrong_or_unknown_prefix() -> None:
    with pytest.raises(DomainContractError, match="PROJ- namespace"):
        DomainId(DomainNamespace.PROJECT, "BOOK-000001")

    with pytest.raises(DomainContractError, match="unsupported"):
        parse_domain_id("UNKNOWN-000001")


def test_domain_id_is_immutable_hashable_and_deterministically_serialized() -> None:
    first = DomainId(DomainNamespace.SCENE, "SCENE-000100")
    second = DomainId(DomainNamespace.SCENE, "SCENE-000100")

    assert first == second
    assert hash(first) == hash(second)
    assert first.to_json() == '{"namespace":"SCENE","value":"SCENE-000100"}'

    with pytest.raises(FrozenInstanceError):
        first.value = "SCENE-000101"  # type: ignore[misc]


def test_same_number_in_different_namespaces_is_not_same_entity() -> None:
    project = DomainId(DomainNamespace.PROJECT, "PROJ-000001")
    book = DomainId(DomainNamespace.BOOK, "BOOK-000001")
    scene = DomainId(DomainNamespace.SCENE, "SCENE-000001")

    assert project != book
    assert book != scene
    assert {project, book, scene} == {project, book, scene}


def test_rename_order_and_version_do_not_change_record_identity() -> None:
    book = _book_record()
    renamed = book.with_updates(title="Renamed book")
    assert renamed.book_id == book.book_id

    scene = _scene_contract()
    reordered = scene.with_updates(order=99, narrative_order=1)
    versioned = scene.with_updates(version=2)

    assert reordered.scene_id == scene.scene_id
    assert versioned.scene_id == scene.scene_id
    assert reordered.narrative_order == 1


def test_id_does_not_define_narrative_order() -> None:
    early = _scene_contract(scene_id="SCENE-000100", order=1, narrative_order=1)
    late = _scene_contract(scene_id="SCENE-000099", order=2, narrative_order=2)

    assert early.scene_id.value > late.scene_id.value
    assert early.narrative_order < late.narrative_order


def test_core_records_validate_expected_id_namespaces() -> None:
    assert _project_record().to_dict()["project_id"] == "PROJ-alpha"
    assert _series_record().to_dict()["series_id"] == "SERIES-alpha"
    assert _book_record(series_id="SERIES-alpha").to_dict()["series_id"] == "SERIES-alpha"
    assert _act_record().to_dict()["act_id"] == "ACT-001"
    assert _sequence_record().to_dict()["sequence_id"] == "SEQ-001"
    assert _chapter_record().to_dict()["chapter_id"] == "CHAPTER-001"
    assert _scene_contract().to_dict()["scene_id"] == "SCENE-001"


def test_records_reject_cross_namespace_identity_mixups() -> None:
    with pytest.raises(DomainContractError, match="book_id"):
        _project_record(book_id="PROJ-alpha")

    with pytest.raises(DomainContractError, match="project_id"):
        _book_record(project_id="BOOK-alpha")

    with pytest.raises(DomainContractError, match="scene_id"):
        _scene_contract(scene_id="BOOK-alpha")

    with pytest.raises(DomainContractError, match="facts_required"):
        _scene_contract(facts_required=["SCENE-001"])


def test_records_reject_invalid_required_fields_and_versions() -> None:
    with pytest.raises(DomainContractError, match="title"):
        _project_record(title="")

    with pytest.raises(DomainContractError, match="version"):
        _act_record(version=0)

    with pytest.raises(DomainContractError, match="order"):
        _chapter_record(order=-1)

    with pytest.raises(DomainContractError, match="scene_ids"):
        _chapter_record(scene_ids="SCENE-001")


def test_record_serialization_is_deterministic() -> None:
    first = _act_record(opening_state={"b": 2, "a": 1})
    second = _act_record(opening_state={"a": 1, "b": 2})

    assert first.to_json() == second.to_json()
    assert '"act_id":"ACT-001"' in first.to_json()
    assert '"opening_state":{"a":1,"b":2}' in first.to_json()


def test_project_and_series_records_bind_to_gap004_storage_scope(
    isolated_agentpro_storage,
) -> None:
    project = _project_record(project_id="PROJ-scope", book_id="BOOK-scope")
    series = _series_record(series_id="SERIES-scope")

    assert project.scope == StorageScope.project("PROJ-scope")
    assert series.scope == StorageScope.series("SERIES-scope")
    project.require_scope(StorageScope.project("PROJ-scope"))
    series.require_scope(StorageScope.series("SERIES-scope"))

    with pytest.raises(DomainContractError, match="project record scope"):
        project.require_scope(StorageScope.project("PROJ-other"))

    with pytest.raises(DomainContractError, match="series record scope"):
        series.require_scope(StorageScope.series("SERIES-other"))

    project_repo = ProjectRepository(StorageResolver().resolve_project(
        str(project.project_id), book_id=str(project.book_id)
    ))
    project_repo.set_scoped_metadata(project.scope, "project_record", project.to_json())
    assert project_repo.get_metadata("project_record") == project.to_json()
    assert project_repo.db_path.is_relative_to(isolated_agentpro_storage)


def test_book_chapter_and_scene_records_reject_foreign_project_scope() -> None:
    book = _book_record(project_id="PROJ-scope")
    chapter = _chapter_record(project_id="PROJ-scope")
    scene = _scene_contract(project_id="PROJ-scope")

    for record in (book, chapter, scene):
        record.require_project_scope(StorageScope.project("PROJ-scope"))
        with pytest.raises(DomainContractError, match="project scope"):
            record.require_project_scope(StorageScope.project("PROJ-foreign"))

from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.adaptive_style import (
    AdaptiveStyleSession,
    AdaptiveStyleError,
    BookStyleDNA,
    GenomeBound,
    SceneIndexKey,
    StyleComposer,
    StyleCritic,
    StyleGenome,
    StyleLibraryProfile,
    StylePerformanceRecord,
    StyleRepository,
    prepare_adaptive_style_session,
)
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.project_repository import (
    PROJECT_DB_SCHEMA_VERSION,
    ProjectRepository,
    ProjectStorageError,
    StorageResolver,
)
from app.p20_core.context_runtime import (
    ProjectExecutionContext,
    build_runtime_context_package,
)
from app.p20_core.storage_paths import get_runs_root, get_storage_root


def genome(value: float = 0.5) -> StyleGenome:
    return StyleGenome(
        pacing=value,
        dialogue_density=value,
        suspense=value,
        syntax_variation=value,
        sentence_length_mean=value,
        sentence_length_variance=value,
        lexical_register="NEUTRAL",
        figurative_density=value,
        pov_intimacy=value,
    )


def dna(book_id: str, version: int = 1, target: float = 0.5) -> BookStyleDNA:
    return BookStyleDNA(
        book_id=book_id,
        version=version,
        genome_bounds={
            name: GenomeBound(max(0, target - 0.2), min(1, target + 0.2), target)
            for name in (
                "pacing", "dialogue_density", "suspense", "syntax_variation",
                "sentence_length_mean", "sentence_length_variance",
                "figurative_density", "pov_intimacy",
            )
        },
        lexical_register="NEUTRAL",
        locked_identity_markers=("pov_intimacy", "lexical_register"),
        derived_from=version,
    )


def scene_key(version: int = 1, *, pov: str = "close-third") -> SceneIndexKey:
    return SceneIndexKey(
        scene_type="dialogue",
        narrative_function="revelation",
        pov=pov,
        target_tension=0.7,
        target_pace=0.6,
        book_style_dna_version=version,
    )


def library() -> StyleLibraryProfile:
    return StyleLibraryProfile(
        profile_id="STYLE-LIB-neutral-dialogue",
        source_ref="curated technique set A",
        extracted_techniques=("short-burst dialogue", "controlled subtext"),
        genome=genome(0.6),
        applicability_tags=("dialogue",),
        version=1,
    )


def repository(project_id: str, book_id: str) -> ProjectRepository:
    repo = ProjectRepository(StorageResolver().resolve_project(project_id, book_id=book_id))
    repo.initialize()
    return repo


def test_dna_persistence_reopen_versioning_and_project_isolation(isolated_agentpro_storage):
    first = repository("PROJ-style-a", "BOOK-style-a")
    styles = StyleRepository(first)
    styles.save_book_style_dna(dna("BOOK-style-a", 1, 0.4))
    styles.save_book_style_dna(dna("BOOK-style-a", 2, 0.6))

    reopened = StyleRepository(repository("PROJ-style-a", "BOOK-style-a"))
    assert [item.version for item in reopened.list_book_style_dna()] == [1, 2]
    assert reopened.get_active_book_style_dna().version == 2

    foreign = StyleRepository(repository("PROJ-style-b", "BOOK-style-b"))
    assert foreign.get_active_book_style_dna() is None
    assert foreign.list_performance() == ()

    with pytest.raises(ProjectStorageError, match="newer active version"):
        styles.save_book_style_dna(dna("BOOK-style-a", 1, 0.4))


def test_composer_is_deterministic_bounded_and_uses_full_scene_index(isolated_agentpro_storage):
    active_dna = dna("BOOK-style-compose")
    profile = library()
    composer = StyleComposer()
    first = composer.compose(
        dna=active_dna,
        scene_id="SCENE-style-001",
        scene_index_key=scene_key(),
        library=(profile,),
        history=(),
    )
    second = composer.compose(
        dna=active_dna,
        scene_id="SCENE-style-001",
        scene_index_key=scene_key(),
        library=(profile,),
        history=(),
    )
    other_pov = composer.compose(
        dna=active_dna,
        scene_id="SCENE-style-001",
        scene_index_key=scene_key(pov="first-person"),
        library=(profile,),
        history=(),
    )

    assert first.to_dict() == second.to_dict()
    assert active_dna.contains(first.target_genome)
    assert first.target_genome.pov_intimacy == active_dna.genome_bounds["pov_intimacy"].target
    assert first.recipe_hash != other_pov.recipe_hash
    assert "write like" not in json.dumps(first.to_dict()).lower()


def test_quality_gate_controls_performance_idempotently_and_old_dna_decays(isolated_agentpro_storage):
    repo = repository("PROJ-style-gate", "BOOK-style-gate")
    style_repo = StyleRepository(repo)
    active_dna = dna("BOOK-style-gate")
    style_repo.save_book_style_dna(active_dna)
    recipe = StyleComposer().compose(
        dna=active_dna,
        scene_id="SCENE-style-gate",
        scene_index_key=scene_key(),
        library=(library(),),
        history=(),
    )
    style_repo.save_recipe(recipe)
    session = AdaptiveStyleSession(style_repo, active_dna, recipe)
    evaluation = session.evaluate(
        artifact_id="ART-style-gate",
        text="Neutral synthetic scene artifact.",
        achieved_genome=recipe.target_genome.to_dict(),
    )
    assert evaluation.style_status == "COMPLIANT"
    assert len(style_repo.list_evaluations()) == 1

    for decision in ("REVISE", "REJECT"):
        assert session.finalize(
            quality_decision=decision,
            quality_score=0.8,
            quality_evaluation_id=f"QUALITY-{decision}",
            quality_artifact_hash=hashlib.sha256("Neutral synthetic scene artifact.".encode()).hexdigest(),
            artifact_id="ART-style-gate",
            artifact_text="Neutral synthetic scene artifact.",
        ) is None
    assert style_repo.list_performance() == ()

    accepted = session.finalize(
        quality_decision="ACCEPT",
        quality_score=0.9,
        quality_evaluation_id="QUALITY-ACCEPT",
        quality_artifact_hash=hashlib.sha256("Neutral synthetic scene artifact.".encode()).hexdigest(),
        artifact_id="ART-style-gate",
        artifact_text="Neutral synthetic scene artifact.",
    )
    retried = session.finalize(
        quality_decision="ACCEPT",
        quality_score=0.9,
        quality_evaluation_id="QUALITY-ACCEPT",
        quality_artifact_hash=hashlib.sha256("Neutral synthetic scene artifact.".encode()).hexdigest(),
        artifact_id="ART-style-gate",
        artifact_text="Neutral synthetic scene artifact.",
    )
    assert accepted == retried
    assert len(style_repo.list_performance()) == 1
    assert accepted.decayed_weight(1) == 0.9
    assert accepted.decayed_weight(2) == 0.225

    new_dna = dna("BOOK-style-gate", 2, 0.5)
    current_record = StylePerformanceRecord.from_dict(
        {
            **accepted.to_dict(),
            "performance_id": "STYLE-PERF-current-version",
            "scene_index_key": scene_key(2).to_dict(),
            "dna_version": 2,
            "achieved_genome": genome(0.7).to_dict(),
        }
    )
    old_record = StylePerformanceRecord.from_dict(
        {
            **accepted.to_dict(),
            "performance_id": "STYLE-PERF-old-version",
            "achieved_genome": genome(0.7).to_dict(),
        }
    )
    without_history = StyleComposer().compose(
        dna=new_dna,
        scene_id="SCENE-style-decay",
        scene_index_key=scene_key(2),
        library=(),
        history=(),
    )
    with_current = StyleComposer().compose(
        dna=new_dna,
        scene_id="SCENE-style-decay",
        scene_index_key=scene_key(2),
        library=(),
        history=(current_record,),
    )
    with_old = StyleComposer().compose(
        dna=new_dna,
        scene_id="SCENE-style-decay",
        scene_index_key=scene_key(2),
        library=(),
        history=(old_record,),
    )
    current_influence = abs(with_current.target_genome.pacing - without_history.target_genome.pacing)
    old_influence = abs(with_old.target_genome.pacing - without_history.target_genome.pacing)
    assert 0 < old_influence < current_influence


def test_noncompliant_style_cannot_create_performance(isolated_agentpro_storage):
    repo = repository("PROJ-style-negative", "BOOK-style-negative")
    active_dna = dna("BOOK-style-negative")
    style_repo = StyleRepository(repo)
    style_repo.save_book_style_dna(active_dna)
    recipe = StyleComposer().compose(
        dna=active_dna,
        scene_id="SCENE-style-negative",
        scene_index_key=scene_key(),
        library=(),
        history=(),
    )
    session = AdaptiveStyleSession(style_repo, active_dna, recipe)
    evaluation = session.evaluate(
        artifact_id="ART-style-negative",
        text="Neutral synthetic scene artifact.",
        achieved_genome=genome(0.95).to_dict(),
    )
    assert evaluation.style_status == "NONCOMPLIANT"
    assert session.finalize(
        quality_decision="ACCEPT",
        quality_score=1,
        quality_evaluation_id="QUALITY-negative",
        quality_artifact_hash=hashlib.sha256("Neutral synthetic scene artifact.".encode()).hexdigest(),
        artifact_id="ART-style-negative",
        artifact_text="Neutral synthetic scene artifact.",
    ) is None
    assert style_repo.list_performance() == ()

    missing_writer_evidence = StyleCritic().evaluate(
        dna=active_dna,
        recipe=recipe,
        artifact_id="ART-style-missing-evidence",
        text="Neutral synthetic scene without achieved style telemetry.",
    )
    assert missing_writer_evidence.style_status == "NONCOMPLIANT"
    assert missing_writer_evidence.dna_compliance is False


def test_project_schema_v4_migrates_to_adaptive_style_storage(isolated_agentpro_storage):
    repo = repository("PROJ-style-migration", "BOOK-style-migration")
    repo.set_metadata("preserved", "yes")
    with repo.connect() as connection:
        for table in ("translation_request_receipts", "translation_heads", "translation_records",
                      "gap018_manuscript_head", "gap018_source_head", "gap018_records"):
            connection.execute(f"DROP TABLE {table}")
        for table in (
            "style_library_profiles",
            "book_style_dna",
            "scene_style_recipes",
            "style_evaluations",
            "style_performance_records",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DROP TABLE memory_event_entities")
        connection.execute("DROP TABLE memory_events")
        connection.execute(
            "DELETE FROM project_metadata WHERE key='memory_ledger_control.v1'"
        )
        connection.execute("UPDATE schema_version SET version=4 WHERE id=1")
        connection.execute("UPDATE project_identity SET schema_version=4 WHERE id=1")
    assert repo.inspect_schema().current_version == 4
    assert repo.migrate_schema(target_version=5).current_version == 5
    assert repo.migrate_schema(
        backup_path=isolated_agentpro_storage / "gap014-ledger.backup",
        maintenance_confirmed=True,
        release_head="TEST-HEAD",
    ).current_version == PROJECT_DB_SCHEMA_VERSION
    assert repo.get_metadata("preserved") == "yes"
    StyleRepository(repo).save_book_style_dna(dna("BOOK-style-migration"))
    assert StyleRepository(repo).get_active_book_style_dna().version == 1


def _scene_contract(project_id: str) -> dict:
    return {
        "scene_id": "SCENE-gap014-api",
        "project_id": project_id,
        "chapter_id": "CHAPTER-gap014-api",
        "order": 1,
        "narrative_order": 1,
        "pov_character_id": "CHAR-gap014-pov",
        "participant_character_ids": ["CHAR-gap014-pov"],
        "location_id": None,
        "route_id": None,
        "time_start": "T0",
        "time_end": "T1",
        "purpose": "revelation",
        "goal": "test goal",
        "obstacle": "test obstacle",
        "conflict": "test conflict",
        "stakes": "test stakes",
        "outcome": "test outcome",
        "state_change": "test state change",
        "facts_required": [],
        "facts_created": [],
        "facts_revealed": [],
        "must_include_facts": [],
        "must_include_events": [],
        "must_include_threads": [],
        "must_include_character_states": [],
        "threads_opened": [],
        "threads_progressed": [],
        "threads_closed": [],
        "setups_created": [],
        "payoffs_completed": [],
        "reader_knowledge_added": [],
        "target_tension": 5,
        "target_pace": 4,
        "status": "READY_TO_WRITE",
        "version": 1,
    }


def _adaptive_payload(project_id: str) -> dict:
    return {
        "scene_type": "dialogue",
        "scene_contract": _scene_contract(project_id),
    }


def test_runtime_cannot_mutate_style_sources_and_active_dna_cannot_be_skipped(
    isolated_agentpro_storage,
):
    repo = repository("PROJ-style-runtime-guard", "BOOK-style-runtime-guard")
    styles = StyleRepository(repo)
    styles.save_book_style_dna(dna("BOOK-style-runtime-guard"))
    styles.save_library_profile(library())

    with pytest.raises(AdaptiveStyleError, match="runtime payload cannot modify"):
        prepare_adaptive_style_session(
            repo,
            {
                "adaptive_style": {
                    **_adaptive_payload("PROJ-style-runtime-guard"),
                    "book_style_dna": dna("BOOK-style-runtime-guard", 2).to_dict(),
                }
            },
            require_style=True,
        )
    assert styles.get_active_book_style_dna().version == 1

    with pytest.raises(AdaptiveStyleError, match="scene context is required"):
        prepare_adaptive_style_session(repo, {}, require_style=True)


def test_context_hash_changes_with_active_dna_version(isolated_agentpro_storage):
    project_id = "PROJ-style-context-hash"
    book_id = "BOOK-style-context-hash"
    repo = repository(project_id, book_id)
    styles = StyleRepository(repo)
    styles.save_book_style_dna(dna(book_id, 1, 0.4))
    execution = ProjectExecutionContext.create(
        project_id=project_id,
        book_id=book_id,
        series_id=None,
        run_id="run-style-context-hash",
        step_id="step-style-context-hash",
    )
    arguments = {
        "execution_context": execution,
        "mode": "WRITE",
        "role": "AUTHOR",
        "requested_model": None,
        "effective_model": "test-model",
        "tool_input": {"input": "neutral synthetic input"},
        "context_sources": {},
    }
    first = build_runtime_context_package(**arguments)
    styles.save_book_style_dna(dna(book_id, 2, 0.6))
    with repo.connect() as connection:
        connection.execute(
            "DELETE FROM context_packages WHERE scope_type='PROJECT' AND scope_id=? AND operation_id=?",
            (project_id, execution.operation_id),
        )
    second = build_runtime_context_package(**arguments)
    assert first.style_version == 1 and second.style_version == 2
    assert first.context_hash != second.context_hash


def test_agent_step_p20_style_flow_context_memory_and_audit(
    isolated_agentpro_storage,
    monkeypatch,
):
    monkeypatch.setenv("AGENT_TEST_MODE", "1")
    storage_book_id = "gap014_api_book"
    project_id = "PROJ-gap014-api"
    domain_book_id = "BOOK-gap014_api_book"
    run_id = "run-gap014-api"
    ensure_test_book_bible(storage_book_id)
    style_repo = StyleRepository(repository(project_id, domain_book_id))
    style_repo.save_book_style_dna(dna(domain_book_id))
    style_repo.save_library_profile(library())
    payload = {
        "input": " ".join(["neutral synthetic narrative sentence"] * 160),
        "adaptive_style": _adaptive_payload(project_id),
    }
    response = TestClient(app).post(
        "/agent/step",
        json={
            "project_id": project_id,
            "book_id": storage_book_id,
            "run_id": run_id,
            "modes": ["WRITE", "QUALITY"],
            "payload": payload,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quality_gate"]["decision"] == "ACCEPT", body
    assert body["adaptive_style"]["performance_record"]["status"] == "ACCEPTED"

    repo = repository(project_id, domain_book_id)
    style_repo = StyleRepository(repo)
    assert len(style_repo.list_performance()) == 1
    assert len(style_repo.list_evaluations()) == 1

    step_docs = [
        json.loads((get_storage_root() / path).read_text(encoding="utf-8"))
        for path in body["artifact_paths"]
    ]
    write_doc = next(item for item in step_docs if item["mode"] == "WRITE")
    quality_doc = next(item for item in step_docs if item["mode"] == "QUALITY")
    features = write_doc["input"]["style_features"]
    assert set(features) == {"target_genome", "selected_techniques", "recipe_id", "recipe_hash"}
    assert "source_ref" not in json.dumps(features)
    context_items = write_doc["input"]["_context_package"]["included_items"]
    assert any(item["layer"] == "STYLE" for item in context_items)
    assert any(
        item["entity_type"] == "SCENE" and item["reason"] == "active SceneContract"
        for item in context_items
    )
    assert quality_doc["adaptive_style"]["performance_record"]["accepted_artifact_hash"] == quality_doc["adaptive_style"]["evaluation"]["artifact_hash"]
    assert quality_doc["result"]["payload"]["meta"]["artifact_hash"] == quality_doc["adaptive_style"]["evaluation"]["artifact_hash"]

    audit = json.loads((get_runs_root() / run_id / "audit.json").read_text(encoding="utf-8"))
    assert audit["adaptive_style"]["recipe"]["recipe_hash"] == features["recipe_hash"]
    assert audit["adaptive_style"]["performance_record"]["status"] == "ACCEPTED"

    omitted = TestClient(app).post(
        "/agent/step",
        json={
            "project_id": project_id,
            "book_id": storage_book_id,
            "run_id": "run-gap014-omitted-style",
            "mode": "WRITE",
            "payload": {"input": "neutral synthetic input"},
        },
    )
    assert omitted.status_code == 422
    assert "scene context is required" in omitted.json()["detail"]

    mutation_attempt = TestClient(app).post(
        "/agent/step",
        json={
            "project_id": project_id,
            "book_id": storage_book_id,
            "run_id": "run-gap014-style-mutation",
            "mode": "WRITE",
            "payload": {
                "input": "neutral synthetic input",
                "adaptive_style": {
                    **_adaptive_payload(project_id),
                    "book_style_dna": dna(domain_book_id, 2).to_dict(),
                },
            },
        },
    )
    assert mutation_attempt.status_code == 422
    assert style_repo.get_active_book_style_dna().version == 1

    retry = TestClient(app).post(
        "/agent/step",
        json={
            "project_id": project_id,
            "book_id": storage_book_id,
            "run_id": run_id,
            "step_id": body["step_id"],
            "technical_retry": True,
            "modes": ["WRITE", "QUALITY"],
            "payload": payload,
        },
    )
    assert retry.status_code == 200, retry.text
    assert len(StyleRepository(repository(project_id, domain_book_id)).list_performance()) == 1

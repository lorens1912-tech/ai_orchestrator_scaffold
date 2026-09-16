from __future__ import annotations

import json
import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.operator_dpapi import read_secret
from app.p20_core.adaptive_style import (
    AdaptiveStyleError,
    BookStyleDNA,
    GenomeBound,
    SceneIndexKey,
    StyleComposer,
    StyleCritic,
    StyleGenome,
    StyleLibraryProfile,
    StyleRepository,
)
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.local_operator import initialize_operator
from app.p20_core.project_repository import (
    ProjectRepository,
    ProjectStorageError,
    StorageResolver,
    ensure_system_repository,
)
from app.p20_core.storage_paths import get_runs_root, get_storage_root
from app.p20_core.style_library_catalog import (
    CATALOG_PROFILE_SET_HASH,
    EXPECTED_PROFILE_NAMES,
    catalog_profiles,
    seed_curated_style_library,
)


FEATURES = (
    "pacing",
    "dialogue_density",
    "suspense",
    "syntax_variation",
    "sentence_length_mean",
    "sentence_length_variance",
    "figurative_density",
    "pov_intimacy",
)


def repository(project_id: str, book_id: str) -> ProjectRepository:
    value = ProjectRepository(StorageResolver().resolve_project(project_id, book_id=book_id))
    value.initialize()
    return value


def dna(book_id: str, *, target: float = 0.4) -> BookStyleDNA:
    return BookStyleDNA(
        book_id=book_id,
        version=1,
        genome_bounds={name: GenomeBound(0.2, 0.6, target) for name in FEATURES},
        lexical_register="NEUTRAL",
        locked_identity_markers=("pov_intimacy", "lexical_register"),
        derived_from=1,
    )


def scene_key(*, scene_type: str = "synthetic-scene", purpose: str = "synthetic-purpose") -> SceneIndexKey:
    return SceneIndexKey(
        scene_type=scene_type,
        narrative_function=purpose,
        pov="CHAR-SYNTHETIC-POV",
        target_tension=0.7,
        target_pace=0.6,
        book_style_dna_version=1,
    )


def profile(
    profile_id: str,
    display_name: str,
    genome: StyleGenome,
    technique: str,
) -> StyleLibraryProfile:
    return StyleLibraryProfile(
        profile_id=profile_id,
        display_name=display_name,
        source_ref=f"synthetic curated source {profile_id}",
        source_version="v1",
        source_hash="a" * 64,
        extracted_techniques=(technique,),
        genome=genome,
        applicability_tags=("synthetic-scene",),
        version=1,
        source_metadata={"provenance": "neutral synthetic test"},
    )


def scene_contract(project_id: str) -> dict:
    return {
        "scene_id": "SCENE-style-library-api",
        "project_id": project_id,
        "chapter_id": "CHAPTER-style-library-api",
        "order": 1,
        "narrative_order": 1,
        "pov_character_id": "CHAR-style-library-pov",
        "participant_character_ids": ["CHAR-style-library-pov"],
        "location_id": None,
        "route_id": None,
        "time_start": "T0",
        "time_end": "T1",
        "purpose": "research-integration",
        "goal": "synthetic test goal",
        "obstacle": "synthetic test obstacle",
        "conflict": "synthetic test conflict",
        "stakes": "synthetic test stakes",
        "outcome": "synthetic test outcome",
        "state_change": "synthetic test state change",
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


def test_catalog_is_exactly_verified_24_and_preserves_sparse_source_semantics():
    profiles = catalog_profiles()
    assert len(profiles) == len(EXPECTED_PROFILE_NAMES) == 24
    assert tuple(item.display_name for item in profiles) == EXPECTED_PROFILE_NAMES
    assert len({item.profile_id for item in profiles}) == 24
    assert len({item.display_name.casefold() for item in profiles}) == 24
    assert CATALOG_PROFILE_SET_HASH == "ffb23daa35b5c5dfd2bc8f76aec30460f44f0b1d63ead0c4ae6020eeb0b04893"

    by_name = {item.display_name: item for item in profiles}
    assert by_name["Freida McFadden"].genome.to_dict() == {"pacing": 0.95}
    assert by_name["Dan Brown"].genome.to_dict() == {}
    assert by_name["Dan Brown"].extracted_techniques
    assert by_name["Dan Brown"].applicability_tags
    assert by_name["Dan Brown"].source_metadata["raw_profile_markdown"]
    assert sum(bool(item.genome.observed_features) for item in profiles) == 5
    assert all(None not in item.genome.to_dict().values() for item in profiles)


def test_seed_reopen_lookup_isolation_idempotency_and_conflicts(isolated_agentpro_storage):
    first = StyleRepository(repository("PROJ-library-a", "BOOK-library-a"))
    result = seed_curated_style_library(first)
    assert result["profiles_loaded"] == 24
    assert result["unique_authors"] == 24
    assert result["duplicates"] == 0
    before = tuple(item.to_dict() for item in first.list_library_profiles())

    assert seed_curated_style_library(first) == result
    reopened = StyleRepository(repository("PROJ-library-a", "BOOK-library-a"))
    assert tuple(item.to_dict() for item in reopened.list_library_profiles()) == before
    selected_by_name = reopened.get_library_profile_by_name("dan brown")
    selected_by_id = reopened.get_library_profile(selected_by_name.profile_id)
    assert selected_by_name == selected_by_id

    foreign = StyleRepository(repository("PROJ-library-b", "BOOK-library-b"))
    assert foreign.list_library_profiles() == ()
    with pytest.raises(AdaptiveStyleError, match="unknown Style Library profile"):
        foreign.select_library_profiles(names=("Dan Brown",))

    with pytest.raises(ProjectStorageError, match="different content"):
        first.save_library_profile(replace(selected_by_name, source_ref="changed synthetic source"))
    with pytest.raises(ProjectStorageError, match="different profile"):
        first.save_library_profile(replace(selected_by_name, profile_id="STYLE-LIB-CONFLICT"))


def test_sparse_composition_is_per_feature_complete_bounded_and_auditable():
    active_dna = dna("BOOK-sparse")
    pacing = profile("PROFILE-PACING", "Pacing Source", StyleGenome(pacing=0.8), "pace technique")
    suspense = profile("PROFILE-SUSPENSE", "Suspense Source", StyleGenome(suspense=0.2), "suspense technique")
    technique_only = profile("PROFILE-TECHNIQUE", "Technique Source", StyleGenome(), "structure technique")
    recipe = StyleComposer().compose(
        dna=active_dna,
        scene_id="SCENE-sparse",
        scene_index_key=scene_key(),
        library=(technique_only, suspense, pacing),
        history=(),
    )

    assert recipe.target_genome.missing_features == ()
    assert active_dna.contains(recipe.target_genome)
    assert recipe.target_genome.pacing == pytest.approx(0.5)
    assert recipe.target_genome.suspense == pytest.approx(0.35)
    for name in set(FEATURES) - {"pacing", "suspense"}:
        assert getattr(recipe.target_genome, name) == pytest.approx(0.4)
    assert recipe.target_genome.pov_intimacy == active_dna.genome_bounds["pov_intimacy"].target
    assert set(recipe.selected_techniques) == {"pace technique", "suspense technique", "structure technique"}
    provenance = "\n".join(recipe.provenance)
    assert "StyleFeature:pacing:from=PROFILE-PACING:v1" in provenance
    assert "StyleFeature:suspense:from=PROFILE-SUSPENSE:v1" in provenance
    assert "StyleFeature:dialogue_density" not in provenance
    assert "StyleTechnique:structure technique:from=PROFILE-TECHNIQUE:v1" in provenance
    with pytest.raises(AdaptiveStyleError, match="requires complete StyleGenome"):
        replace(recipe, target_genome=StyleGenome(pacing=0.5))
    with pytest.raises(AdaptiveStyleError, match="requires complete StyleGenome"):
        StyleCritic().evaluate(
            dna=active_dna,
            recipe=recipe,
            artifact_id="ART-sparse-invalid",
            text="Neutral synthetic text.",
            achieved_genome=StyleGenome(pacing=0.5),
        )


def test_authenticated_operator_seed_list_and_stable_selection(isolated_agentpro_storage, tmp_path):
    if os.name != "nt":
        pytest.skip("Actual Windows DPAPI test")
    project_id = "PROJ-library-operator"
    book_id = "BOOK-library-operator"
    system = ensure_system_repository()
    system.bind_project(project_id, book_id)
    repository(project_id, book_id)
    secret_path = tmp_path / "operator" / "operator.dpapi"
    initialize_operator(system, secret_path)
    token = read_secret(secret_path)
    headers = {"Authorization": f"Bearer {token}"}
    base = f"/operator/projects/{project_id}/style-library"

    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50100)) as client:
        assert client.post(base + "/seed").status_code == 401
        seeded = client.post(base + "/seed", headers=headers)
        assert seeded.status_code == 200, seeded.text
        assert seeded.json()["profiles_loaded"] == 24
        assert client.post(base + "/seed", headers=headers).json() == seeded.json()
        listed = client.get(base, headers=headers).json()["profiles"]
        assert len(listed) == 24
        by_name = client.get(base + "/by-name/Dan%20Brown", headers=headers)
        assert by_name.status_code == 200, by_name.text
        profile_id = by_name.json()["profile_id"]
        assert client.get(base + f"/by-id/{profile_id}", headers=headers).json() == by_name.json()
        assert "raw_profile_markdown" not in json.dumps(by_name.json())


def test_agent_step_uses_selected_profiles_and_keeps_library_immutable(
    isolated_agentpro_storage,
    monkeypatch,
):
    monkeypatch.setenv("AGENT_TEST_MODE", "1")
    storage_book_id = "style_library_api_book"
    project_id = "PROJ-style-library-api"
    domain_book_id = "BOOK-style_library_api_book"
    run_id = "run-style-library-api"
    ensure_test_book_bible(storage_book_id)
    styles = StyleRepository(repository(project_id, domain_book_id))
    styles.save_book_style_dna(dna(domain_book_id))
    seed_curated_style_library(styles)
    before = tuple(item.to_dict() for item in styles.list_library_profiles())
    payload = {
        "input": " ".join(["neutral synthetic narrative sentence"] * 160),
        "adaptive_style": {
            "scene_type": "research-integration",
            "scene_contract": scene_contract(project_id),
            "library_profile_names": ["Dan Brown", "Graham Brown"],
        },
    }
    request = {
        "project_id": project_id,
        "book_id": storage_book_id,
        "run_id": run_id,
        "modes": ["WRITE", "QUALITY"],
        "payload": payload,
    }
    response = TestClient(app).post("/agent/step", json=request)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quality_gate"]["decision"] == "ACCEPT"
    assert body["adaptive_style"]["performance_record"]["status"] == "ACCEPTED"

    documents = [
        json.loads((get_storage_root() / path).read_text(encoding="utf-8"))
        for path in body["artifact_paths"]
    ]
    write = next(item for item in documents if item["mode"] == "WRITE")
    features = write["input"]["style_features"]
    assert set(features["target_genome"]) == set(FEATURES) | {"lexical_register"}
    serialized_features = json.dumps(features)
    assert "Dan Brown" not in serialized_features
    assert "Graham Brown" not in serialized_features
    assert "source_ref" not in serialized_features
    audit = json.loads((get_runs_root() / run_id / "audit.json").read_text(encoding="utf-8"))
    provenance = "\n".join(audit["adaptive_style"]["recipe"]["provenance"])
    assert "STYLE-LIB-AUTHOR-dan-brown" in provenance
    assert "STYLE-LIB-AUTHOR-graham-brown" in provenance
    assert "StyleTechnique:" in provenance

    reopened = StyleRepository(repository(project_id, domain_book_id))
    assert tuple(item.to_dict() for item in reopened.list_library_profiles()) == before
    assert len(reopened.list_performance()) == 1
    retry = TestClient(app).post(
        "/agent/step",
        json={**request, "step_id": body["step_id"], "technical_retry": True},
    )
    assert retry.status_code == 200, retry.text
    assert len(StyleRepository(repository(project_id, domain_book_id)).list_performance()) == 1
    assert tuple(item.to_dict() for item in reopened.list_library_profiles()) == before

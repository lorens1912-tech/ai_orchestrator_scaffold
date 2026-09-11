from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.p20_core.domain_records import (
    CharacterState,
    EventRecord,
    FactRecord,
    KnowledgeEvent,
    PayoffRecord,
    SetupRecord,
    ThreadRecord,
)
from app.p20_core.memory_extraction import (
    MemoryExtractionDecision,
    MemoryExtractionError,
    MemoryExtractionPolicy,
    MemoryExtractionStatus,
    ModelInvocation,
    ModelInvocationRole,
    RelationshipChangeRecord,
    SceneMemorySource,
    SceneQualityStatus,
    StructuredMemoryExtractionCandidate,
    VerificationAxisStatus,
    commit_verified_memory_candidate,
    compute_source_hash,
    verify_memory_extraction_candidate,
)
from app.p20_core.project_repository import (
    ProjectDomainTransaction,
    ProjectRepository,
    SeriesRepository,
    StorageResolver,
    SystemRepository,
)


STAMP = "2026-09-11T00:00:00Z"
PROJECT_ID = "PROJ-gap008"
SCENE_ID = "SCENE-gap008-source"
ARTIFACT_REF = "books/gap008/chapters/chapter_001.json"
SOURCE_TEXT = "Ada opens the archive door and learns that Bo hid the brass marker."


def _reset_sqlite_file(db_path: Path) -> None:
    for path in (
        db_path,
        Path(str(db_path) + "-wal"),
        Path(str(db_path) + "-shm"),
    ):
        path.unlink(missing_ok=True)


def _table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        return {str(row[0]) for row in rows}
    finally:
        conn.close()


def _source(**overrides) -> SceneMemorySource:
    data = {
        "project_id": PROJECT_ID,
        "source_scene_id": SCENE_ID,
        "source_artifact_ref": ARTIFACT_REF,
        "source_text": SOURCE_TEXT,
    }
    data.update(overrides)
    if "source_text" in data:
        return SceneMemorySource.from_text(**data)
    return SceneMemorySource(**data)


def _extractor(**overrides) -> ModelInvocation:
    data = {
        "role": ModelInvocationRole.EXTRACTOR,
        "provider": "openai",
        "model": "gpt-gap008-extractor",
        "call_id": "extractor-call-001",
        "metadata": {"temperature": 0},
    }
    data.update(overrides)
    return ModelInvocation(**data)


def _verifier(**overrides) -> ModelInvocation:
    data = {
        "role": ModelInvocationRole.VERIFIER,
        "provider": "anthropic",
        "model": "claude-gap008-verifier",
        "call_id": "verifier-call-001",
        "metadata": {"temperature": 0},
    }
    data.update(overrides)
    return ModelInvocation(**data)


def _fact(**overrides) -> FactRecord:
    data = {
        "fact_id": "FACT-gap008-door",
        "project_id": PROJECT_ID,
        "subject_id": "CHAR-ada",
        "predicate": "opened",
        "object_type": "state",
        "object_id": None,
        "object_value": "archive door",
        "reality_status": "CANDIDATE",
        "verification_status": "EXTRACTED",
        "confidence": 0.94,
        "frozen": False,
        "author_locked": False,
        "valid_from": "SCENE-001",
        "valid_to": None,
        "established_event_id": "EVENT-gap008-door",
        "established_scene_id": SCENE_ID,
        "source_artifact_ref": ARTIFACT_REF,
        "source_refs": [f"{SCENE_ID}#p1"],
        "canon_version": 1,
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return FactRecord(**data)


def _character_state(**overrides) -> CharacterState:
    data = {
        "state_id": "CONTEXT-gap008-ada-state",
        "project_id": PROJECT_ID,
        "character_id": "CHAR-ada",
        "source_scene_id": SCENE_ID,
        "source_event_id": "EVENT-gap008-door",
        "source_artifact_ref": ARTIFACT_REF,
        "state_payload": {"knowledge": "Bo hid the brass marker"},
        "valid_from": "SCENE-001",
        "valid_to": None,
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return CharacterState(**data)


def _event(**overrides) -> EventRecord:
    data = {
        "event_id": "EVENT-gap008-door",
        "project_id": PROJECT_ID,
        "event_type": "DISCOVERY",
        "time_start": "day-1T08:00",
        "time_end": "day-1T08:05",
        "narrative_order": 1,
        "location_id": None,
        "participant_ids": ["CHAR-ada", "CHAR-bo"],
        "description": "Ada opens the archive door.",
        "cause_refs": [],
        "effect_refs": [],
        "source_scene_id": SCENE_ID,
        "source_artifact_ref": ARTIFACT_REF,
        "canon_status": "CANDIDATE",
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return EventRecord(**data)


def _knowledge(**overrides) -> KnowledgeEvent:
    data = {
        "knowledge_event_id": "KNOWLEDGE-gap008-ada-marker",
        "project_id": PROJECT_ID,
        "character_id": "CHAR-ada",
        "fact_id": "FACT-gap008-door",
        "event_id": None,
        "knowledge_status": "KNOWS",
        "learned_at_scene_id": SCENE_ID,
        "learned_at_event_id": "EVENT-gap008-door",
        "learned_from_character_id": "CHAR-bo",
        "source_ref": f"{SCENE_ID}#p2",
        "valid_from": "SCENE-001",
        "valid_to": None,
        "confidence": 0.91,
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return KnowledgeEvent(**data)


def _thread(**overrides) -> ThreadRecord:
    data = {
        "thread_id": "THREAD-gap008-archive",
        "project_id": PROJECT_ID,
        "name": "Archive marker",
        "description": "The brass marker opens a future thread.",
        "importance": 8,
        "opened_scene_id": SCENE_ID,
        "closed_scene_id": None,
        "status": "OPEN",
        "payoff_required": True,
        "target_payoff": "Use the marker to reach the lower archive.",
        "actual_payoff_ref": None,
        "deliberately_left_reason": None,
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return ThreadRecord(**data)


def _setup(**overrides) -> SetupRecord:
    data = {
        "setup_id": "SETUP-gap008-marker",
        "project_id": PROJECT_ID,
        "created_scene_id": SCENE_ID,
        "description": "Bo hides the brass marker.",
        "importance": 7,
        "expected_payoff": "The marker unlocks the lower archive.",
        "target_range": "ACT-002",
        "actual_payoff_scene_id": None,
        "status": "OPEN",
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return SetupRecord(**data)


def _payoff(**overrides) -> PayoffRecord:
    data = {
        "payoff_id": "PAYOFF-gap008-marker",
        "project_id": PROJECT_ID,
        "setup_id": "SETUP-gap008-marker",
        "completed_scene_id": SCENE_ID,
        "description": "The marker opens the lower archive.",
        "source_artifact_ref": ARTIFACT_REF,
        "status": "PAID_OFF",
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return PayoffRecord(**data)


def _relationship_change(**overrides) -> RelationshipChangeRecord:
    data = {
        "relationship_change_id": "REL-gap008-ada-bo-trust",
        "project_id": PROJECT_ID,
        "subject_character_id": "CHAR-ada",
        "object_character_id": "CHAR-bo",
        "relationship_type": "trust",
        "change_summary": "Ada trusts Bo enough to enter the archive.",
        "source_scene_id": SCENE_ID,
        "source_artifact_ref": ARTIFACT_REF,
        "confidence": 0.86,
        "version": 1,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    data.update(overrides)
    return RelationshipChangeRecord(**data)


def _candidate(**overrides) -> StructuredMemoryExtractionCandidate:
    source = overrides.pop("source", _source())
    project_id = overrides.pop("project_id", None)
    if project_id is not None:
        source = _source(project_id=project_id)
    data = {
        "candidate_id": "candidate-gap008-001",
        "source": source,
        "extraction_attempt": 1,
        "candidate_records": [
            _fact(),
            _character_state(),
            _event(),
            _knowledge(),
            _thread(),
            _setup(),
            _payoff(),
        ],
        "relationship_changes": [_relationship_change()],
        "extractor_call": _extractor(),
        "created_at": STAMP,
        "scene_quality_status": SceneQualityStatus.ACCEPT,
    }
    data.update(overrides)
    return StructuredMemoryExtractionCandidate.from_source(**data)


def _verify(
    candidate: StructuredMemoryExtractionCandidate,
    *,
    precision=VerificationAxisStatus.ACCEPT,
    completeness=VerificationAxisStatus.ACCEPT,
    source: SceneMemorySource | None = None,
    policy: MemoryExtractionPolicy | None = None,
):
    return verify_memory_extraction_candidate(
        candidate,
        source=source or _source(),
        verifier_call=_verifier(),
        precision_status=precision,
        completeness_status=completeness,
        precision_reasons=["precision checked"],
        completeness_reasons=["completeness checked"],
        must_fix=[],
        policy=policy or MemoryExtractionPolicy(max_attempts=2),
    )


def test_extraction_candidate_is_not_automatically_persisted_memory(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project(PROJECT_ID))
    _reset_sqlite_file(repo.db_path)

    candidate = _candidate()

    assert candidate.memory_extraction_status == MemoryExtractionStatus.CANDIDATE
    assert candidate.scene_quality_status == SceneQualityStatus.ACCEPT
    assert repo.list_structured_memory_records() == {}
    assert repo.db_path.is_relative_to(isolated_agentpro_storage)


def test_extractor_and_verifier_are_logically_separate_invocations() -> None:
    candidate = _candidate()

    assert candidate.extractor_call.role == ModelInvocationRole.EXTRACTOR

    with pytest.raises(MemoryExtractionError, match="VERIFIER role"):
        verify_memory_extraction_candidate(
            candidate,
            source=_source(),
            verifier_call=_extractor(call_id="different-call"),
            precision_status=VerificationAxisStatus.ACCEPT,
            completeness_status=VerificationAxisStatus.ACCEPT,
        )

    with pytest.raises(MemoryExtractionError, match="independent"):
        verify_memory_extraction_candidate(
            candidate,
            source=_source(),
            verifier_call=_verifier(call_id=candidate.extractor_call.call_id),
            precision_status=VerificationAxisStatus.ACCEPT,
            completeness_status=VerificationAxisStatus.ACCEPT,
        )


@pytest.mark.parametrize(
    ("precision", "expected_decision"),
    [
        (VerificationAxisStatus.ACCEPT, MemoryExtractionDecision.ACCEPT),
        (VerificationAxisStatus.REVISE, MemoryExtractionDecision.REVISE),
        (VerificationAxisStatus.REJECT, MemoryExtractionDecision.REJECT),
    ],
)
def test_verifier_precision_axis_drives_decision_independently(
    precision,
    expected_decision,
) -> None:
    result = _verify(
        _candidate(),
        precision=precision,
        completeness=VerificationAxisStatus.ACCEPT,
    )

    assert result.precision_status == precision
    assert result.completeness_status == VerificationAxisStatus.ACCEPT
    assert result.decision == expected_decision


@pytest.mark.parametrize(
    ("completeness", "expected_decision"),
    [
        (VerificationAxisStatus.ACCEPT, MemoryExtractionDecision.ACCEPT),
        (VerificationAxisStatus.REVISE, MemoryExtractionDecision.REVISE),
        (VerificationAxisStatus.REJECT, MemoryExtractionDecision.REJECT),
    ],
)
def test_verifier_completeness_axis_drives_decision_independently(
    completeness,
    expected_decision,
) -> None:
    result = _verify(
        _candidate(),
        precision=VerificationAxisStatus.ACCEPT,
        completeness=completeness,
    )

    assert result.precision_status == VerificationAxisStatus.ACCEPT
    assert result.completeness_status == completeness
    assert result.decision == expected_decision


def test_precision_and_completeness_results_are_stored_independently() -> None:
    result = verify_memory_extraction_candidate(
        _candidate(),
        source=_source(),
        verifier_call=_verifier(),
        precision_status=VerificationAxisStatus.ACCEPT,
        completeness_status=VerificationAxisStatus.REVISE,
        precision_reasons=["no hallucination"],
        completeness_reasons=["missing relationship delta"],
        must_fix=["add relationship delta"],
    )

    assert result.precision_status == VerificationAxisStatus.ACCEPT
    assert result.completeness_status == VerificationAxisStatus.REVISE
    assert result.precision_reasons == ("no hallucination",)
    assert result.completeness_reasons == ("missing relationship delta",)
    assert result.must_fix == ("add relationship delta",)


def test_scene_quality_status_does_not_control_memory_extraction_status() -> None:
    accepted_scene_candidate = _candidate(scene_quality_status=SceneQualityStatus.ACCEPT)
    revise_memory = _verify(
        accepted_scene_candidate,
        precision=VerificationAxisStatus.REVISE,
        completeness=VerificationAxisStatus.ACCEPT,
    )

    rejected_scene_candidate = _candidate(
        candidate_id="candidate-gap008-rejected-scene",
        scene_quality_status=SceneQualityStatus.REJECT,
    )
    accepted_memory = _verify(rejected_scene_candidate)

    assert revise_memory.scene_quality_status == SceneQualityStatus.ACCEPT
    assert revise_memory.memory_extraction_status == MemoryExtractionStatus.REVISE
    assert accepted_memory.scene_quality_status == SceneQualityStatus.REJECT
    assert accepted_memory.memory_extraction_status == MemoryExtractionStatus.ACCEPT


def test_extractor_cannot_self_accept_memory_candidate() -> None:
    with pytest.raises(MemoryExtractionError, match="extractor cannot assign final"):
        _candidate(memory_extraction_status=MemoryExtractionStatus.ACCEPT)


def test_verifier_accept_allows_atomic_project_memory_commit(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project(PROJECT_ID))
    _reset_sqlite_file(repo.db_path)
    candidate = _candidate()
    verification = _verify(candidate)

    result = commit_verified_memory_candidate(repo, candidate, verification)
    records = repo.list_structured_memory_records()

    assert result.memory_extraction_status == MemoryExtractionStatus.ACCEPT
    assert result.committed_record_ids == (
        "CONTEXT-gap008-ada-state",
        "EVENT-gap008-door",
        "FACT-gap008-door",
        "KNOWLEDGE-gap008-ada-marker",
        "PAYOFF-gap008-marker",
        "REL-gap008-ada-bo-trust",
        "SETUP-gap008-marker",
        "THREAD-gap008-archive",
    )
    assert set(records) == set(result.committed_record_ids)
    assert repo.db_path == isolated_agentpro_storage / "projects" / PROJECT_ID / "project.db"


@pytest.mark.parametrize(
    "status",
    [VerificationAxisStatus.REVISE, VerificationAxisStatus.REJECT],
)
def test_verifier_revise_or_reject_blocks_project_memory_commit(
    isolated_agentpro_storage,
    status,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project(f"{PROJECT_ID}-{status.value.lower()}"))
    _reset_sqlite_file(repo.db_path)
    candidate = _candidate(
        project_id=f"{PROJECT_ID}-{status.value.lower()}",
        candidate_records=[_fact(project_id=f"{PROJECT_ID}-{status.value.lower()}")],
        relationship_changes=[],
    )
    verification = _verify(candidate, precision=status)

    with pytest.raises(MemoryExtractionError, match="ACCEPT|committed"):
        commit_verified_memory_candidate(repo, candidate, verification)

    assert repo.list_structured_memory_records() == {}


def test_retry_loop_respects_limit_and_escalates_on_exhaustion() -> None:
    first_attempt = _verify(
        _candidate(extraction_attempt=1),
        precision=VerificationAxisStatus.REVISE,
        policy=MemoryExtractionPolicy(max_attempts=2),
    )
    final_attempt = _verify(
        _candidate(candidate_id="candidate-gap008-final", extraction_attempt=2),
        precision=VerificationAxisStatus.REVISE,
        policy=MemoryExtractionPolicy(max_attempts=2),
    )

    assert first_attempt.memory_extraction_status == MemoryExtractionStatus.REVISE
    assert first_attempt.escalation_required is False
    assert final_attempt.memory_extraction_status == MemoryExtractionStatus.ESCALATED
    assert final_attempt.escalation_required is True
    assert final_attempt.retry_count == 1


def test_provenance_or_source_scene_mismatch_is_rejected() -> None:
    source_mismatch = _source(source_scene_id="SCENE-gap008-other", source_text=SOURCE_TEXT)
    source_result = _verify(_candidate(), source=source_mismatch)

    record_mismatch = _candidate(
        candidate_id="candidate-gap008-bad-record",
        candidate_records=[_fact(source_artifact_ref="books/gap008/chapters/other.json")],
        relationship_changes=[],
    )
    record_result = _verify(record_mismatch)

    assert source_result.decision == MemoryExtractionDecision.REJECT
    assert source_result.memory_extraction_status == MemoryExtractionStatus.REJECT
    assert any("source_scene_id" in reason for reason in source_result.precision_reasons)
    assert record_result.decision == MemoryExtractionDecision.REJECT
    assert any("source artifact" in reason for reason in record_result.precision_reasons)


def test_candidate_set_is_committed_as_one_acceptance_unit(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project(PROJECT_ID))
    _reset_sqlite_file(repo.db_path)
    candidate = _candidate()

    commit_verified_memory_candidate(repo, candidate, _verify(candidate))

    assert repo.list_structured_memory_records("FACT") == {
        "FACT-gap008-door": _fact().to_json(),
    }
    assert repo.list_structured_memory_records("CHARACTER_STATE") == {
        "CONTEXT-gap008-ada-state": _character_state().to_json(),
    }
    assert "REL-gap008-ada-bo-trust" in repo.list_structured_memory_records("RELATIONSHIP_CHANGE")


def test_partial_project_db_write_rolls_back_whole_candidate_set(
    isolated_agentpro_storage,
    monkeypatch,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project(PROJECT_ID))
    _reset_sqlite_file(repo.db_path)
    candidate = _candidate()
    verification = _verify(candidate)
    original_add = ProjectDomainTransaction.add_structured_memory_record
    calls = {"count": 0}

    def fail_on_second_record(self, record, *, scope=None):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("forced partial-write failure")
        return original_add(self, record, scope=scope)

    monkeypatch.setattr(
        ProjectDomainTransaction,
        "add_structured_memory_record",
        fail_on_second_record,
    )

    with pytest.raises(RuntimeError, match="partial-write"):
        commit_verified_memory_candidate(repo, candidate, verification)

    assert calls["count"] == 2
    assert repo.list_structured_memory_records() == {}


def test_cross_project_candidate_is_rejected_before_commit(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project("PROJ-gap008-other"))
    _reset_sqlite_file(repo.db_path)
    candidate = _candidate()
    verification = _verify(candidate)

    with pytest.raises(MemoryExtractionError, match="repository scope"):
        commit_verified_memory_candidate(repo, candidate, verification)

    assert repo.list_structured_memory_records() == {}


def test_accepted_memory_stays_only_in_project_db_not_series_or_system(
    isolated_agentpro_storage,
) -> None:
    resolver = StorageResolver()
    project_repo = ProjectRepository(resolver.resolve_project(PROJECT_ID))
    series_repo = SeriesRepository(resolver.resolve_series("SERIES-gap008"))
    system_repo = SystemRepository(resolver.resolve_system())
    _reset_sqlite_file(project_repo.db_path)
    _reset_sqlite_file(series_repo.db_path)
    _reset_sqlite_file(system_repo.db_path)
    candidate = _candidate()

    commit_verified_memory_candidate(project_repo, candidate, _verify(candidate))
    series_repo.set_metadata("probe", "series")
    system_repo.set_metadata("probe", "system")

    assert "project_structured_memory_records" in _table_names(project_repo.db_path)
    assert "project_structured_memory_records" not in _table_names(series_repo.db_path)
    assert "project_structured_memory_records" not in _table_names(system_repo.db_path)
    assert project_repo.db_path.is_relative_to(isolated_agentpro_storage)
    assert series_repo.db_path.is_relative_to(isolated_agentpro_storage)
    assert system_repo.db_path.is_relative_to(isolated_agentpro_storage)


def test_accepting_structured_memory_does_not_promote_fact_to_frozen_canon(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project(PROJECT_ID))
    _reset_sqlite_file(repo.db_path)
    candidate = _candidate(candidate_records=[_fact(frozen=False, reality_status="CANDIDATE")])

    commit_verified_memory_candidate(repo, candidate, _verify(candidate))
    stored_fact_json = repo.list_structured_memory_records("FACT")["FACT-gap008-door"]

    assert '"frozen":false' in stored_fact_json
    assert '"reality_status":"CANDIDATE"' in stored_fact_json
    assert '"verification_status":"EXTRACTED"' in stored_fact_json


def test_audit_payload_is_reconstructable_and_deterministic() -> None:
    candidate = _candidate(candidate_records=[_fact()], relationship_changes=[])
    first = _verify(candidate)
    second = _verify(candidate)

    assert first.audit_payload() == second.audit_payload()
    assert first.audit_payload()["candidate_id"] == "candidate-gap008-001"
    assert first.audit_payload()["candidate_hash"] == candidate.candidate_hash
    assert first.audit_payload()["verifier_call"]["role"] == "VERIFIER"
    assert first.audit_payload()["precision_status"] == "ACCEPT"
    assert first.audit_payload()["completeness_status"] == "ACCEPT"


def test_source_hash_is_deterministic_for_same_scene_source() -> None:
    first = _source()
    second = _source()

    assert first.source_hash == second.source_hash
    assert first.source_hash == compute_source_hash(SOURCE_TEXT)
    assert _candidate().candidate_hash == _candidate().candidate_hash

from __future__ import annotations

from pathlib import Path

import pytest

from app.p20_core.domain_mutation_guard import (
    DomainMutationGuard,
    MutationContext,
    MutationOutcome,
    MutationReasonCode,
    MutationSource,
    MutationType,
    evaluate_domain_mutation,
)
from app.p20_core.domain_records import FactRecord
from app.p20_core.lock_service import acquire_run_lock, release_run_lock
from app.p20_core.memory_extraction import (
    MemoryExtractionError,
    ModelInvocation,
    ModelInvocationRole,
    SceneMemorySource,
    StructuredMemoryExtractionCandidate,
    VerificationAxisStatus,
    commit_verified_memory_candidate,
    verify_memory_extraction_candidate,
)
from app.p20_core.project_repository import ProjectRepository, ProjectStorageError, StorageResolver


STAMP = "2026-09-11T00:00:00Z"
PROJECT_ID = "PROJ-gap009"
SCENE_ID = "SCENE-gap009"
ARTIFACT_REF = "books/gap009/chapters/chapter_001.json"


def _reset_sqlite_file(db_path: Path) -> None:
    for path in (
        db_path,
        Path(str(db_path) + "-wal"),
        Path(str(db_path) + "-shm"),
    ):
        path.unlink(missing_ok=True)


def _fact(**overrides) -> FactRecord:
    data = {
        "fact_id": "FACT-gap009",
        "project_id": PROJECT_ID,
        "subject_id": "CHAR-ada",
        "predicate": "knows",
        "object_type": "state",
        "object_id": None,
        "object_value": "first value",
        "reality_status": "PROJECT_CANON",
        "verification_status": "CONFIRMED",
        "confidence": 1.0,
        "frozen": False,
        "author_locked": False,
        "valid_from": "SCENE-001",
        "valid_to": None,
        "established_event_id": "EVENT-gap009",
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


def _context(
    *,
    record_id: str = "FACT-gap009",
    mutation_type: MutationType = MutationType.UPDATE,
    source: MutationSource = MutationSource.AUTOMATION,
) -> MutationContext:
    return MutationContext(
        project_id=PROJECT_ID,
        record_type="FACT",
        record_id=record_id,
        mutation_type=mutation_type,
        source=source,
        actor_id="gap009-test",
    )


def _source(project_id: str = PROJECT_ID) -> SceneMemorySource:
    return SceneMemorySource.from_text(
        project_id=project_id,
        source_scene_id=SCENE_ID,
        source_artifact_ref=ARTIFACT_REF,
        source_text="Ada learns the first value.",
    )


def _extractor() -> ModelInvocation:
    return ModelInvocation(
        role=ModelInvocationRole.EXTRACTOR,
        provider="openai",
        model="gpt-gap009-extractor",
        call_id="gap009-extractor",
    )


def _verifier() -> ModelInvocation:
    return ModelInvocation(
        role=ModelInvocationRole.VERIFIER,
        provider="anthropic",
        model="claude-gap009-verifier",
        call_id="gap009-verifier",
    )


def _candidate(*records: FactRecord) -> StructuredMemoryExtractionCandidate:
    return StructuredMemoryExtractionCandidate.from_source(
        candidate_id="candidate-gap009",
        source=_source(),
        extraction_attempt=1,
        candidate_records=records,
        relationship_changes=[],
        extractor_call=_extractor(),
        created_at=STAMP,
    )


def _accepted(candidate: StructuredMemoryExtractionCandidate):
    return verify_memory_extraction_candidate(
        candidate,
        source=_source(),
        verifier_call=_verifier(),
        precision_status=VerificationAxisStatus.ACCEPT,
        completeness_status=VerificationAxisStatus.ACCEPT,
    )


def _commit(repo: ProjectRepository, *records: FactRecord) -> None:
    with repo.domain_transaction() as tx:
        for record in records:
            tx.add_structured_memory_record(record)


def test_unlocked_unfrozen_record_mutation_is_allowed() -> None:
    current = _fact(version=1)
    proposed = _fact(version=2, object_value="second value")

    decision = evaluate_domain_mutation(
        current_payload=current.to_json(),
        proposed_payload=proposed.to_json(),
        context=_context(),
    )

    assert decision.outcome == MutationOutcome.ALLOW
    assert decision.reason_code == MutationReasonCode.UPDATE_ALLOWED
    assert decision.current_frozen is False
    assert decision.current_author_locked is False


@pytest.mark.parametrize(
    ("current", "expected_reason"),
    [
        (_fact(frozen=True, author_locked=False), MutationReasonCode.FROZEN_RECORD),
        (_fact(frozen=False, author_locked=True), MutationReasonCode.AUTHOR_LOCKED_RECORD),
        (_fact(frozen=True, author_locked=True), MutationReasonCode.FROZEN_AND_AUTHOR_LOCKED_RECORD),
    ],
)
def test_frozen_and_author_locked_records_deny_ordinary_mutation_with_distinct_reasons(
    current,
    expected_reason,
) -> None:
    proposed = current.with_updates(version=2, object_value="changed value")

    decision = evaluate_domain_mutation(
        current_payload=current.to_json(),
        proposed_payload=proposed.to_json(),
        context=_context(source=MutationSource.MODEL),
    )

    assert decision.outcome == MutationOutcome.DENY
    assert decision.reason_code == expected_reason


def test_technical_run_lock_is_not_treated_as_domain_mutation_lock(
    isolated_agentpro_storage,
) -> None:
    acquire_run_lock("gap009_run_lock", "gap009_book")
    try:
        decision = evaluate_domain_mutation(
            current_payload=_fact(version=1).to_json(),
            proposed_payload=_fact(version=2, object_value="changed despite technical lock").to_json(),
            context=_context(source=MutationSource.AUTOMATION),
        )
    finally:
        release_run_lock("gap009_run_lock")

    assert decision.outcome == MutationOutcome.ALLOW
    assert decision.reason_code == MutationReasonCode.UPDATE_ALLOWED
    assert (isolated_agentpro_storage / "runs" / "gap009_run_lock" / "run.lock.json").exists() is False


@pytest.mark.parametrize(
    ("current", "proposed", "expected_reason"),
    [
        (
            _fact(frozen=True, author_locked=False, version=1),
            _fact(frozen=False, author_locked=False, version=2),
            MutationReasonCode.SILENT_UNFREEZE_DENIED,
        ),
        (
            _fact(frozen=False, author_locked=True, version=1),
            _fact(frozen=False, author_locked=False, version=2),
            MutationReasonCode.SILENT_UNLOCK_DENIED,
        ),
    ],
)
def test_silent_unfreeze_or_unlock_is_denied(current, proposed, expected_reason) -> None:
    decision = evaluate_domain_mutation(
        current_payload=current.to_json(),
        proposed_payload=proposed.to_json(),
        context=_context(source=MutationSource.AUTHOR),
    )

    assert decision.outcome == MutationOutcome.DENY
    assert decision.reason_code == expected_reason


def test_allowed_update_keeps_domain_id_and_requires_version_increment() -> None:
    current = _fact(fact_id="FACT-gap009-stable", version=1)
    proposed = current.with_updates(version=2, object_value="renamed value")

    allowed = evaluate_domain_mutation(
        current_payload=current.to_json(),
        proposed_payload=proposed.to_json(),
        context=_context(record_id="FACT-gap009-stable"),
    )
    denied = evaluate_domain_mutation(
        current_payload=current.to_json(),
        proposed_payload=current.with_updates(object_value="changed without version").to_json(),
        context=_context(record_id="FACT-gap009-stable"),
    )

    assert proposed.fact_id == current.fact_id
    assert allowed.outcome == MutationOutcome.ALLOW
    assert allowed.proposed_version == 2
    assert denied.outcome == MutationOutcome.DENY
    assert denied.reason_code == MutationReasonCode.VERSION_NOT_INCREMENTED


def test_ordinary_update_cannot_change_stable_domain_id() -> None:
    current = _fact(fact_id="FACT-gap009-stable", version=1)
    proposed = current.with_updates(fact_id="FACT-gap009-renamed", version=2)

    decision = evaluate_domain_mutation(
        current_payload=current.to_json(),
        proposed_payload=proposed.to_json(),
        context=_context(record_id="FACT-gap009-stable"),
    )

    assert decision.outcome == MutationOutcome.DENY
    assert decision.reason_code == MutationReasonCode.DOMAIN_ID_CHANGED


def test_cross_project_mutation_is_rejected_by_project_repository(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project(PROJECT_ID))
    _reset_sqlite_file(repo.db_path)

    with pytest.raises(ProjectStorageError, match="record scope does not match"):
        _commit(repo, _fact(project_id="PROJ-gap009-other"))

    assert repo.list_structured_memory_records() == {}


@pytest.mark.parametrize(
    "locked_record",
    [
        _fact(fact_id="FACT-gap009-frozen", frozen=True, author_locked=False, version=1),
        _fact(fact_id="FACT-gap009-author", frozen=False, author_locked=True, version=1),
    ],
)
def test_structured_memory_automation_cannot_overwrite_protected_fact_record(
    isolated_agentpro_storage,
    locked_record,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project(PROJECT_ID))
    _reset_sqlite_file(repo.db_path)
    _commit(repo, locked_record)
    proposed = locked_record.with_updates(version=2, object_value="model overwrite")

    with pytest.raises(ProjectStorageError, match="domain mutation denied"):
        _commit(repo, proposed)

    records = repo.list_structured_memory_records("FACT")
    assert records[str(locked_record.fact_id)] == locked_record.to_json()


def test_verifier_accept_and_candidate_commit_do_not_bypass_mutation_guard(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project(PROJECT_ID))
    _reset_sqlite_file(repo.db_path)
    locked_record = _fact(fact_id="FACT-gap009-accept-bypass", frozen=True, version=1)
    _commit(repo, locked_record)
    proposed = locked_record.with_updates(version=2, object_value="accepted overwrite")
    candidate = _candidate(proposed)

    with pytest.raises(ProjectStorageError, match="FROZEN_RECORD"):
        commit_verified_memory_candidate(repo, candidate, _accepted(candidate))

    assert repo.list_structured_memory_records("FACT")[str(locked_record.fact_id)] == locked_record.to_json()


def test_batch_mutation_rolls_back_when_one_record_is_denied(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project(PROJECT_ID))
    _reset_sqlite_file(repo.db_path)
    locked = _fact(fact_id="FACT-gap009-z-locked", frozen=True, version=1)
    _commit(repo, locked)
    new_record = _fact(fact_id="FACT-gap009-a-new", object_value="new fact", version=1)
    locked_update = locked.with_updates(version=2, object_value="blocked overwrite")
    candidate = _candidate(new_record, locked_update)

    with pytest.raises(ProjectStorageError, match="FROZEN_RECORD"):
        commit_verified_memory_candidate(repo, candidate, _accepted(candidate))

    records = repo.list_structured_memory_records("FACT")
    assert "FACT-gap009-a-new" not in records
    assert records["FACT-gap009-z-locked"] == locked.to_json()


@pytest.mark.parametrize(
    ("frozen", "author_locked"),
    [
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    ],
)
def test_frozen_and_author_locked_states_remain_independent(frozen, author_locked) -> None:
    record = _fact(frozen=frozen, author_locked=author_locked)

    assert record.frozen is frozen
    assert record.author_locked is author_locked


def test_mutation_decision_is_deterministic_and_auditable() -> None:
    current = _fact(frozen=True, version=1)
    proposed = current.with_updates(version=2, object_value="changed")
    kwargs = {
        "current_payload": current.to_json(),
        "proposed_payload": proposed.to_json(),
        "context": _context(source=MutationSource.MODEL),
    }

    first = DomainMutationGuard().evaluate(**kwargs)
    second = DomainMutationGuard().evaluate(**kwargs)

    assert first == second
    assert first.to_json() == second.to_json()
    assert first.to_dict() == {
        "actor_id": "gap009-test",
        "current_author_locked": False,
        "current_frozen": True,
        "current_version": 1,
        "guard_version": 1,
        "mutation_type": "UPDATE",
        "outcome": "DENY",
        "project_id": PROJECT_ID,
        "proposed_author_locked": False,
        "proposed_frozen": True,
        "proposed_version": 2,
        "reason_code": "FROZEN_RECORD",
        "record_id": "FACT-gap009",
        "record_type": "FACT",
        "source": "MODEL",
    }

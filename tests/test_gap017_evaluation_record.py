from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

import pytest

from app.p20_core.evaluation import (
    LOCAL_PROMPT_VERSION,
    EvaluationAttemptFenced,
    EvaluationBinding,
    EvaluationConflict,
    EvaluationInProgress,
    EvaluationIntegrityError,
    EvaluationNeedsIntervention,
    EvaluationRecoveryRequired,
    binding_hash,
    canonical_hash,
    evaluation_cache_key,
    finalize_evaluation,
    load_completed_evaluation,
    prepare_evaluation,
    recover_evaluation,
    start_evaluation,
)
from app.p20_core.project_repository import ProjectRepository, StorageResolver


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def repository(root, suffix: str = "A") -> ProjectRepository:
    return ProjectRepository(
        StorageResolver(root).resolve_project(
            f"PROJ-GAP017-{suffix}", book_id=f"BOOK-GAP017-{suffix}"
        )
    )


def binding(repo: ProjectRepository, suffix: str = "A") -> EvaluationBinding:
    return EvaluationBinding.local_deterministic(
        project_id=repo.scope.scope_id,
        book_id=repo.context.book_id,
        series_id="SERIES-GAP017-TEST",
        run_id=f"RUN-GAP017-{suffix}",
        step_id=f"STEP-GAP017-{suffix}",
        operation_id=f"evaluation:{repo.scope.scope_id}:RUN-GAP017-{suffix}:STEP-GAP017-{suffix}",
        artifact_id=f"ARTIFACT-GAP017-{suffix}",
        artifact_version="1",
        artifact_hash=sha(f"neutral artifact {suffix}"),
        criteria_version="QUALITY-CRITERIA-V1",
        criteria={"accept_min": 0.7, "revise_min": 0.55},
        context_package_id=f"CONTEXT-GAP017-{suffix}",
        context_hash=sha(f"neutral context {suffix}"),
        evaluator_id="app.quality_contract._fq_tool_quality",
        evaluator_version="FINAL_QUALITY_CANON_20260325_V4",
        configuration={"accept_min": 0.7, "revise_min": 0.55},
        additional_input_hashes={"style_evaluation": sha("neutral style")},
        input_evaluation_refs=("STYLE-EVAL-TEST",),
    )


def complete(repo: ProjectRepository, item: EvaluationBinding, decision: str = "ACCEPT"):
    started = start_evaluation(repo, item)
    assert started.attempt_token
    return finalize_evaluation(
        repo,
        item,
        attempt_token=started.attempt_token,
        decision=decision,
        reasons=("neutral reason",),
        must_fix=({"id": "FIX-TEST", "detail": "neutral fix"},),
    )


REQUIRED_RECORD_FIELDS = {
    "evaluation_id", "project_id", "book_id", "series_id", "run_id",
    "step_id", "operation_id", "evaluation_kind", "artifact_id",
    "artifact_hash", "criteria_version", "criteria_hash", "prompt_version",
    "context_package_id", "context_hash", "evaluator_kind", "evaluator_id",
    "evaluator_version", "requested_model", "effective_model", "provider",
    "model_version", "configuration_hash", "input_fingerprint",
    "invocation_refs", "input_evaluation_refs", "execution_status",
    "validation_status", "decision", "reasons", "must_fix", "created_at",
    "completed_at", "reevaluation_of", "schema_version", "record_hash",
}


@pytest.mark.parametrize("decision", ["ACCEPT", "REVISE", "REJECT"])
def test_complete_record_has_exact_contract_fields_and_valid_decision(
    isolated_agentpro_storage, decision
):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    record = complete(repo, item, decision)

    assert set(record.to_dict()) == REQUIRED_RECORD_FIELDS
    assert record.execution_status == "COMPLETED"
    assert record.validation_status == "VALID"
    assert record.decision == decision


def test_local_deterministic_has_no_fictitious_model_identity(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    record = complete(repo, item)

    assert record.evaluator_kind == "LOCAL_DETERMINISTIC"
    assert record.prompt_version == LOCAL_PROMPT_VERSION
    assert record.requested_model is None
    assert record.effective_model is None
    assert record.provider is None
    assert record.model_version is None
    assert record.invocation_refs == ()


def test_persistence_reopen_and_same_operation_reuse(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    record = complete(repo, item, "REJECT")

    reopened = repository(isolated_agentpro_storage)
    loaded = load_completed_evaluation(reopened, item)
    retry = start_evaluation(reopened, item)

    assert loaded == record
    assert retry.reused is True
    assert retry.attempt_token is None
    assert retry.record == record
    assert len(reopened.get_evaluation_envelope(item.operation_id)["attempts"]) == 1


def test_historical_absence_is_not_recorded_and_not_a_cache_hit(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)

    assert repo.get_evaluation_envelope(item.operation_id) is None
    assert load_completed_evaluation(repo, item) is None


def test_same_operation_retry_does_not_call_local_test_evaluator_again(
    isolated_agentpro_storage,
):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    calls = 0

    first = start_evaluation(repo, item)
    if not first.reused:
        calls += 1
        finalize_evaluation(
            repo, item, attempt_token=first.attempt_token, decision="ACCEPT"
        )
    retry = start_evaluation(repo, item)
    if not retry.reused:
        calls += 1

    assert calls == 1
    assert retry.reused is True


def test_record_hash_integrity_fails_closed(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    complete(repo, item)
    with repo.evaluation_transaction(item.operation_id) as state:
        state["record"]["reasons"] = ["tampered"]

    with pytest.raises(EvaluationIntegrityError, match="record hash mismatch"):
        load_completed_evaluation(repo, item)


def test_envelope_record_hash_and_decision_must_match_record(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    record = complete(repo, item)
    state = repo.get_evaluation_envelope(item.operation_id)
    assert state["record_hash"] == record.record_hash

    with repo.evaluation_transaction(item.operation_id) as mutable:
        mutable["decision"] = "REJECT"
    with pytest.raises(EvaluationIntegrityError, match="decision mismatch"):
        load_completed_evaluation(repo, item)


def test_cache_key_is_deterministic_and_canonical(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)

    assert evaluation_cache_key(item) == evaluation_cache_key(
        EvaluationBinding.from_dict(json.loads(json.dumps(item.to_dict())))
    )
    assert len(evaluation_cache_key(item)) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", "PROJ-GAP017-OTHER"),
        ("book_id", "BOOK-GAP017-OTHER"),
        ("series_id", "SERIES-GAP017-OTHER"),
        ("run_id", "RUN-GAP017-OTHER"),
        ("step_id", "STEP-GAP017-OTHER"),
        ("operation_id", "evaluation:other"),
        ("artifact_id", "ARTIFACT-GAP017-OTHER"),
        ("artifact_version", "2"),
        ("artifact_hash", sha("other artifact")),
        ("criteria_version", "QUALITY-CRITERIA-V2"),
        ("criteria_hash", sha("other criteria")),
        ("prompt_hash", sha("other prompt")),
        ("context_package_id", "CONTEXT-GAP017-OTHER"),
        ("context_hash", sha("other context")),
        ("evaluator_id", "app.quality_rules.evaluate_quality"),
        ("evaluator_version", "QUALITY-RULES-V2"),
        ("configuration_hash", sha("other configuration")),
        ("input_fingerprint", sha("other input")),
        ("invocation_refs", ("INV-OTHER",)),
        ("input_evaluation_refs", ("STYLE-EVAL-OTHER",)),
        ("reevaluation_of", "EVAL-PRIOR"),
    ],
)
def test_each_cache_preimage_component_changes_key(
    isolated_agentpro_storage, field, value
):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    changed = replace(item, **{field: value})

    assert evaluation_cache_key(changed) != evaluation_cache_key(item)


def test_actual_model_identity_changes_cache_key(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    local = binding(repo)
    base = replace(
        local,
        evaluator_kind="MODEL",
        prompt_version="QUALITY-PROMPT-V1",
        requested_model="gpt-test-requested",
        effective_model="gpt-test-effective",
        provider="test-provider",
        model_version=None,
    )
    provider_version = replace(base, model_version="snapshot-test")
    prompt_version = replace(
        base,
        prompt_version="QUALITY-PROMPT-V2",
        prompt_hash=sha("quality prompt v2"),
    )

    assert evaluation_cache_key(base) != evaluation_cache_key(provider_version)
    assert evaluation_cache_key(base) != evaluation_cache_key(prompt_version)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", "PROJ-GAP017-WRONG"),
        ("book_id", "BOOK-GAP017-WRONG"),
        ("run_id", "RUN-GAP017-WRONG"),
        ("step_id", "STEP-GAP017-WRONG"),
    ],
)
def test_wrong_scope_or_execution_binding_fails_closed(
    isolated_agentpro_storage, field, value
):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    complete(repo, item)

    with pytest.raises(EvaluationConflict):
        start_evaluation(repo, replace(item, **{field: value}))


def test_unsupported_envelope_schema_fails_closed(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    complete(repo, item)
    with repo.evaluation_transaction(item.operation_id) as state:
        state["schema_version"] = 999

    with pytest.raises(EvaluationIntegrityError, match="unsupported"):
        load_completed_evaluation(repo, item)


def test_corrupt_cache_key_fails_closed(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    complete(repo, item)
    with repo.evaluation_transaction(item.operation_id) as state:
        state["cache_key"] = sha("corrupt")

    with pytest.raises(EvaluationIntegrityError, match="cache key mismatch"):
        load_completed_evaluation(repo, item)


def test_same_operation_changed_binding_is_conflict_not_cache_miss(
    isolated_agentpro_storage,
):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    complete(repo, item)

    with pytest.raises(EvaluationConflict):
        start_evaluation(repo, replace(item, artifact_hash=sha("changed text")))


def test_concurrent_duplicate_has_one_owner_and_other_is_in_progress(
    isolated_agentpro_storage,
):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)

    def run():
        try:
            return start_evaluation(repo, item)
        except EvaluationInProgress as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))

    assert sum(hasattr(result, "attempt_token") for result in results) == 1
    assert sum(isinstance(result, EvaluationInProgress) for result in results) == 1
    assert len(repo.get_evaluation_envelope(item.operation_id)["attempts"]) == 1


def test_old_running_attempt_is_not_taken_over_without_operator_recovery(
    isolated_agentpro_storage,
):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    started = start_evaluation(repo, item)
    with repo.evaluation_transaction(item.operation_id) as state:
        state["attempts"][-1]["started_at"] = "2000-01-01T00:00:00+00:00"

    with pytest.raises(EvaluationInProgress):
        start_evaluation(repo, item)
    with pytest.raises(EvaluationConflict):
        recover_evaluation(
            repo,
            replace(item, run_id="RUN-GAP017-FOREIGN"),
            recovered_by="OPERATOR-TEST",
        )
    state = repo.get_evaluation_envelope(item.operation_id)
    assert state["attempts"][-1]["attempt_token"] == started.attempt_token
    assert state["attempts"][-1]["status"] == "RUNNING"


def test_prepared_requires_operator_recovery_and_preserves_evaluation_id(
    isolated_agentpro_storage,
):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    evaluation_id = prepare_evaluation(repo, item)

    with pytest.raises(EvaluationRecoveryRequired):
        start_evaluation(repo, item)
    recovered = recover_evaluation(repo, item, recovered_by="OPERATOR-TEST")

    assert recovered.evaluation_id == evaluation_id
    assert recovered.attempt_token
    assert len(repo.get_evaluation_envelope(item.operation_id)["attempts"]) == 1


def test_running_recovery_fences_old_token_and_new_attempt_can_finalize(
    isolated_agentpro_storage,
):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    first = start_evaluation(repo, item)
    recovered = recover_evaluation(repo, item, recovered_by="OPERATOR-TEST")

    assert recovered.evaluation_id == first.evaluation_id
    assert recovered.attempt_token != first.attempt_token
    with pytest.raises(EvaluationAttemptFenced):
        finalize_evaluation(
            repo, item, attempt_token=first.attempt_token, decision="ACCEPT"
        )
    record = finalize_evaluation(
        repo, item, attempt_token=recovered.attempt_token, decision="REVISE"
    )
    assert record.evaluation_id == first.evaluation_id
    assert record.decision == "REVISE"


def test_unprovable_binding_becomes_needs_intervention(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    prepare_evaluation(repo, item)
    with repo.evaluation_transaction(item.operation_id) as state:
        del state["binding"]["criteria_hash"]

    with pytest.raises(EvaluationNeedsIntervention, match="UNPROVABLE"):
        recover_evaluation(repo, item, recovered_by="OPERATOR-TEST")

    state = repo.get_evaluation_envelope(item.operation_id)
    assert state["recovery_status"] == "NEEDS_INTERVENTION"
    assert state["execution_status"] == "INTERRUPTED"
    assert state["decision"] is None
    assert "record" not in state


def test_real_sqlite_failure_rolls_back_final_record(isolated_agentpro_storage, monkeypatch):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    started = start_evaluation(repo, item)
    original_transaction = repo.evaluation_transaction

    @contextmanager
    def fail_final_record_write(operation_id, *, connection=None):
        with original_transaction(operation_id, connection=connection) as state:
            yield state
            if "record" in state:
                raise sqlite3.IntegrityError("injected GAP017 finalization failure")

    with monkeypatch.context() as patch:
        patch.setattr(repo, "evaluation_transaction", fail_final_record_write)
        with pytest.raises(sqlite3.IntegrityError, match="injected GAP017"):
            finalize_evaluation(
                repo, item, attempt_token=started.attempt_token, decision="ACCEPT"
            )

    state = repo.get_evaluation_envelope(item.operation_id)
    assert state["execution_status"] == "RUNNING"
    assert state["attempts"][-1]["status"] == "RUNNING"
    assert "record" not in state


def test_two_projects_are_isolated(isolated_agentpro_storage):
    repo_a = repository(isolated_agentpro_storage, "A")
    repo_b = repository(isolated_agentpro_storage, "B")
    item_a = binding(repo_a, "A")
    item_b = binding(repo_b, "B")
    record_a = complete(repo_a, item_a, "ACCEPT")
    record_b = complete(repo_b, item_b, "REJECT")

    assert [x["record"]["evaluation_id"] for x in repo_a.list_evaluation_envelopes()] == [
        record_a.evaluation_id
    ]
    assert [x["record"]["evaluation_id"] for x in repo_b.list_evaluation_envelopes()] == [
        record_b.evaluation_id
    ]
    assert repo_a.get_evaluation_envelope(item_b.operation_id) is None
    assert repo_b.get_evaluation_envelope(item_a.operation_id) is None


def test_binding_hash_is_stable_and_separate_from_cache_key(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)

    assert binding_hash(item) == binding_hash(item)
    assert binding_hash(item) != evaluation_cache_key(item)
    assert evaluation_cache_key(item) == canonical_hash(
        "GAP017_EVALUATION_CACHE_V1", item.to_dict()
    )

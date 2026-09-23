"""GAP-017 project-owned EvaluationRecord persistence and fencing.

The active P20 QUALITY path uses this module as the sole durable owner for
bindings, exact cached outputs, same-operation reuse, and recovery attempts.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence
from uuid import uuid4


SCHEMA_VERSION = 1
CACHE_DOMAIN = "GAP017_EVALUATION_CACHE_V1"
LOCAL_PROMPT_VERSION = "NOT_APPLICABLE:LOCAL_V1"
_HASH_CHARS = frozenset("0123456789abcdef")


class EvaluationError(RuntimeError):
    pass


class EvaluationConflict(EvaluationError):
    pass


class EvaluationInProgress(EvaluationError):
    pass


class EvaluationRecoveryRequired(EvaluationError):
    pass


class EvaluationIntegrityError(EvaluationError):
    pass


class EvaluationNeedsIntervention(EvaluationError):
    pass


class EvaluationAttemptFenced(EvaluationError):
    pass


class ExecutionStatus(str, Enum):
    PREPARED = "PREPARED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class ValidationStatus(str, Enum):
    NOT_PERFORMED = "NOT_PERFORMED"
    VALID = "VALID"
    INVALID = "INVALID"


class QualityDecision(str, Enum):
    ACCEPT = "ACCEPT"
    REVISE = "REVISE"
    REJECT = "REJECT"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name)


def _require_hash(value: Any, name: str) -> str:
    value = _required_text(value, name).lower()
    if len(value) != 64 or any(char not in _HASH_CHARS for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def canonical_hash(domain: str, value: Any) -> str:
    payload = {"domain": _required_text(domain, "domain"), "payload": value}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def compute_criteria_hash(criteria: Mapping[str, Any]) -> str:
    return canonical_hash("GAP017_CRITERIA_V1", dict(criteria))


def compute_configuration_hash(configuration: Mapping[str, Any]) -> str:
    return canonical_hash("GAP017_CONFIGURATION_V1", dict(configuration))


def compute_input_fingerprint(
    *,
    artifact_hash: str,
    context_hash: str,
    additional_input_hashes: Mapping[str, str] | None = None,
) -> str:
    additional = dict(additional_input_hashes or {})
    for name, value in additional.items():
        _required_text(name, "additional input name")
        _require_hash(value, f"additional_input_hashes[{name!r}]")
    return canonical_hash(
        "GAP017_EVALUATION_INPUT_V1",
        {
            "artifact_hash": _require_hash(artifact_hash, "artifact_hash"),
            "context_hash": _require_hash(context_hash, "context_hash"),
            "additional_input_hashes": additional,
        },
    )


@dataclass(frozen=True)
class EvaluationBinding:
    project_id: str
    book_id: str
    series_id: str | None
    run_id: str
    step_id: str
    operation_id: str
    evaluation_kind: str
    artifact_id: str
    artifact_version: str
    artifact_hash: str
    criteria_version: str
    criteria_hash: str
    prompt_version: str
    prompt_hash: str
    context_package_id: str
    context_hash: str
    evaluator_kind: str
    evaluator_id: str
    evaluator_version: str
    requested_model: str | None
    effective_model: str | None
    provider: str | None
    model_version: str | None
    configuration_hash: str
    input_fingerprint: str
    invocation_refs: tuple[str, ...] = ()
    input_evaluation_refs: tuple[str, ...] = ()
    reevaluation_of: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "project_id", "book_id", "run_id", "step_id", "operation_id",
            "evaluation_kind", "artifact_id", "artifact_version",
            "criteria_version", "prompt_version", "context_package_id",
            "evaluator_kind", "evaluator_id", "evaluator_version",
        ):
            _required_text(getattr(self, name), name)
        _optional_text(self.series_id, "series_id")
        _optional_text(self.reevaluation_of, "reevaluation_of")
        for name in (
            "artifact_hash", "criteria_hash", "prompt_hash", "context_hash",
            "configuration_hash", "input_fingerprint",
        ):
            _require_hash(getattr(self, name), name)
        if self.evaluation_kind != "QUALITY":
            raise ValueError("phase 2 supports evaluation_kind=QUALITY only")
        if self.evaluator_kind == "LOCAL_DETERMINISTIC":
            if any(
                value is not None
                for value in (
                    self.requested_model,
                    self.effective_model,
                    self.provider,
                    self.model_version,
                )
            ):
                raise ValueError("LOCAL_DETERMINISTIC model identity must be null")
            if self.prompt_version != LOCAL_PROMPT_VERSION:
                raise ValueError("LOCAL_DETERMINISTIC prompt_version mismatch")
        for name in ("invocation_refs", "input_evaluation_refs"):
            values = getattr(self, name)
            if not isinstance(values, tuple):
                raise ValueError(f"{name} must be a tuple")
            for value in values:
                _required_text(value, name)

    @classmethod
    def local_deterministic(
        cls,
        *,
        project_id: str,
        book_id: str,
        series_id: str | None,
        run_id: str,
        step_id: str,
        operation_id: str,
        artifact_id: str,
        artifact_version: str,
        artifact_hash: str,
        criteria_version: str,
        criteria: Mapping[str, Any],
        context_package_id: str,
        context_hash: str,
        evaluator_id: str,
        evaluator_version: str,
        configuration: Mapping[str, Any],
        additional_input_hashes: Mapping[str, str] | None = None,
        invocation_refs: Sequence[str] = (),
        input_evaluation_refs: Sequence[str] = (),
        reevaluation_of: str | None = None,
    ) -> "EvaluationBinding":
        return cls(
            project_id=project_id,
            book_id=book_id,
            series_id=series_id,
            run_id=run_id,
            step_id=step_id,
            operation_id=operation_id,
            evaluation_kind="QUALITY",
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            artifact_hash=artifact_hash,
            criteria_version=criteria_version,
            criteria_hash=compute_criteria_hash(criteria),
            prompt_version=LOCAL_PROMPT_VERSION,
            prompt_hash=canonical_hash("GAP017_LOCAL_PROMPT_V1", LOCAL_PROMPT_VERSION),
            context_package_id=context_package_id,
            context_hash=context_hash,
            evaluator_kind="LOCAL_DETERMINISTIC",
            evaluator_id=evaluator_id,
            evaluator_version=evaluator_version,
            requested_model=None,
            effective_model=None,
            provider=None,
            model_version=None,
            configuration_hash=compute_configuration_hash(configuration),
            input_fingerprint=compute_input_fingerprint(
                artifact_hash=artifact_hash,
                context_hash=context_hash,
                additional_input_hashes=additional_input_hashes,
            ),
            invocation_refs=tuple(invocation_refs),
            input_evaluation_refs=tuple(input_evaluation_refs),
            reevaluation_of=reevaluation_of,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "book_id": self.book_id,
            "series_id": self.series_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "operation_id": self.operation_id,
            "evaluation_kind": self.evaluation_kind,
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "artifact_hash": self.artifact_hash,
            "criteria_version": self.criteria_version,
            "criteria_hash": self.criteria_hash,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "context_package_id": self.context_package_id,
            "context_hash": self.context_hash,
            "evaluator_kind": self.evaluator_kind,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "requested_model": self.requested_model,
            "effective_model": self.effective_model,
            "provider": self.provider,
            "model_version": self.model_version,
            "configuration_hash": self.configuration_hash,
            "input_fingerprint": self.input_fingerprint,
            "invocation_refs": list(self.invocation_refs),
            "input_evaluation_refs": list(self.input_evaluation_refs),
            "reevaluation_of": self.reevaluation_of,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvaluationBinding":
        data = dict(value)
        data["invocation_refs"] = tuple(data.get("invocation_refs") or ())
        data["input_evaluation_refs"] = tuple(data.get("input_evaluation_refs") or ())
        try:
            return cls(**data)
        except (TypeError, ValueError) as exc:
            raise EvaluationIntegrityError("evaluation binding is incomplete or invalid") from exc


def evaluation_cache_key(binding: EvaluationBinding) -> str:
    return canonical_hash(CACHE_DOMAIN, binding.to_dict())


def binding_hash(binding: EvaluationBinding) -> str:
    return canonical_hash("GAP017_EVALUATION_BINDING_V1", binding.to_dict())


@dataclass(frozen=True)
class EvaluationRecord:
    evaluation_id: str
    project_id: str
    book_id: str
    series_id: str | None
    run_id: str
    step_id: str
    operation_id: str
    evaluation_kind: str
    artifact_id: str
    artifact_hash: str
    criteria_version: str
    criteria_hash: str
    prompt_version: str
    context_package_id: str
    context_hash: str
    evaluator_kind: str
    evaluator_id: str
    evaluator_version: str
    requested_model: str | None
    effective_model: str | None
    provider: str | None
    model_version: str | None
    configuration_hash: str
    input_fingerprint: str
    invocation_refs: tuple[str, ...]
    input_evaluation_refs: tuple[str, ...]
    execution_status: str
    validation_status: str
    decision: str | None
    reasons: tuple[str, ...]
    must_fix: tuple[dict[str, Any], ...]
    created_at: str
    completed_at: str | None
    reevaluation_of: str | None
    schema_version: int
    record_hash: str

    def unsigned_dict(self) -> dict[str, Any]:
        result = self.to_dict()
        result.pop("record_hash")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "project_id": self.project_id,
            "book_id": self.book_id,
            "series_id": self.series_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "operation_id": self.operation_id,
            "evaluation_kind": self.evaluation_kind,
            "artifact_id": self.artifact_id,
            "artifact_hash": self.artifact_hash,
            "criteria_version": self.criteria_version,
            "criteria_hash": self.criteria_hash,
            "prompt_version": self.prompt_version,
            "context_package_id": self.context_package_id,
            "context_hash": self.context_hash,
            "evaluator_kind": self.evaluator_kind,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "requested_model": self.requested_model,
            "effective_model": self.effective_model,
            "provider": self.provider,
            "model_version": self.model_version,
            "configuration_hash": self.configuration_hash,
            "input_fingerprint": self.input_fingerprint,
            "invocation_refs": list(self.invocation_refs),
            "input_evaluation_refs": list(self.input_evaluation_refs),
            "execution_status": self.execution_status,
            "validation_status": self.validation_status,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "must_fix": [copy.deepcopy(item) for item in self.must_fix],
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "reevaluation_of": self.reevaluation_of,
            "schema_version": self.schema_version,
            "record_hash": self.record_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvaluationRecord":
        data = dict(value)
        data["invocation_refs"] = tuple(data.get("invocation_refs") or ())
        data["input_evaluation_refs"] = tuple(data.get("input_evaluation_refs") or ())
        data["reasons"] = tuple(data.get("reasons") or ())
        data["must_fix"] = tuple(copy.deepcopy(data.get("must_fix") or ()))
        try:
            record = cls(**data)
        except (TypeError, ValueError) as exc:
            raise EvaluationIntegrityError("evaluation record is incomplete") from exc
        record.validate()
        return record

    def validate(self) -> None:
        for name in (
            "evaluation_id", "project_id", "book_id", "run_id", "step_id",
            "operation_id", "evaluation_kind", "artifact_id", "criteria_version",
            "prompt_version", "context_package_id", "evaluator_kind",
            "evaluator_id", "evaluator_version", "created_at",
        ):
            _required_text(getattr(self, name), name)
        for name in (
            "artifact_hash", "criteria_hash", "context_hash",
            "configuration_hash", "input_fingerprint", "record_hash",
        ):
            _require_hash(getattr(self, name), name)
        if self.schema_version != SCHEMA_VERSION:
            raise EvaluationIntegrityError("unsupported evaluation record schema")
        _optional_text(self.series_id, "series_id")
        _optional_text(self.reevaluation_of, "reevaluation_of")
        if self.evaluation_kind != "QUALITY":
            raise EvaluationIntegrityError("unsupported evaluation kind")
        for value in self.invocation_refs:
            _required_text(value, "invocation_refs")
        for value in self.input_evaluation_refs:
            _required_text(value, "input_evaluation_refs")
        for value in self.reasons:
            _required_text(value, "reasons")
        if any(not isinstance(value, dict) for value in self.must_fix):
            raise EvaluationIntegrityError("must_fix entries must be objects")
        try:
            execution = ExecutionStatus(self.execution_status)
            validation = ValidationStatus(self.validation_status)
            decision = None if self.decision is None else QualityDecision(self.decision)
        except ValueError as exc:
            raise EvaluationIntegrityError("evaluation record status is invalid") from exc
        if execution is ExecutionStatus.COMPLETED and validation is ValidationStatus.VALID:
            if decision is None or self.completed_at is None:
                raise EvaluationIntegrityError("valid completed evaluation requires decision and time")
        elif decision is not None:
            raise EvaluationIntegrityError("non-valid evaluation cannot carry quality decision")
        if self.evaluator_kind == "LOCAL_DETERMINISTIC":
            if any(
                value is not None
                for value in (
                    self.requested_model,
                    self.effective_model,
                    self.provider,
                    self.model_version,
                )
            ) or self.prompt_version != LOCAL_PROMPT_VERSION:
                raise EvaluationIntegrityError("local evaluator carries fictitious model identity")
        expected = canonical_hash("GAP017_EVALUATION_RECORD_V1", self.unsigned_dict())
        if self.record_hash != expected:
            raise EvaluationIntegrityError("evaluation record hash mismatch")


@dataclass(frozen=True)
class EvaluationStart:
    evaluation_id: str
    attempt_token: str | None
    record: EvaluationRecord | None
    reused: bool
    output: dict[str, Any] | None = None


def _new_attempt(*, ordinal: int, recovered_by: str | None = None) -> dict[str, Any]:
    attempt = {
        "attempt_token": "EVAL-ATTEMPT-" + uuid4().hex,
        "ordinal": ordinal,
        "started_at": _now(),
        "status": ExecutionStatus.RUNNING.value,
    }
    if recovered_by is not None:
        attempt["recovered_by"] = _required_text(recovered_by, "recovered_by")
        attempt["recovery"] = True
    return attempt


def _initial_envelope(binding: EvaluationBinding, evaluation_id: str | None = None) -> dict[str, Any]:
    created_at = _now()
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": evaluation_id or "EVAL-" + uuid4().hex,
        "project_id": binding.project_id,
        "book_id": binding.book_id,
        "series_id": binding.series_id,
        "run_id": binding.run_id,
        "step_id": binding.step_id,
        "operation_id": binding.operation_id,
        "binding": binding.to_dict(),
        "binding_hash": binding_hash(binding),
        "cache_key": evaluation_cache_key(binding),
        "execution_status": ExecutionStatus.PREPARED.value,
        "validation_status": ValidationStatus.NOT_PERFORMED.value,
        "decision": None,
        "attempts": [],
        "audit": [{"event": "PREPARED", "at": created_at}],
        "created_at": created_at,
        "recovery_status": None,
    }


def _binding_from_envelope(state: Mapping[str, Any]) -> EvaluationBinding:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise EvaluationIntegrityError("unsupported evaluation envelope schema")
    if not isinstance(state.get("binding"), dict):
        raise EvaluationIntegrityError("evaluation binding is missing")
    binding = EvaluationBinding.from_dict(state["binding"])
    if state.get("binding_hash") != binding_hash(binding):
        raise EvaluationIntegrityError("evaluation binding hash mismatch")
    if state.get("cache_key") != evaluation_cache_key(binding):
        raise EvaluationIntegrityError("evaluation cache key mismatch")
    for name in ("project_id", "book_id", "series_id", "run_id", "step_id", "operation_id"):
        if state.get(name) != getattr(binding, name):
            raise EvaluationIntegrityError(f"evaluation envelope {name} mismatch")
    return binding


def _assert_same_binding(state: Mapping[str, Any], expected: EvaluationBinding) -> None:
    stored = _binding_from_envelope(state)
    if stored.to_dict() != expected.to_dict():
        raise EvaluationConflict("evaluation binding changed for the same operation")


def _record_from_envelope(state: Mapping[str, Any], binding: EvaluationBinding) -> EvaluationRecord:
    value = state.get("record")
    if not isinstance(value, dict):
        raise EvaluationIntegrityError("completed evaluation record is missing")
    record = EvaluationRecord.from_dict(value)
    for name in (
        "project_id", "book_id", "series_id", "run_id", "step_id",
        "operation_id", "evaluation_kind", "artifact_id", "artifact_hash",
        "criteria_version", "criteria_hash", "prompt_version",
        "context_package_id", "context_hash", "evaluator_kind", "evaluator_id",
        "evaluator_version", "requested_model", "effective_model", "provider",
        "model_version", "configuration_hash", "input_fingerprint",
        "reevaluation_of",
    ):
        if getattr(record, name) != getattr(binding, name):
            raise EvaluationIntegrityError(f"record {name} does not match binding")
    if record.evaluation_id != state.get("evaluation_id"):
        raise EvaluationIntegrityError("record evaluation_id mismatch")
    if state.get("record_hash") != record.record_hash:
        raise EvaluationIntegrityError("envelope record_hash mismatch")
    if state.get("execution_status") != record.execution_status:
        raise EvaluationIntegrityError("envelope execution status mismatch")
    if state.get("validation_status") != record.validation_status:
        raise EvaluationIntegrityError("envelope validation status mismatch")
    if state.get("decision") != record.decision:
        raise EvaluationIntegrityError("envelope decision mismatch")
    if list(record.invocation_refs) != list(binding.invocation_refs):
        raise EvaluationIntegrityError("record invocation_refs mismatch")
    if list(record.input_evaluation_refs) != list(binding.input_evaluation_refs):
        raise EvaluationIntegrityError("record input_evaluation_refs mismatch")
    return record


def prepare_evaluation(repository, binding: EvaluationBinding) -> str:
    """Persist PREPARED only. Normal execution should use start_evaluation."""
    with repository.evaluation_transaction(binding.operation_id) as state:
        if state:
            _assert_same_binding(state, binding)
            return _required_text(state.get("evaluation_id"), "evaluation_id")
        state.update(_initial_envelope(binding))
        return state["evaluation_id"]


def _output_from_envelope(state: Mapping[str, Any]) -> dict[str, Any]:
    output = state.get("output")
    if not isinstance(output, dict):
        raise EvaluationIntegrityError("completed evaluation output is missing")
    if state.get("output_hash") != canonical_hash("GAP017_EVALUATION_OUTPUT_V1", output):
        raise EvaluationIntegrityError("evaluation output hash mismatch")
    payload = output.get("payload")
    if isinstance(payload, dict) and (
        payload.get("DECISION", payload.get("decision")) != state.get("decision")
    ):
        raise EvaluationIntegrityError("evaluation output decision mismatch")
    return copy.deepcopy(output)


def start_evaluation(
    repository,
    binding: EvaluationBinding,
    *,
    claim_recovery: bool = False,
) -> EvaluationStart:
    with repository.evaluation_transaction(binding.operation_id) as state:
        created_here = not state
        if created_here:
            state.update(_initial_envelope(binding))
        else:
            _assert_same_binding(state, binding)
        if state.get("recovery_status") == "NEEDS_INTERVENTION":
            raise EvaluationNeedsIntervention("EVALUATION_NEEDS_INTERVENTION")
        if (
            state.get("execution_status") == ExecutionStatus.COMPLETED.value
            and state.get("validation_status") == ValidationStatus.VALID.value
        ):
            record = _record_from_envelope(state, binding)
            return EvaluationStart(
                record.evaluation_id, None, record, True,
                _output_from_envelope(state),
            )
        if not created_here:
            if state.get("recovery_status") == "NEEDS_INTERVENTION":
                raise EvaluationNeedsIntervention("EVALUATION_NEEDS_INTERVENTION")
            if state.get("execution_status") == ExecutionStatus.RUNNING.value:
                attempts = state.get("attempts")
                attempt = attempts[-1] if isinstance(attempts, list) and attempts else None
                if (
                    claim_recovery
                    and isinstance(attempt, dict)
                    and attempt.get("recovery") is True
                    and not attempt.get("claimed_at")
                ):
                    attempt["claimed_at"] = _now()
                    return EvaluationStart(
                        state["evaluation_id"], attempt["attempt_token"], None, False
                    )
                raise EvaluationInProgress("EVALUATION_IN_PROGRESS")
            raise EvaluationRecoveryRequired("EVALUATION_OPERATOR_RECOVERY_REQUIRED")
        attempt = _new_attempt(ordinal=1)
        state["attempts"].append(attempt)
        state["execution_status"] = ExecutionStatus.RUNNING.value
        state["audit"].append(
            {"event": "STARTED", "at": attempt["started_at"], "attempt_token": attempt["attempt_token"]}
        )
        return EvaluationStart(state["evaluation_id"], attempt["attempt_token"], None, False)


def finalize_evaluation(
    repository,
    binding: EvaluationBinding,
    *,
    attempt_token: str,
    decision: str,
    reasons: Sequence[str] = (),
    must_fix: Sequence[Mapping[str, Any]] = (),
    output: Mapping[str, Any] | None = None,
) -> EvaluationRecord:
    try:
        quality_decision = QualityDecision(decision)
    except ValueError as exc:
        raise ValueError("decision must be ACCEPT, REVISE or REJECT") from exc
    reason_values = tuple(_required_text(value, "reason") for value in reasons)
    must_fix_values = tuple(copy.deepcopy(dict(value)) for value in must_fix)
    with repository.evaluation_transaction(binding.operation_id) as state:
        if not state:
            raise EvaluationConflict("evaluation operation does not exist")
        _assert_same_binding(state, binding)
        if state.get("recovery_status") == "NEEDS_INTERVENTION":
            raise EvaluationNeedsIntervention("EVALUATION_NEEDS_INTERVENTION")
        attempts = state.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            raise EvaluationAttemptFenced("evaluation has no active attempt")
        attempt = attempts[-1]
        if (
            attempt.get("attempt_token") != attempt_token
            or attempt.get("status") != ExecutionStatus.RUNNING.value
            or state.get("execution_status") != ExecutionStatus.RUNNING.value
        ):
            raise EvaluationAttemptFenced("EVALUATION_ATTEMPT_FENCED")
        completed_at = _now()
        unsigned = {
            "evaluation_id": state["evaluation_id"],
            "project_id": binding.project_id,
            "book_id": binding.book_id,
            "series_id": binding.series_id,
            "run_id": binding.run_id,
            "step_id": binding.step_id,
            "operation_id": binding.operation_id,
            "evaluation_kind": binding.evaluation_kind,
            "artifact_id": binding.artifact_id,
            "artifact_hash": binding.artifact_hash,
            "criteria_version": binding.criteria_version,
            "criteria_hash": binding.criteria_hash,
            "prompt_version": binding.prompt_version,
            "context_package_id": binding.context_package_id,
            "context_hash": binding.context_hash,
            "evaluator_kind": binding.evaluator_kind,
            "evaluator_id": binding.evaluator_id,
            "evaluator_version": binding.evaluator_version,
            "requested_model": binding.requested_model,
            "effective_model": binding.effective_model,
            "provider": binding.provider,
            "model_version": binding.model_version,
            "configuration_hash": binding.configuration_hash,
            "input_fingerprint": binding.input_fingerprint,
            "invocation_refs": list(binding.invocation_refs),
            "input_evaluation_refs": list(binding.input_evaluation_refs),
            "execution_status": ExecutionStatus.COMPLETED.value,
            "validation_status": ValidationStatus.VALID.value,
            "decision": quality_decision.value,
            "reasons": list(reason_values),
            "must_fix": [copy.deepcopy(item) for item in must_fix_values],
            "created_at": state["created_at"],
            "completed_at": completed_at,
            "reevaluation_of": binding.reevaluation_of,
            "schema_version": SCHEMA_VERSION,
        }
        unsigned["record_hash"] = canonical_hash("GAP017_EVALUATION_RECORD_V1", unsigned)
        record = EvaluationRecord.from_dict(unsigned)
        output_value = copy.deepcopy(dict(output or {}))
        state["record"] = record.to_dict()
        state["record_hash"] = record.record_hash
        state["output"] = output_value
        state["output_hash"] = canonical_hash(
            "GAP017_EVALUATION_OUTPUT_V1", output_value
        )
        state["execution_status"] = ExecutionStatus.COMPLETED.value
        state["validation_status"] = ValidationStatus.VALID.value
        state["decision"] = quality_decision.value
        attempt.update(
            status=ExecutionStatus.COMPLETED.value,
            validation_status=ValidationStatus.VALID.value,
            decision=quality_decision.value,
            completed_at=completed_at,
        )
        state["audit"].append(
            {
                "event": "COMPLETED",
                "at": completed_at,
                "attempt_token": attempt_token,
                "record_hash": record.record_hash,
            }
        )
        return record


def load_completed_evaluation(repository, binding: EvaluationBinding) -> EvaluationRecord | None:
    state = repository.get_evaluation_envelope(binding.operation_id)
    if state is None:
        return None
    _assert_same_binding(state, binding)
    if state.get("recovery_status") == "NEEDS_INTERVENTION":
        raise EvaluationNeedsIntervention("EVALUATION_NEEDS_INTERVENTION")
    if (
        state.get("execution_status") == ExecutionStatus.COMPLETED.value
        and state.get("validation_status") == ValidationStatus.VALID.value
    ):
        _output_from_envelope(state)
        return _record_from_envelope(state, binding)
    return None


def recover_evaluation(
    repository,
    binding: EvaluationBinding,
    *,
    recovered_by: str,
    expected_attempt_token: str | None = None,
) -> EvaluationStart:
    intervention_reason: str | None = None
    result: EvaluationStart | None = None
    with repository.evaluation_transaction(binding.operation_id) as state:
        if not state:
            raise EvaluationNeedsIntervention("EVALUATION_BINDING_NOT_RECORDED")
        if state.get("recovery_status") == "NEEDS_INTERVENTION":
            raise EvaluationNeedsIntervention("EVALUATION_NEEDS_INTERVENTION")
        try:
            _assert_same_binding(state, binding)
        except EvaluationIntegrityError as exc:
            intervention_reason = str(exc)
            stamp = _now()
            state["recovery_status"] = "NEEDS_INTERVENTION"
            state["execution_status"] = ExecutionStatus.INTERRUPTED.value
            state["validation_status"] = ValidationStatus.INVALID.value
            state["decision"] = None
            state.setdefault("audit", []).append(
                {
                    "event": "NEEDS_INTERVENTION",
                    "at": stamp,
                    "reason": "UNPROVABLE_BINDING",
                }
            )
        if intervention_reason is None:
            if (
                state.get("execution_status") == ExecutionStatus.COMPLETED.value
                and state.get("validation_status") == ValidationStatus.VALID.value
            ):
                record = _record_from_envelope(state, binding)
                return EvaluationStart(
                    record.evaluation_id, None, record, True,
                    _output_from_envelope(state),
                )
            if state.get("execution_status") not in {
                ExecutionStatus.PREPARED.value,
                ExecutionStatus.RUNNING.value,
                ExecutionStatus.INTERRUPTED.value,
            }:
                raise EvaluationNeedsIntervention("EVALUATION_STATE_NOT_RECOVERABLE")
            stamp = _now()
            attempts = state.setdefault("attempts", [])
            if expected_attempt_token is not None and (
                not attempts or attempts[-1].get("attempt_token") != expected_attempt_token
            ):
                raise EvaluationAttemptFenced("EVALUATION_RECOVERY_ATTEMPT_FENCED")
            if (attempts and attempts[-1].get("recovery") is True
                    and attempts[-1].get("status") == ExecutionStatus.RUNNING.value
                    and expected_attempt_token is None):
                return EvaluationStart(
                    state["evaluation_id"], attempts[-1]["attempt_token"], None, False
                )
            if attempts and attempts[-1].get("status") == ExecutionStatus.RUNNING.value:
                attempts[-1].update(
                    status=ExecutionStatus.INTERRUPTED.value,
                    interrupted_at=stamp,
                    fenced_by=recovered_by,
                )
            attempt = _new_attempt(ordinal=len(attempts) + 1, recovered_by=recovered_by)
            attempts.append(attempt)
            state["execution_status"] = ExecutionStatus.RUNNING.value
            state["validation_status"] = ValidationStatus.NOT_PERFORMED.value
            state["decision"] = None
            state["recovery_status"] = "RECOVERED"
            state.setdefault("audit", []).append(
                {
                    "event": "RECOVERED",
                    "at": attempt["started_at"],
                    "attempt_token": attempt["attempt_token"],
                    "recovered_by": recovered_by,
                }
            )
            result = EvaluationStart(
                state["evaluation_id"], attempt["attempt_token"], None, False
            )
    if intervention_reason is not None:
        raise EvaluationNeedsIntervention("EVALUATION_BINDING_UNPROVABLE")
    assert result is not None
    return result


def find_evaluation(
    repository,
    *,
    evaluation_id: str,
) -> tuple[EvaluationBinding, EvaluationRecord | None, dict[str, Any]]:
    expected = _required_text(evaluation_id, "evaluation_id")
    matches = []
    for state in repository.list_evaluation_envelopes():
        if state.get("evaluation_id") == expected:
            matches.append(state)
    if len(matches) != 1:
        raise EvaluationNeedsIntervention(
            "EVALUATION_NOT_FOUND" if not matches else "EVALUATION_ID_AMBIGUOUS"
        )
    state = matches[0]
    binding = _binding_from_envelope(state)
    record = None
    if (
        state.get("execution_status") == ExecutionStatus.COMPLETED.value
        and state.get("validation_status") == ValidationStatus.VALID.value
    ):
        record = _record_from_envelope(state, binding)
        _output_from_envelope(state)
    return binding, record, copy.deepcopy(state)


def recover_evaluation_by_identity(
    repository,
    *,
    operation_id: str,
    evaluation_id: str,
    run_id: str,
    step_id: str,
    recovered_by: str,
    expected_attempt_token: str | None = None,
) -> EvaluationStart:
    state = repository.get_evaluation_envelope(
        _required_text(operation_id, "operation_id")
    )
    if state is None:
        raise EvaluationNeedsIntervention("EVALUATION_BINDING_NOT_RECORDED")
    if any(state.get(name) != _required_text(value, name) for name, value in (
        ("evaluation_id", evaluation_id), ("run_id", run_id),
        ("step_id", step_id), ("operation_id", operation_id),
    )):
        raise EvaluationConflict("EVALUATION_RECOVERY_IDENTITY_MISMATCH")
    try:
        binding = _binding_from_envelope(state)
    except EvaluationIntegrityError:
        with repository.evaluation_transaction(operation_id) as mutable:
            mutable["recovery_status"] = "NEEDS_INTERVENTION"
            mutable["execution_status"] = ExecutionStatus.INTERRUPTED.value
            mutable["validation_status"] = ValidationStatus.INVALID.value
            mutable["decision"] = None
            mutable.setdefault("audit", []).append({
                "event": "NEEDS_INTERVENTION",
                "at": _now(),
                "reason": "UNPROVABLE_BINDING",
            })
        raise EvaluationNeedsIntervention("EVALUATION_BINDING_UNPROVABLE") from None
    if (
        state.get("evaluation_id") != _required_text(evaluation_id, "evaluation_id")
        or binding.run_id != _required_text(run_id, "run_id")
        or binding.step_id != _required_text(step_id, "step_id")
        or binding.operation_id != operation_id
    ):
        raise EvaluationConflict("EVALUATION_RECOVERY_IDENTITY_MISMATCH")
    return recover_evaluation(repository, binding, recovered_by=recovered_by,
                              expected_attempt_token=expected_attempt_token)


__all__ = [
    "CACHE_DOMAIN",
    "LOCAL_PROMPT_VERSION",
    "SCHEMA_VERSION",
    "EvaluationAttemptFenced",
    "EvaluationBinding",
    "EvaluationConflict",
    "EvaluationError",
    "EvaluationInProgress",
    "EvaluationIntegrityError",
    "EvaluationNeedsIntervention",
    "EvaluationRecord",
    "EvaluationRecoveryRequired",
    "EvaluationStart",
    "ExecutionStatus",
    "QualityDecision",
    "ValidationStatus",
    "binding_hash",
    "canonical_hash",
    "canonical_json",
    "compute_configuration_hash",
    "compute_criteria_hash",
    "compute_input_fingerprint",
    "evaluation_cache_key",
    "find_evaluation",
    "finalize_evaluation",
    "load_completed_evaluation",
    "prepare_evaluation",
    "recover_evaluation",
    "recover_evaluation_by_identity",
    "start_evaluation",
]

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, Iterable

from app.p20_core.domain_records import (
    DomainContractError,
    DomainId,
    DomainNamespace,
    require_domain_id,
)
from app.p20_core.project_repository import ProjectRepository, ProjectStorageError, StorageScope


MEMORY_EXTRACTION_SCHEMA_VERSION = 1

SUPPORTED_MEMORY_RECORD_TYPES = frozenset(
    {
        "FACT",
        "CHARACTER_STATE",
        "EVENT",
        "KNOWLEDGE_EVENT",
        "THREAD",
        "SETUP",
        "PAYOFF",
        "RELATIONSHIP_CHANGE",
    }
)


class MemoryExtractionError(ValueError):
    pass


class ModelInvocationRole(str, Enum):
    EXTRACTOR = "EXTRACTOR"
    VERIFIER = "VERIFIER"


class VerificationAxisStatus(str, Enum):
    ACCEPT = "ACCEPT"
    REVISE = "REVISE"
    REJECT = "REJECT"


class MemoryExtractionDecision(str, Enum):
    ACCEPT = "ACCEPT"
    REVISE = "REVISE"
    REJECT = "REJECT"


class MemoryExtractionStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    ACCEPT = "ACCEPT"
    REVISE = "REVISE"
    REJECT = "REJECT"
    ESCALATED = "ESCALATED"


class SceneQualityStatus(str, Enum):
    ACCEPT = "ACCEPT"
    REVISE = "REVISE"
    REJECT = "REJECT"
    UNKNOWN = "UNKNOWN"


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _serialize(value: Any) -> Any:
    if isinstance(value, DomainId):
        return value.value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(value[key]) for key in sorted(value, key=str)}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _serialize(value.to_dict())
    return value


class SerializableExtractionRecord:
    def to_dict(self) -> dict[str, Any]:
        return {
            item.name: _serialize(getattr(self, item.name))
            for item in fields(self)
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise MemoryExtractionError(f"{field_name} must be a string")
    if value != value.strip() or not value:
        raise MemoryExtractionError(f"{field_name} is required")
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _positive_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise MemoryExtractionError(f"{field_name} must be a positive integer")
    return value


def _non_negative_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MemoryExtractionError(f"{field_name} must be a non-negative integer")
    return value


def _number(value: Any, field_name: str) -> int | float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise MemoryExtractionError(f"{field_name} must be a number")
    return value


def _confidence(value: Any, field_name: str = "confidence") -> int | float:
    score = _number(value, field_name)
    if score < 0 or score > 1:
        raise MemoryExtractionError(f"{field_name} must be between 0 and 1")
    return score


def _bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise MemoryExtractionError(f"{field_name} must be a boolean")
    return value


def _text_tuple(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or values is None:
        raise MemoryExtractionError(f"{field_name} must be a list")
    return tuple(_required_text(value, field_name) for value in values)


def _json_object(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MemoryExtractionError(f"{field_name} must be an object")
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise MemoryExtractionError(f"{field_name} must be JSON-serializable") from exc
    return dict(value)


def _coerce_enum(enum_type: type[Enum], value: Any, field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise MemoryExtractionError(f"{field_name} is unsupported") from exc


def _record_sort_key(record: Any) -> tuple[str, str]:
    return (
        str(getattr(record, "memory_record_type", "")),
        str(getattr(record, "record_id", "")),
    )


def _record_json(record: Any) -> str:
    to_json = getattr(record, "to_json", None)
    if not callable(to_json):
        raise MemoryExtractionError("candidate record must provide to_json")
    return str(to_json())


def _record_scene_values(record: Any) -> tuple[str, ...]:
    fields_to_check = (
        "established_scene_id",
        "source_scene_id",
        "learned_at_scene_id",
        "opened_scene_id",
        "closed_scene_id",
        "created_scene_id",
        "completed_scene_id",
    )
    values: list[str] = []
    for field_name in fields_to_check:
        value = getattr(record, field_name, None)
        if value is not None:
            values.append(str(value))
    return tuple(values)


def _record_artifact_values(record: Any) -> tuple[str, ...]:
    values: list[str] = []
    value = getattr(record, "source_artifact_ref", None)
    if value is not None:
        values.append(str(value))
    return tuple(values)


def _validate_candidate_record(record: Any, scope: StorageScope) -> Any:
    record_type = str(getattr(record, "memory_record_type", ""))
    if record_type not in SUPPORTED_MEMORY_RECORD_TYPES:
        raise MemoryExtractionError("unsupported structured memory record type")

    if hasattr(record, "require_project_scope"):
        try:
            record.require_project_scope(scope)
        except (DomainContractError, ProjectStorageError) as exc:
            raise MemoryExtractionError("candidate record project scope does not match") from exc

    declared_scope = getattr(record, "scope", None)
    if declared_scope is not None and declared_scope != scope:
        raise MemoryExtractionError("candidate record project scope does not match")

    _record_json(record)
    return record


def compute_source_hash(source_text: str) -> str:
    return hashlib.sha256(str(source_text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ModelInvocation(SerializableExtractionRecord):
    role: ModelInvocationRole | str
    provider: str
    model: str
    call_id: str
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "role",
            _coerce_enum(ModelInvocationRole, self.role, "role"),
        )
        object.__setattr__(self, "provider", _required_text(self.provider, "provider"))
        object.__setattr__(self, "model", _required_text(self.model, "model"))
        object.__setattr__(self, "call_id", _required_text(self.call_id, "call_id"))
        object.__setattr__(self, "metadata", _json_object(self.metadata, "metadata"))


@dataclass(frozen=True)
class SceneMemorySource(SerializableExtractionRecord):
    project_id: DomainId | str
    source_scene_id: DomainId | str
    source_artifact_ref: str
    source_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_id",
            require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"),
        )
        object.__setattr__(
            self,
            "source_scene_id",
            require_domain_id(self.source_scene_id, DomainNamespace.SCENE, "source_scene_id"),
        )
        object.__setattr__(
            self,
            "source_artifact_ref",
            _required_text(self.source_artifact_ref, "source_artifact_ref"),
        )
        object.__setattr__(self, "source_hash", _required_text(self.source_hash, "source_hash"))

    @classmethod
    def from_text(
        cls,
        *,
        project_id: DomainId | str,
        source_scene_id: DomainId | str,
        source_artifact_ref: str,
        source_text: str,
    ) -> "SceneMemorySource":
        return cls(
            project_id=project_id,
            source_scene_id=source_scene_id,
            source_artifact_ref=source_artifact_ref,
            source_hash=compute_source_hash(source_text),
        )


@dataclass(frozen=True)
class RelationshipChangeRecord(SerializableExtractionRecord):
    relationship_change_id: DomainId | str
    project_id: DomainId | str
    subject_character_id: DomainId | str
    object_character_id: DomainId | str
    relationship_type: str
    change_summary: str
    source_scene_id: DomainId | str
    source_artifact_ref: str
    confidence: int | float
    version: int
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relationship_change_id",
            require_domain_id(
                self.relationship_change_id,
                DomainNamespace.RELATIONSHIP,
                "relationship_change_id",
            ),
        )
        object.__setattr__(
            self,
            "project_id",
            require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"),
        )
        object.__setattr__(
            self,
            "subject_character_id",
            require_domain_id(
                self.subject_character_id,
                DomainNamespace.CHARACTER,
                "subject_character_id",
            ),
        )
        object.__setattr__(
            self,
            "object_character_id",
            require_domain_id(
                self.object_character_id,
                DomainNamespace.CHARACTER,
                "object_character_id",
            ),
        )
        object.__setattr__(self, "relationship_type", _required_text(self.relationship_type, "relationship_type"))
        object.__setattr__(self, "change_summary", _required_text(self.change_summary, "change_summary"))
        object.__setattr__(
            self,
            "source_scene_id",
            require_domain_id(self.source_scene_id, DomainNamespace.SCENE, "source_scene_id"),
        )
        object.__setattr__(
            self,
            "source_artifact_ref",
            _required_text(self.source_artifact_ref, "source_artifact_ref"),
        )
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(self, "version", _positive_int(self.version, "version"))
        object.__setattr__(self, "created_at", _required_text(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _required_text(self.updated_at, "updated_at"))

    @property
    def record_id(self) -> DomainId:
        return self.relationship_change_id

    @property
    def memory_record_type(self) -> str:
        return "RELATIONSHIP_CHANGE"

    @property
    def scope(self) -> StorageScope:
        return StorageScope.project(str(self.project_id))

    def require_project_scope(self, scope: StorageScope) -> None:
        if scope != self.scope:
            raise MemoryExtractionError("relationship change project scope does not match")


@dataclass(frozen=True)
class MemoryExtractionPolicy(SerializableExtractionRecord):
    max_attempts: int
    escalation_target: str = "USER"

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_attempts", _positive_int(self.max_attempts, "max_attempts"))
        object.__setattr__(
            self,
            "escalation_target",
            _required_text(self.escalation_target, "escalation_target"),
        )


DEFAULT_MEMORY_EXTRACTION_POLICY = MemoryExtractionPolicy(max_attempts=2)


@dataclass(frozen=True)
class StructuredMemoryExtractionCandidate(SerializableExtractionRecord):
    candidate_id: str
    project_id: DomainId | str
    source_scene_id: DomainId | str
    source_artifact_ref: str
    source_hash: str
    extraction_attempt: int
    candidate_records: Iterable[Any]
    extractor_call: ModelInvocation
    created_at: str
    version: int = 1
    memory_extraction_status: MemoryExtractionStatus | str = MemoryExtractionStatus.CANDIDATE
    scene_quality_status: SceneQualityStatus | str | None = None
    relationship_changes: Iterable[RelationshipChangeRecord] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _required_text(self.candidate_id, "candidate_id"))
        object.__setattr__(
            self,
            "project_id",
            require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"),
        )
        object.__setattr__(
            self,
            "source_scene_id",
            require_domain_id(self.source_scene_id, DomainNamespace.SCENE, "source_scene_id"),
        )
        object.__setattr__(
            self,
            "source_artifact_ref",
            _required_text(self.source_artifact_ref, "source_artifact_ref"),
        )
        object.__setattr__(self, "source_hash", _required_text(self.source_hash, "source_hash"))
        object.__setattr__(
            self,
            "extraction_attempt",
            _positive_int(self.extraction_attempt, "extraction_attempt"),
        )
        object.__setattr__(self, "created_at", _required_text(self.created_at, "created_at"))
        object.__setattr__(self, "version", _positive_int(self.version, "version"))

        memory_status = _coerce_enum(
            MemoryExtractionStatus,
            self.memory_extraction_status,
            "memory_extraction_status",
        )
        if memory_status != MemoryExtractionStatus.CANDIDATE:
            raise MemoryExtractionError("extractor cannot assign final memory_extraction_status")
        object.__setattr__(self, "memory_extraction_status", memory_status)

        scene_quality = (
            SceneQualityStatus.UNKNOWN
            if self.scene_quality_status is None
            else _coerce_enum(SceneQualityStatus, self.scene_quality_status, "scene_quality_status")
        )
        object.__setattr__(self, "scene_quality_status", scene_quality)

        if not isinstance(self.extractor_call, ModelInvocation):
            raise MemoryExtractionError("extractor_call must be a ModelInvocation")
        if self.extractor_call.role != ModelInvocationRole.EXTRACTOR:
            raise MemoryExtractionError("extractor_call must use EXTRACTOR role")

        scope = self.scope
        records = tuple(
            _validate_candidate_record(record, scope)
            for record in tuple(self.candidate_records)
        )
        relationship_changes = tuple(
            _validate_candidate_record(record, scope)
            for record in tuple(self.relationship_changes)
        )
        if not records and not relationship_changes:
            raise MemoryExtractionError("candidate must include at least one memory entity")
        object.__setattr__(self, "candidate_records", tuple(sorted(records, key=_record_sort_key)))
        object.__setattr__(
            self,
            "relationship_changes",
            tuple(sorted(relationship_changes, key=_record_sort_key)),
        )

    @classmethod
    def from_source(
        cls,
        *,
        candidate_id: str,
        source: SceneMemorySource,
        extraction_attempt: int,
        candidate_records: Iterable[Any],
        extractor_call: ModelInvocation,
        created_at: str,
        version: int = 1,
        memory_extraction_status: MemoryExtractionStatus | str = MemoryExtractionStatus.CANDIDATE,
        scene_quality_status: SceneQualityStatus | str | None = None,
        relationship_changes: Iterable[RelationshipChangeRecord] = (),
    ) -> "StructuredMemoryExtractionCandidate":
        return cls(
            candidate_id=candidate_id,
            project_id=source.project_id,
            source_scene_id=source.source_scene_id,
            source_artifact_ref=source.source_artifact_ref,
            source_hash=source.source_hash,
            extraction_attempt=extraction_attempt,
            candidate_records=candidate_records,
            extractor_call=extractor_call,
            created_at=created_at,
            version=version,
            memory_extraction_status=memory_extraction_status,
            scene_quality_status=scene_quality_status,
            relationship_changes=relationship_changes,
        )

    @property
    def scope(self) -> StorageScope:
        return StorageScope.project(str(self.project_id))

    @property
    def candidate_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @property
    def memory_entities(self) -> tuple[Any, ...]:
        return tuple(sorted((*self.candidate_records, *self.relationship_changes), key=_record_sort_key))


@dataclass(frozen=True)
class MemoryExtractionVerification(SerializableExtractionRecord):
    candidate_id: str
    candidate_hash: str
    project_id: DomainId | str
    source_scene_id: DomainId | str
    source_artifact_ref: str
    source_hash: str
    verifier_call: ModelInvocation
    precision_status: VerificationAxisStatus | str
    completeness_status: VerificationAxisStatus | str
    decision: MemoryExtractionDecision | str
    memory_extraction_status: MemoryExtractionStatus | str
    retry_count: int
    escalation_required: bool
    precision_reasons: Iterable[str]
    completeness_reasons: Iterable[str]
    must_fix: Iterable[str]
    scene_quality_status: SceneQualityStatus | str
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _required_text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "candidate_hash", _required_text(self.candidate_hash, "candidate_hash"))
        object.__setattr__(
            self,
            "project_id",
            require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"),
        )
        object.__setattr__(
            self,
            "source_scene_id",
            require_domain_id(self.source_scene_id, DomainNamespace.SCENE, "source_scene_id"),
        )
        object.__setattr__(
            self,
            "source_artifact_ref",
            _required_text(self.source_artifact_ref, "source_artifact_ref"),
        )
        object.__setattr__(self, "source_hash", _required_text(self.source_hash, "source_hash"))
        if not isinstance(self.verifier_call, ModelInvocation):
            raise MemoryExtractionError("verifier_call must be a ModelInvocation")
        if self.verifier_call.role != ModelInvocationRole.VERIFIER:
            raise MemoryExtractionError("verifier_call must use VERIFIER role")
        object.__setattr__(
            self,
            "precision_status",
            _coerce_enum(VerificationAxisStatus, self.precision_status, "precision_status"),
        )
        object.__setattr__(
            self,
            "completeness_status",
            _coerce_enum(VerificationAxisStatus, self.completeness_status, "completeness_status"),
        )
        object.__setattr__(
            self,
            "decision",
            _coerce_enum(MemoryExtractionDecision, self.decision, "decision"),
        )
        object.__setattr__(
            self,
            "memory_extraction_status",
            _coerce_enum(
                MemoryExtractionStatus,
                self.memory_extraction_status,
                "memory_extraction_status",
            ),
        )
        object.__setattr__(self, "retry_count", _non_negative_int(self.retry_count, "retry_count"))
        object.__setattr__(self, "escalation_required", _bool(self.escalation_required, "escalation_required"))
        object.__setattr__(self, "precision_reasons", _text_tuple(self.precision_reasons, "precision_reasons"))
        object.__setattr__(
            self,
            "completeness_reasons",
            _text_tuple(self.completeness_reasons, "completeness_reasons"),
        )
        object.__setattr__(self, "must_fix", _text_tuple(self.must_fix, "must_fix"))
        object.__setattr__(
            self,
            "scene_quality_status",
            _coerce_enum(SceneQualityStatus, self.scene_quality_status, "scene_quality_status"),
        )
        object.__setattr__(self, "version", _positive_int(self.version, "version"))

        expected_decision = _decision_from_axes(self.precision_status, self.completeness_status)
        if self.decision != expected_decision:
            raise MemoryExtractionError("decision must match precision and completeness statuses")
        if self.decision == MemoryExtractionDecision.ACCEPT and self.memory_extraction_status != MemoryExtractionStatus.ACCEPT:
            raise MemoryExtractionError("ACCEPT decision must produce ACCEPT memory_extraction_status")
        if self.decision == MemoryExtractionDecision.REJECT and self.memory_extraction_status != MemoryExtractionStatus.REJECT:
            raise MemoryExtractionError("REJECT decision must produce REJECT memory_extraction_status")
        if self.decision == MemoryExtractionDecision.REVISE and self.memory_extraction_status not in {
            MemoryExtractionStatus.REVISE,
            MemoryExtractionStatus.ESCALATED,
        }:
            raise MemoryExtractionError("REVISE decision must produce REVISE or ESCALATED status")

    def audit_payload(self) -> dict[str, Any]:
        return self.to_dict()


@dataclass(frozen=True)
class StructuredMemoryCommitResult(SerializableExtractionRecord):
    candidate_id: str
    candidate_hash: str
    project_id: DomainId | str
    memory_extraction_status: MemoryExtractionStatus | str
    committed_record_ids: Iterable[str]
    committed_record_types: Iterable[str]
    project_db_path: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _required_text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "candidate_hash", _required_text(self.candidate_hash, "candidate_hash"))
        object.__setattr__(
            self,
            "project_id",
            require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"),
        )
        object.__setattr__(
            self,
            "memory_extraction_status",
            _coerce_enum(
                MemoryExtractionStatus,
                self.memory_extraction_status,
                "memory_extraction_status",
            ),
        )
        object.__setattr__(
            self,
            "committed_record_ids",
            _text_tuple(self.committed_record_ids, "committed_record_ids"),
        )
        object.__setattr__(
            self,
            "committed_record_types",
            _text_tuple(self.committed_record_types, "committed_record_types"),
        )
        object.__setattr__(self, "project_db_path", _required_text(self.project_db_path, "project_db_path"))


def _decision_from_axes(
    precision_status: VerificationAxisStatus,
    completeness_status: VerificationAxisStatus,
) -> MemoryExtractionDecision:
    if VerificationAxisStatus.REJECT in {precision_status, completeness_status}:
        return MemoryExtractionDecision.REJECT
    if VerificationAxisStatus.REVISE in {precision_status, completeness_status}:
        return MemoryExtractionDecision.REVISE
    return MemoryExtractionDecision.ACCEPT


def _status_for_decision(
    decision: MemoryExtractionDecision,
    *,
    attempt: int,
    policy: MemoryExtractionPolicy,
) -> tuple[MemoryExtractionStatus, bool]:
    if decision == MemoryExtractionDecision.ACCEPT:
        return MemoryExtractionStatus.ACCEPT, False
    if decision == MemoryExtractionDecision.REJECT:
        return MemoryExtractionStatus.REJECT, False
    if attempt >= policy.max_attempts:
        return MemoryExtractionStatus.ESCALATED, True
    return MemoryExtractionStatus.REVISE, False


def _candidate_source_mismatches(
    candidate: StructuredMemoryExtractionCandidate,
    source: SceneMemorySource,
) -> tuple[str, ...]:
    mismatches: list[str] = []
    if candidate.project_id != source.project_id:
        mismatches.append("candidate project_id does not match source project_id")
    if candidate.source_scene_id != source.source_scene_id:
        mismatches.append("candidate source_scene_id does not match source scene")
    if candidate.source_artifact_ref != source.source_artifact_ref:
        mismatches.append("candidate source_artifact_ref does not match source artifact")
    if candidate.source_hash != source.source_hash:
        mismatches.append("candidate source_hash does not match source hash")
    return tuple(mismatches)


def _candidate_record_provenance_mismatches(
    candidate: StructuredMemoryExtractionCandidate,
) -> tuple[str, ...]:
    mismatches: list[str] = []
    expected_scene = str(candidate.source_scene_id)
    expected_artifact = str(candidate.source_artifact_ref)

    for record in candidate.memory_entities:
        record_label = f"{getattr(record, 'memory_record_type', 'UNKNOWN')}:{getattr(record, 'record_id', 'UNKNOWN')}"
        scene_values = _record_scene_values(record)
        if scene_values and expected_scene not in scene_values:
            mismatches.append(f"{record_label} does not reference source scene {expected_scene}")
        artifact_values = _record_artifact_values(record)
        if artifact_values and expected_artifact not in artifact_values:
            mismatches.append(f"{record_label} does not reference source artifact {expected_artifact}")
    return tuple(mismatches)


def verify_memory_extraction_candidate(
    candidate: StructuredMemoryExtractionCandidate,
    *,
    source: SceneMemorySource,
    verifier_call: ModelInvocation,
    precision_status: VerificationAxisStatus | str,
    completeness_status: VerificationAxisStatus | str,
    precision_reasons: Iterable[str] = (),
    completeness_reasons: Iterable[str] = (),
    must_fix: Iterable[str] = (),
    policy: MemoryExtractionPolicy = DEFAULT_MEMORY_EXTRACTION_POLICY,
) -> MemoryExtractionVerification:
    if not isinstance(candidate, StructuredMemoryExtractionCandidate):
        raise MemoryExtractionError("candidate must be a StructuredMemoryExtractionCandidate")
    if not isinstance(source, SceneMemorySource):
        raise MemoryExtractionError("source must be a SceneMemorySource")
    if not isinstance(verifier_call, ModelInvocation):
        raise MemoryExtractionError("verifier_call must be a ModelInvocation")
    if verifier_call.role != ModelInvocationRole.VERIFIER:
        raise MemoryExtractionError("verifier_call must use VERIFIER role")
    if verifier_call.call_id == candidate.extractor_call.call_id:
        raise MemoryExtractionError("extractor and verifier invocations must be independent")
    if not isinstance(policy, MemoryExtractionPolicy):
        raise MemoryExtractionError("policy must be a MemoryExtractionPolicy")

    precision = _coerce_enum(VerificationAxisStatus, precision_status, "precision_status")
    completeness = _coerce_enum(VerificationAxisStatus, completeness_status, "completeness_status")
    resolved_precision_reasons = list(_text_tuple(precision_reasons, "precision_reasons"))
    resolved_completeness_reasons = list(_text_tuple(completeness_reasons, "completeness_reasons"))
    resolved_must_fix = list(_text_tuple(must_fix, "must_fix"))

    provenance_mismatches = (
        *_candidate_source_mismatches(candidate, source),
        *_candidate_record_provenance_mismatches(candidate),
    )
    if provenance_mismatches:
        precision = VerificationAxisStatus.REJECT
        resolved_precision_reasons.extend(provenance_mismatches)
        resolved_must_fix.extend(provenance_mismatches)

    decision = _decision_from_axes(precision, completeness)
    memory_status, escalation_required = _status_for_decision(
        decision,
        attempt=candidate.extraction_attempt,
        policy=policy,
    )

    return MemoryExtractionVerification(
        candidate_id=candidate.candidate_id,
        candidate_hash=candidate.candidate_hash,
        project_id=candidate.project_id,
        source_scene_id=candidate.source_scene_id,
        source_artifact_ref=candidate.source_artifact_ref,
        source_hash=candidate.source_hash,
        verifier_call=verifier_call,
        precision_status=precision,
        completeness_status=completeness,
        decision=decision,
        memory_extraction_status=memory_status,
        retry_count=max(0, candidate.extraction_attempt - 1),
        escalation_required=escalation_required,
        precision_reasons=resolved_precision_reasons,
        completeness_reasons=resolved_completeness_reasons,
        must_fix=resolved_must_fix,
        scene_quality_status=candidate.scene_quality_status,
    )


def commit_verified_memory_candidate(
    repository: ProjectRepository,
    candidate: StructuredMemoryExtractionCandidate,
    verification: MemoryExtractionVerification,
) -> StructuredMemoryCommitResult:
    if not isinstance(repository, ProjectRepository):
        raise MemoryExtractionError("repository must be a ProjectRepository")
    if not isinstance(candidate, StructuredMemoryExtractionCandidate):
        raise MemoryExtractionError("candidate must be a StructuredMemoryExtractionCandidate")
    if not isinstance(verification, MemoryExtractionVerification):
        raise MemoryExtractionError("verification must be a MemoryExtractionVerification")

    if repository.scope != candidate.scope:
        raise MemoryExtractionError("candidate project scope does not match repository scope")
    if verification.project_id != candidate.project_id:
        raise MemoryExtractionError("verification project_id does not match candidate")
    if verification.candidate_id != candidate.candidate_id:
        raise MemoryExtractionError("verification candidate_id does not match candidate")
    if verification.candidate_hash != candidate.candidate_hash:
        raise MemoryExtractionError("verification candidate_hash does not match candidate")
    if verification.memory_extraction_status != MemoryExtractionStatus.ACCEPT:
        raise MemoryExtractionError("only ACCEPT memory_extraction_status can be committed")
    if verification.decision != MemoryExtractionDecision.ACCEPT:
        raise MemoryExtractionError("only verifier ACCEPT can be committed")
    if verification.escalation_required:
        raise MemoryExtractionError("escalated memory extraction cannot be committed")

    provenance_mismatches = _candidate_record_provenance_mismatches(candidate)
    if provenance_mismatches:
        raise MemoryExtractionError("candidate provenance does not match source")

    committed_ids: list[str] = []
    committed_types: list[str] = []
    with repository.domain_transaction() as tx:
        for record in candidate.memory_entities:
            tx.add_structured_memory_record(record)
            committed_ids.append(str(getattr(record, "record_id")))
            committed_types.append(str(getattr(record, "memory_record_type")))

    return StructuredMemoryCommitResult(
        candidate_id=candidate.candidate_id,
        candidate_hash=candidate.candidate_hash,
        project_id=candidate.project_id,
        memory_extraction_status=verification.memory_extraction_status,
        committed_record_ids=committed_ids,
        committed_record_types=committed_types,
        project_db_path=str(repository.db_path),
    )


__all__ = [
    "DEFAULT_MEMORY_EXTRACTION_POLICY",
    "MEMORY_EXTRACTION_SCHEMA_VERSION",
    "SUPPORTED_MEMORY_RECORD_TYPES",
    "MemoryExtractionDecision",
    "MemoryExtractionError",
    "MemoryExtractionPolicy",
    "MemoryExtractionStatus",
    "MemoryExtractionVerification",
    "ModelInvocation",
    "ModelInvocationRole",
    "RelationshipChangeRecord",
    "SceneMemorySource",
    "SceneQualityStatus",
    "StructuredMemoryCommitResult",
    "StructuredMemoryExtractionCandidate",
    "VerificationAxisStatus",
    "commit_verified_memory_candidate",
    "compute_source_hash",
    "verify_memory_extraction_candidate",
]

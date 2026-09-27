"""GAP-018 immutable candidate snapshot; internal QA is not author approval."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from app.p20_core.book_qa import load_book_qa_report
from app.p20_core.cross_store_recovery import (
    ArtifactRoot, CrossStoreOperationPlan, CrossStoreRecoveryService, RecoveryStatus,
)
from app.p20_core.manuscript_version import load_manuscript, validate_current_manuscript_inputs
from app.p20_core.project_repository import ProjectRepository


class CandidateIntegrityError(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _artifact_ref(candidate_id: str) -> str:
    return f"candidate_masters/v1/{candidate_id}.json"


@dataclass(frozen=True)
class CandidateRequest:
    project_id: str
    book_id: str
    manuscript_id: str
    content_hash: str
    manifest_hash: str
    qa_id: str
    report_hash: str
    candidate_version: int
    request_id: str
    parent_candidate_id: str | None = None


@dataclass(frozen=True)
class CandidateMaster:
    schema_version: int
    candidate_id: str
    project_id: str
    book_id: str
    manuscript_id: str
    manuscript_version: int
    content_hash: str
    manifest_hash: str
    book_bible_snapshot_ref: str
    book_bible_hash: str
    canon_snapshot_ref: str
    canon_snapshot_hash: str
    qa_id: str
    report_hash: str
    candidate_version: int
    parent_candidate_id: str | None
    request_id: str
    status: str
    created_at: str
    artifact_ref: str
    artifact_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CandidateMaster:
        try:
            result = cls(**value)
        except (KeyError, TypeError, ValueError) as exc:
            raise CandidateIntegrityError("candidate record is invalid") from exc
        if (result.schema_version != 1 or result.status != "READY_FOR_AUTHOR_REVIEW"
                or result.artifact_hash != _record_hash(result)
                or result.candidate_id != "CM-" + result.artifact_hash
                or result.artifact_ref != _artifact_ref(result.candidate_id)):
            raise CandidateIntegrityError("candidate integrity mismatch")
        return result


def _record_hash(record: CandidateMaster) -> str:
    payload = record.to_dict()
    for key in ("candidate_id", "created_at", "artifact_ref", "artifact_hash"):
        payload.pop(key)
    return _hash(payload)


def create_candidate(repository: ProjectRepository, request: CandidateRequest) -> CandidateMaster:
    if (request.project_id, request.book_id) != (repository.context.project_id, repository.context.book_id):
        raise CandidateIntegrityError("candidate scope mismatch")
    if type(request.candidate_version) is not int or request.candidate_version < 1 or not request.request_id.strip():
        raise CandidateIntegrityError("candidate version or request ID invalid")
    manuscript = load_manuscript(repository, request.manuscript_id)
    report = load_book_qa_report(repository, request.qa_id)
    if (request.content_hash, request.manifest_hash) != (manuscript.content_hash, manuscript.manifest_hash):
        raise CandidateIntegrityError("candidate manuscript hash mismatch")
    if (report.manuscript_id, report.content_hash, report.manifest_hash, report.report_hash) != (
        manuscript.manuscript_id, manuscript.content_hash, manuscript.manifest_hash, request.report_hash,
    ):
        raise CandidateIntegrityError("candidate Book QA binding mismatch")
    if (report.execution_status, report.validation_status, report.quality_decision) != (
        "COMPLETED", "VALID", "ACCEPT",
    ):
        raise CandidateIntegrityError("only completed valid Book QA ACCEPT can create candidate")
    if (report.book_bible_hash, report.canon_snapshot_hash) != (
        manuscript.book_bible_hash, manuscript.canon_snapshot_hash,
    ):
        raise CandidateIntegrityError("candidate input snapshot mismatch")
    if request.parent_candidate_id is not None:
        parent = load_candidate(repository, request.parent_candidate_id)
        if request.candidate_version != parent.candidate_version + 1:
            raise CandidateIntegrityError("candidate version does not follow parent")
    elif request.candidate_version != 1:
        raise CandidateIntegrityError("first candidate version must be one")
    from app.p20_core.gap018_receipts import reserve_request
    reserve_request(repository, "CANDIDATE", request.request_id, asdict(request))
    provisional = CandidateMaster(
        1, "", request.project_id, request.book_id, manuscript.manuscript_id,
        manuscript.version, manuscript.content_hash, manuscript.manifest_hash,
        manuscript.book_bible_snapshot_ref, manuscript.book_bible_hash,
        manuscript.canon_snapshot_ref, manuscript.canon_snapshot_hash,
        report.qa_id, report.report_hash, request.candidate_version,
        request.parent_candidate_id, request.request_id, "READY_FOR_AUTHOR_REVIEW",
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "", "",
    )
    artifact_hash = _record_hash(provisional)
    candidate_id = "CM-" + artifact_hash
    candidate = CandidateMaster(**{**provisional.__dict__, "candidate_id": candidate_id,
                                  "artifact_ref": _artifact_ref(candidate_id),
                                  "artifact_hash": artifact_hash})
    operation_id = "gap018-candidate-" + artifact_hash
    if repository.get_gap018_record("CANDIDATE", candidate_id) is not None:
        return load_candidate(repository, candidate_id)
    if repository.get_gap018_manuscript_head() != (
        manuscript.manuscript_id, manuscript.version, manuscript.manifest_hash,
    ):
        raise CandidateIntegrityError("candidate manuscript head is stale")
    if validate_current_manuscript_inputs(repository, manuscript).status != "VALID":
        raise CandidateIntegrityError("candidate manuscript inputs are stale")
    plan = CrossStoreOperationPlan(
        operation_id=operation_id,
        project_id=request.project_id, book_id=request.book_id,
        operation_type="CANDIDATE_MASTER_V1", source_ref=report.artifact_ref,
        artifact_relative_path=candidate.artifact_ref,
        artifact_bytes=_json(candidate.to_dict()).encode("utf-8"),
        expected_versions={"candidate_version": request.candidate_version},
        provenance_refs=(manuscript.artifact_ref, report.artifact_ref),
        artifact_root=ArtifactRoot.PROJECT,
    )
    service = CrossStoreRecoveryService(repository)
    if repository.get_cross_store_operation(operation_id) is not None:
        outcome = service.recover(operation_id)
    else:
        outcome = service.execute(plan)
    if outcome.get("status") != RecoveryStatus.COMMITTED.value:
        raise CandidateIntegrityError("candidate artifact did not commit")
    path = (repository.context.project_root / candidate.artifact_ref).resolve()
    path.relative_to(repository.context.project_root.resolve())
    committed = CandidateMaster.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if committed.candidate_id != candidate_id:
        raise CandidateIntegrityError("candidate artifact identity changed")
    with repository.gap018_transaction() as connection:
        if repository.get_gap018_manuscript_head(connection=connection) != (
            manuscript.manuscript_id, manuscript.version, manuscript.manifest_hash,
        ):
            raise CandidateIntegrityError("candidate manuscript head changed before domain commit")
        repository.put_gap018_record("CANDIDATE", committed.candidate_id,
                                     committed.to_dict(), connection=connection)
    return load_candidate(repository, candidate_id)


def load_candidate(repository: ProjectRepository, candidate_id: str) -> CandidateMaster:
    if not candidate_id.startswith("CM-") or len(candidate_id) != 67:
        raise CandidateIntegrityError("invalid candidate ID")
    operation_id = "gap018-candidate-" + candidate_id[3:]
    operation = repository.get_cross_store_operation_readonly(operation_id)
    if operation is None or operation.get("operation_type") != "CANDIDATE_MASTER_V1" or operation.get("status") != "COMMITTED":
        raise CandidateIntegrityError("candidate commit missing")
    CrossStoreRecoveryService(repository).verify_committed_readonly(operation_id)
    path = (repository.context.project_root / _artifact_ref(candidate_id)).resolve()
    path.relative_to(repository.context.project_root.resolve())
    try:
        candidate = CandidateMaster.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateIntegrityError("candidate artifact missing or invalid") from exc
    if (candidate.project_id, candidate.book_id, candidate.candidate_id) != (
        repository.context.project_id, repository.context.book_id, candidate_id,
    ):
        raise CandidateIntegrityError("candidate owner mismatch")
    durable = repository.get_gap018_record("CANDIDATE", candidate_id)
    if durable is None or durable != candidate.to_dict():
        raise CandidateIntegrityError("candidate domain commit is missing")
    return candidate

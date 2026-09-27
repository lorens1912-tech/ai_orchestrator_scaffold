"""GAP-018 author decision and recoverable Source Master promotion."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.p20_core.book_qa import load_book_qa_report
from app.p20_core.candidate_master import load_candidate
from app.p20_core.cross_store_recovery import (
    ArtifactRoot, CrossStoreOperationPlan, CrossStoreRecoveryError,
    CrossStoreRecoveryService, RecoveryInterventionRequired, RecoveryStatus,
)
from app.p20_core.local_operator import OperatorError, OperatorIdentity
from app.p20_core.manuscript_version import load_manuscript, validate_current_manuscript_inputs
from app.p20_core.project_repository import ProjectRepository
from app.p20_core.source_master import (
    AuthorApproval, SourceHead, SourceMaster, SourcePromotionOperation,
    SourceContractError, _hash,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _source_head(repository: ProjectRepository) -> SourceHead:
    return SourceHead(*repository.get_gap018_source_head())


def _approval_from_dict(value: dict[str, Any]) -> AuthorApproval:
    try:
        result = AuthorApproval(**{**value, "expected_head": SourceHead(**value["expected_head"])})
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceContractError("AuthorApproval record is malformed") from exc
    result.validate()
    return result


def _set_promotion_status(
    repository: ProjectRepository, operation_id: str, status: str, *,
    source: SourceMaster | None = None, recovery_refs: tuple[str, ...] = (),
    connection=None,
) -> None:
    if connection is None:
        with repository.gap018_transaction() as transaction:
            _set_promotion_status(repository, operation_id, status, source=source,
                                  recovery_refs=recovery_refs, connection=transaction)
        return
    current = repository.get_gap018_record("PROMOTION", operation_id, connection=connection)
    if current is None:
        raise SourceContractError("Source promotion intent is missing")
    if status == "SOURCE_PENDING" and current["execution_status"] != "RECORDED":
        return
    if current["execution_status"] in {"SOURCE_COMMITTED", "SOURCE_CONFLICT"}:
        if current["execution_status"] == status:
            return
        raise SourceContractError("terminal Source promotion cannot change state")
    updated = {**current, "execution_status": status, "updated_at": _now()}
    if source is not None:
        updated.update(artifact_ref=source.artifact_ref,
                       artifact_hash=source.artifact_hash,
                       source_master_id=source.source_master_id)
    if recovery_refs:
        updated["recovery_refs"] = recovery_refs
    promotion = SourcePromotionOperation(**{
        **updated, "expected_head": SourceHead(**updated["expected_head"]),
        "recovery_refs": tuple(updated["recovery_refs"]),
    })
    promotion.validate()
    repository.update_gap018_promotion(operation_id, current, asdict(promotion),
                                       connection=connection)


def _candidate_basis(repository: ProjectRepository, candidate_id: str, *,
                     check_rejected: bool = True):
    if check_rejected and repository.gap018_candidate_rejected(candidate_id):
        raise OperatorError("CANDIDATE_REJECTED", 409)
    candidate = load_candidate(repository, candidate_id)
    report = load_book_qa_report(repository, candidate.qa_id)
    manuscript = load_manuscript(repository, candidate.manuscript_id)
    if (report.execution_status, report.validation_status, report.quality_decision) != (
        "COMPLETED", "VALID", "ACCEPT",
    ):
        raise OperatorError("BOOK_QA_NOT_ACCEPTED", 422)
    if ((report.report_hash, report.manuscript_id, report.content_hash) !=
            (candidate.report_hash, manuscript.manuscript_id, manuscript.content_hash)):
        raise OperatorError("CANDIDATE_BINDING_MISMATCH", 409)
    if validate_current_manuscript_inputs(repository, manuscript).status != "VALID":
        raise OperatorError("CANDIDATE_STALE", 409)
    if repository.get_gap018_manuscript_head() != (
        manuscript.manuscript_id, manuscript.version, manuscript.manifest_hash,
    ):
        raise OperatorError("CANDIDATE_STALE", 409)
    return candidate, report, manuscript


def issue_source_review(
    repository: ProjectRepository, candidate_id: str, identity: OperatorIdentity,
    *, ttl_seconds: int,
) -> dict[str, Any]:
    """Persist a one-use, exact-content challenge; this is not approval."""
    if not 1 <= ttl_seconds <= 900:
        raise OperatorError("INVALID_CHALLENGE_TTL", 503)
    candidate, report, manuscript = _candidate_basis(repository, candidate_id)
    expected = _source_head(repository)
    challenge_id = "challenge-" + secrets.token_hex(32)
    value = {
        "challenge_id": challenge_id,
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.artifact_hash,
        "manuscript_id": manuscript.manuscript_id,
        "manuscript_hash": manuscript.content_hash,
        "manifest_hash": manuscript.manifest_hash,
        "qa_id": report.qa_id,
        "qa_report_hash": report.report_hash,
        "book_bible_hash": manuscript.book_bible_hash,
        "canon_snapshot_hash": manuscript.canon_snapshot_hash,
        "expected_head": asdict(expected),
        "operator_id": identity.operator_id,
        "credential_id": identity.credential_id,
        "credential_version": identity.credential_version,
        "expires_at": time.time() + ttl_seconds,
    }
    with repository.gap018_transaction() as connection:
        if repository.gap018_candidate_rejected(candidate_id, connection=connection):
            raise OperatorError("CANDIDATE_REJECTED", 409)
        if SourceHead(*repository.get_gap018_source_head(connection=connection)) != expected:
            raise OperatorError("SOURCE_HEAD_CHANGED", 409)
        repository.put_gap018_record("CHALLENGE", challenge_id, value, connection=connection)
    return value


def _request_hash(request: dict[str, Any], identity: OperatorIdentity,
                  candidate_id: str) -> str:
    return _hash({"request": request, "identity": identity.to_dict(),
                  "candidate_id": candidate_id})


def record_source_decision(
    repository: ProjectRepository, candidate_id: str, request: dict[str, Any],
    identity: OperatorIdentity,
) -> dict[str, Any]:
    """Record author choice before attempting any Source materialization."""
    request_id = str(request.get("request_id") or "").strip()
    if not request_id or request.get("decision") not in {"APPROVE", "REJECT"}:
        raise OperatorError("INVALID_SOURCE_DECISION", 422)
    expected_hash = _request_hash(request, identity, candidate_id)
    receipt_id = "decision-" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    known = repository.get_gap018_record("REQUEST", receipt_id)
    if known is not None:
        if known["request_hash"] != expected_hash:
            raise OperatorError("SOURCE_REQUEST_ID_CONFLICT", 409)
        if known.get("operation_id") is None:
            return {"status": "APPROVAL_RECORDED", "decision": "REJECT",
                    "approval_id": known["approval_id"]}
        return recover_source_promotion(repository, known["operation_id"])
    candidate, report, manuscript = _candidate_basis(
        repository, candidate_id, check_rejected=False)
    with repository.gap018_transaction() as connection:
        replay = repository.get_gap018_record("REQUEST", receipt_id, connection=connection)
        if replay is not None:
            if replay["request_hash"] != expected_hash:
                raise OperatorError("SOURCE_REQUEST_ID_CONFLICT", 409)
            approval_id = replay["approval_id"]
            operation_id = replay.get("operation_id")
        else:
            challenge_id = str(request.get("challenge_id") or "")
            challenge = repository.get_gap018_record("CHALLENGE", challenge_id, connection=connection)
            if challenge is None or repository.get_gap018_record(
                "CHALLENGE_USE", challenge_id, connection=connection,
            ) is not None:
                raise OperatorError("INVALID_SOURCE_CHALLENGE", 409)
            if repository.gap018_candidate_rejected(candidate_id, connection=connection):
                raise OperatorError("CANDIDATE_REJECTED", 409)
            if challenge["expires_at"] <= time.time():
                raise OperatorError("SOURCE_CHALLENGE_EXPIRED", 409)
            if (challenge["operator_id"], challenge["credential_id"], challenge["credential_version"]) != (
                identity.operator_id, identity.credential_id, identity.credential_version,
            ):
                raise OperatorError("SOURCE_CHALLENGE_ACTOR_MISMATCH", 403)
            if challenge["candidate_id"] != candidate_id or any(
                request.get(key) != challenge[key]
                for key in ("candidate_hash", "manuscript_hash", "manifest_hash", "qa_report_hash")
            ) or request.get("expected_head") != challenge["expected_head"]:
                raise OperatorError("SOURCE_DECISION_BINDING_MISMATCH", 409)
            expected = SourceHead(**challenge["expected_head"])
            if SourceHead(*repository.get_gap018_source_head(connection=connection)) != expected:
                raise OperatorError("SOURCE_HEAD_CHANGED", 409)
            if repository.get_gap018_manuscript_head(connection=connection) != (
                manuscript.manuscript_id, manuscript.version, manuscript.manifest_hash,
            ):
                raise OperatorError("CANDIDATE_STALE", 409)
            # Candidate/QA/source snapshots were checked at review; reopen and
            # recheck them before writing the decision, without another model call.
            if (candidate.artifact_hash, report.report_hash, manuscript.content_hash,
                    manuscript.manifest_hash) != (
                    challenge["candidate_hash"], challenge["qa_report_hash"],
                    challenge["manuscript_hash"], challenge["manifest_hash"],
            ):
                raise OperatorError("SOURCE_DECISION_BINDING_MISMATCH", 409)
            approval_id = "AP-" + _hash({"request_hash": expected_hash, "challenge_id": challenge_id})
            approval = AuthorApproval(
                1, approval_id, repository.context.project_id, repository.context.book_id,
                candidate_id, candidate.artifact_hash, manuscript.content_hash,
                report.report_hash, request["decision"], identity.operator_id,
                identity.credential_id, str(identity.credential_version), challenge_id,
                "local-operator:" + challenge_id, request_id, expected, _now(), "",
            )
            approval = AuthorApproval(**{**approval.__dict__, "approval_hash": _hash(approval.unsigned_dict())})
            approval.validate()
            operation_id = None if approval.decision == "REJECT" else "gap018-source-" + approval.approval_hash
            repository.put_gap018_record("APPROVAL", approval_id, asdict(approval), connection=connection)
            repository.put_gap018_record("CHALLENGE_USE", challenge_id, {
                "request_id": request_id, "approval_id": approval_id,
            }, connection=connection)
            repository.put_gap018_record("REQUEST", receipt_id, {
                "request_hash": expected_hash, "approval_id": approval_id,
                "operation_id": operation_id, "decision": approval.decision,
            }, connection=connection)
            if operation_id is not None:
                promotion = SourcePromotionOperation(
                    1, operation_id, repository.context.project_id, repository.context.book_id,
                    request_id, expected_hash, approval_id, approval.approval_hash,
                    candidate_id, candidate.artifact_hash, expected, "RECORDED",
                    None, None, None, (), _now(),
                )
                promotion.validate()
                repository.put_gap018_record("PROMOTION", operation_id,
                                             asdict(promotion), connection=connection)
            else:
                state_id = "candidate-state-" + _hash({"candidate_id": candidate_id,
                                                         "approval_id": approval_id})
                repository.put_gap018_record("CANDIDATE_STATE", state_id, {
                    "candidate_id": candidate_id, "status": "REJECTED_BY_AUTHOR",
                    "approval_id": approval_id,
                }, connection=connection)
    if operation_id is None:
        return {"status": "APPROVAL_RECORDED", "decision": "REJECT", "approval_id": approval_id}
    return recover_source_promotion(repository, operation_id)


def _source_plan(repository: ProjectRepository, approval: AuthorApproval):
    candidate = load_candidate(repository, approval.candidate_id)
    manuscript = load_manuscript(repository, candidate.manuscript_id)
    if (candidate.artifact_hash, candidate.report_hash, manuscript.content_hash) != (
        approval.candidate_hash, approval.qa_report_hash, approval.manuscript_hash,
    ):
        raise OperatorError("SOURCE_APPROVAL_BINDING_MISMATCH", 409)
    expected = approval.expected_head
    version = 1 if expected.version is None else expected.version + 1
    source_id = "SM-" + _hash({
        "approval_hash": approval.approval_hash,
        "candidate_hash": candidate.artifact_hash,
        "expected_head": asdict(expected),
    })
    manuscript_path = (repository.context.project_root / manuscript.artifact_ref).resolve()
    manuscript_path.relative_to(repository.context.project_root.resolve())
    bundle = json.loads(manuscript_path.read_text(encoding="utf-8"))
    artifact = {
        "schema_version": 1, "source_master_id": source_id,
        "project_id": repository.context.project_id, "book_id": repository.context.book_id,
        "version": version, "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.artifact_hash, "manuscript_id": manuscript.manuscript_id,
        "manuscript_version": manuscript.version, "content_hash": manuscript.content_hash,
        "manifest_hash": manuscript.manifest_hash, "content": bundle["content"],
        "book_bible_snapshot_ref": manuscript.book_bible_snapshot_ref,
        "book_bible_hash": manuscript.book_bible_hash,
        "canon_snapshot_ref": manuscript.canon_snapshot_ref,
        "canon_snapshot_hash": manuscript.canon_snapshot_hash,
        "qa_id": candidate.qa_id, "qa_report_hash": candidate.report_hash,
        "approval_id": approval.approval_id, "approval_hash": approval.approval_hash,
    }
    raw = _json(artifact).encode("utf-8")
    artifact_hash = hashlib.sha256(raw).hexdigest()
    artifact_ref = f"source_masters/v1/{source_id}.json"
    source = SourceMaster(
        1, source_id, repository.context.project_id, repository.context.book_id,
        version, expected.source_master_id, candidate.candidate_id,
        candidate.artifact_hash, manuscript.manuscript_id, manuscript.version,
        artifact_ref, artifact_hash, approval.approval_id, approval.approval_hash,
        True, approval.decided_at, "SOURCE_COMMITTED", approval.decided_at,
    )
    source.validate(approval)
    plan = CrossStoreOperationPlan(
        operation_id="gap018-source-" + approval.approval_hash,
        project_id=repository.context.project_id, book_id=repository.context.book_id,
        operation_type="SOURCE_MASTER_V1", source_ref=candidate.artifact_ref,
        artifact_relative_path=artifact_ref, artifact_root=ArtifactRoot.PROJECT,
        artifact_bytes=raw, expected_versions={"source_version": version},
        provenance_refs=(candidate.artifact_ref, manuscript.artifact_ref,
                         approval.approval_id),
    )
    return source, plan, manuscript


def recover_source_promotion(repository: ProjectRepository, operation_id: str) -> dict[str, Any]:
    """Resume the original approval and expected-head CAS without new consent."""
    intent = repository.get_gap018_record("PROMOTION", operation_id)
    if intent is None:
        raise OperatorError("SOURCE_OPERATION_NOT_FOUND", 404)
    terminal = repository.get_gap018_record("PROMOTION_TERMINAL", operation_id)
    if terminal is not None:
        return dict(terminal)
    approval_value = repository.get_gap018_record("APPROVAL", intent["approval_id"])
    if approval_value is None:
        raise OperatorError("SOURCE_APPROVAL_MISSING", 409)
    approval = _approval_from_dict(approval_value)
    if approval.decision != "APPROVE" or approval.approval_hash != intent["approval_hash"]:
        raise OperatorError("SOURCE_APPROVAL_BINDING_MISMATCH", 409)
    source, plan, manuscript = _source_plan(repository, approval)
    if plan.operation_id != operation_id:
        raise OperatorError("SOURCE_OPERATION_BINDING_MISMATCH", 409)
    # A completed historical receipt is returned before revalidating mutable
    # current inputs; replay never changes a later current head.
    already = repository.get_gap018_record("SOURCE", source.source_master_id)
    if already is not None:
        raise SourceContractError("Source record exists without a terminal promotion receipt")
    _set_promotion_status(repository, operation_id, "SOURCE_PENDING",
                          recovery_refs=plan.provenance_refs)
    if (validate_current_manuscript_inputs(repository, manuscript).status != "VALID"
            or repository.get_gap018_manuscript_head() != (
                manuscript.manuscript_id, manuscript.version, manuscript.manifest_hash,
            )):
        _set_promotion_status(repository, operation_id, "NEEDS_INTERVENTION")
        return {"status": "NEEDS_INTERVENTION", "approval_id": approval.approval_id,
                "operation_id": operation_id, "reason": "SOURCE_INPUTS_STALE"}
    try:
        outcome = CrossStoreRecoveryService(repository).execute(plan)
    except RecoveryInterventionRequired:
        _set_promotion_status(repository, operation_id, "NEEDS_INTERVENTION")
        return {"status": "NEEDS_INTERVENTION", "approval_id": approval.approval_id,
                "operation_id": operation_id, "reason": "F004_NEEDS_INTERVENTION"}
    except CrossStoreRecoveryError:
        _set_promotion_status(repository, operation_id, "RECOVERY_REQUIRED")
        return {"status": "RECOVERY_REQUIRED", "approval_id": approval.approval_id,
                "operation_id": operation_id}
    if outcome.get("status") == RecoveryStatus.NEEDS_INTERVENTION.value:
        _set_promotion_status(repository, operation_id, "NEEDS_INTERVENTION")
        return {"status": "NEEDS_INTERVENTION", "approval_id": approval.approval_id,
                "operation_id": operation_id, "reason": "F004_NEEDS_INTERVENTION"}
    if outcome.get("status") != RecoveryStatus.COMMITTED.value:
        _set_promotion_status(repository, operation_id, "RECOVERY_REQUIRED")
        return {"status": "RECOVERY_REQUIRED", "approval_id": approval.approval_id,
                "operation_id": operation_id}
    expected_tuple = (approval.expected_head.source_master_id,
                      approval.expected_head.version, approval.expected_head.head_hash)
    with repository.gap018_transaction() as connection:
        prior = repository.get_gap018_record("PROMOTION_TERMINAL", operation_id,
                                            connection=connection)
        if prior is not None:
            return dict(prior)
        if repository.get_gap018_manuscript_head(connection=connection) != (
            manuscript.manuscript_id, manuscript.version, manuscript.manifest_hash,
        ):
            _set_promotion_status(repository, operation_id, "NEEDS_INTERVENTION",
                                  connection=connection)
            return {"status": "NEEDS_INTERVENTION", "approval_id": approval.approval_id,
                    "operation_id": operation_id, "reason": "SOURCE_INPUTS_STALE"}
        if repository.get_gap018_source_head(connection=connection) != expected_tuple:
            result = {"status": "SOURCE_CONFLICT", "approval_id": approval.approval_id,
                      "operation_id": operation_id}
            _set_promotion_status(repository, operation_id, "SOURCE_CONFLICT",
                                  source=source, connection=connection)
            repository.put_gap018_record("PROMOTION_TERMINAL", operation_id, result,
                                         connection=connection)
            return result
        repository.put_gap018_record("SOURCE", source.source_master_id,
                                     asdict(source), connection=connection)
        if not repository.cas_gap018_source_head(
            expected_tuple, (source.source_master_id, source.version, source.artifact_hash),
            connection=connection,
        ):
            raise SourceContractError("Source CAS changed inside serialized transaction")
        result = {"status": "SOURCE_COMMITTED", "source_master_id": source.source_master_id,
                  "artifact_hash": source.artifact_hash, "approval_id": approval.approval_id,
                  "operation_id": operation_id}
        _set_promotion_status(repository, operation_id, "SOURCE_COMMITTED",
                              source=source, connection=connection)
        repository.put_gap018_record("PROMOTION_TERMINAL", operation_id, result,
                                     connection=connection)
    return result


def load_source_master(repository: ProjectRepository, source_master_id: str) -> SourceMaster:
    value = repository.get_gap018_record("SOURCE", source_master_id)
    if value is None:
        raise OperatorError("SOURCE_MASTER_NOT_FOUND", 404)
    try:
        source = SourceMaster(**value)
    except (TypeError, ValueError) as exc:
        raise SourceContractError("SourceMaster record is malformed") from exc
    approval_value = repository.get_gap018_record("APPROVAL", source.approval_id)
    if approval_value is None:
        raise SourceContractError("Source approval is missing")
    approval = _approval_from_dict(approval_value)
    source.validate(approval)
    operation_id = "gap018-source-" + approval.approval_hash
    terminal = repository.get_gap018_record("PROMOTION_TERMINAL", operation_id)
    promotion = repository.get_gap018_record("PROMOTION", operation_id)
    if (terminal is None or terminal.get("status") != "SOURCE_COMMITTED"
            or terminal.get("source_master_id") != source.source_master_id
            or terminal.get("artifact_hash") != source.artifact_hash
            or promotion is None or promotion.get("execution_status") != "SOURCE_COMMITTED"
            or promotion.get("source_master_id") != source.source_master_id
            or promotion.get("artifact_hash") != source.artifact_hash):
        raise SourceContractError("Source terminal receipt is missing or inconsistent")
    operation = repository.get_cross_store_operation_readonly(operation_id)
    if operation is None or operation.get("operation_type") != "SOURCE_MASTER_V1" or operation.get("status") != "COMMITTED":
        raise SourceContractError("Source F-004 commit is missing")
    CrossStoreRecoveryService(repository).verify_committed_readonly(operation_id)
    path = (repository.context.project_root / source.artifact_ref).resolve()
    path.relative_to(repository.context.project_root.resolve())
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source.artifact_hash:
        raise SourceContractError("Source artifact hash mismatch")
    try:
        artifact = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceContractError("Source artifact is malformed") from exc
    for key in ("source_master_id", "project_id", "book_id", "version",
                "candidate_id", "candidate_hash", "manuscript_id",
                "manuscript_version", "approval_id", "approval_hash"):
        if artifact.get(key) != getattr(source, key):
            raise SourceContractError("Source artifact and durable record differ")
    return source


def current_source_master(repository: ProjectRepository) -> SourceMaster | None:
    source_id, _version, head_hash = repository.get_gap018_source_head()
    if source_id is None:
        return None
    source = load_source_master(repository, source_id)
    if source.artifact_hash != head_hash:
        raise SourceContractError("current Source head hash mismatch")
    return source

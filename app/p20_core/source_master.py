"""GAP-018 approval, promotion and source-master value contracts.

Approval is an author decision; a SourceMaster exists only after a separate
committed artifact and a successful conditional current-head update.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


class SourceContractError(ValueError):
    pass


def _hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _digest(value: str, field: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise SourceContractError(f"{field} must be a SHA-256 hex digest")


@dataclass(frozen=True)
class SourceHead:
    source_master_id: str | None
    version: int | None
    head_hash: str | None

    def validate(self) -> None:
        if (self.source_master_id, self.version, self.head_hash) == (None, None, None):
            return
        if (not self.source_master_id or type(self.version) is not int or self.version < 1):
            raise SourceContractError("expected Source head must be a complete triple")
        _digest(self.head_hash, "head_hash")


@dataclass(frozen=True)
class AuthorApproval:
    schema_version: int
    approval_id: str
    project_id: str
    book_id: str
    candidate_id: str
    candidate_hash: str
    manuscript_hash: str
    qa_report_hash: str
    decision: str
    operator_id: str
    credential_id: str
    credential_version: str
    challenge_id: str
    authorization_ref: str
    request_id: str
    expected_head: SourceHead
    decided_at: str
    approval_hash: str

    def unsigned_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("approval_hash")
        return result

    def validate(self) -> None:
        if self.schema_version != 1 or self.decision not in {"APPROVE", "REJECT"}:
            raise SourceContractError("invalid approval schema or decision")
        for name in ("approval_id", "project_id", "book_id", "candidate_id", "operator_id",
                     "credential_id", "credential_version", "challenge_id",
                     "authorization_ref", "request_id", "decided_at"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise SourceContractError(f"{name} is required")
        for name in ("candidate_hash", "manuscript_hash", "qa_report_hash"):
            _digest(getattr(self, name), name)
        self.expected_head.validate()
        if self.approval_hash != _hash(self.unsigned_dict()):
            raise SourceContractError("approval hash mismatch")


@dataclass(frozen=True)
class SourcePromotionOperation:
    schema_version: int
    operation_id: str
    project_id: str
    book_id: str
    request_id: str
    request_hash: str
    approval_id: str
    approval_hash: str
    candidate_id: str
    candidate_hash: str
    expected_head: SourceHead
    execution_status: str
    artifact_ref: str | None
    artifact_hash: str | None
    source_master_id: str | None
    recovery_refs: tuple[str, ...]
    updated_at: str

    def validate(self) -> None:
        if self.schema_version != 1 or self.execution_status not in {
            "RECORDED", "SOURCE_PENDING", "SOURCE_COMMITTED", "RECOVERY_REQUIRED",
            "SOURCE_CONFLICT", "NEEDS_INTERVENTION",
        }:
            raise SourceContractError("invalid promotion schema or state")
        self.expected_head.validate()
        for name in ("request_hash", "approval_hash", "candidate_hash"):
            _digest(getattr(self, name), name)
        if self.execution_status == "SOURCE_COMMITTED":
            if not self.artifact_ref or not self.source_master_id:
                raise SourceContractError("committed promotion lacks Source artifact")
            _digest(self.artifact_hash, "artifact_hash")


@dataclass(frozen=True)
class SourceMaster:
    schema_version: int
    source_master_id: str
    project_id: str
    book_id: str
    version: int
    parent_source_master_id: str | None
    candidate_id: str
    candidate_hash: str
    manuscript_id: str
    manuscript_version: int
    artifact_ref: str
    artifact_hash: str
    approval_id: str
    approval_hash: str
    approved_by_user: bool
    approved_at: str
    status: str
    created_at: str

    def validate(self, approval: AuthorApproval) -> None:
        approval.validate()
        if (self.schema_version != 1 or self.status != "SOURCE_COMMITTED"
                or self.approved_by_user is not True or approval.decision != "APPROVE"):
            raise SourceContractError("Source requires committed author approval")
        if (self.project_id, self.book_id, self.approval_id, self.approval_hash,
                self.candidate_id, self.candidate_hash) != (
                approval.project_id, approval.book_id, approval.approval_id,
                approval.approval_hash, approval.candidate_id, approval.candidate_hash):
            raise SourceContractError("Source and approval binding mismatch")
        if type(self.version) is not int or self.version < 1:
            raise SourceContractError("Source version is invalid")
        expected_version = (1 if approval.expected_head.version is None
                            else approval.expected_head.version + 1)
        expected_id = "SM-" + _hash({
            "approval_hash": approval.approval_hash,
            "candidate_hash": approval.candidate_hash,
            "expected_head": asdict(approval.expected_head),
        })
        if (self.version != expected_version
                or self.parent_source_master_id != approval.expected_head.source_master_id
                or self.source_master_id != expected_id
                or self.artifact_ref != f"source_masters/v1/{expected_id}.json"):
            raise SourceContractError("Source lineage or artifact identity mismatch")
        _digest(self.artifact_hash, "artifact_hash")

"""GAP-018 book-level QA contract, reducer, and immutable report storage."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from app.p20_core.cross_store_recovery import (
    ArtifactRoot, CrossStoreOperationPlan, CrossStoreRecoveryService, RecoveryStatus,
)
from app.p20_core.manuscript_version import ManuscriptVersion, load_manuscript
from app.p20_core.model_provenance import fingerprint
from app.p20_core.project_repository import ProjectRepository


POLICY_VERSION = "GAP018_BOOK_QA_POLICY_V1"
CRITERIA_VERSION = "GAP018_BOOK_QA_CRITERIA_V1"
MANDATORY_LEVELS = ("SCENE", "CHAPTER", "SEQUENCE", "ACT", "BOOK")
MANDATORY_CRITERIA = (
    "STRUCTURE", "CAUSALITY", "PACING", "TENSION", "CHARACTER_ARCS",
    "CONTINUITY", "CANON", "CHARACTER_KNOWLEDGE", "READER_KNOWLEDGE",
    "PLOTS", "SETUP_PAYOFF", "STYLE", "VOICE", "REDUNDANCY",
    "RESEARCH_FACTS", "TERMINOLOGY", "FINAL_QUALITY",
)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


POLICY_HASH = _hash({"version": POLICY_VERSION, "priority": ("BLOCKED", "REJECT", "REVISE", "ACCEPT"),
                     "blocking_severity": ("BLOCKER", "CRITICAL")})
CRITERIA_HASH = _hash({"version": CRITERIA_VERSION, "levels": MANDATORY_LEVELS,
                       "criteria": MANDATORY_CRITERIA})


class BookQAIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class Coverage:
    level: str
    criterion: str
    source_ref: str
    source_hash: str
    context_ref: str
    context_hash: str
    result: str


@dataclass(frozen=True)
class Finding:
    finding_id: str
    level: str
    criterion: str
    source_ref: str
    source_hash: str
    location: str
    severity: str
    evidence: str
    classification: str | None
    must_fix: bool
    resolved: bool
    context_ref: str | None = None
    context_hash: str | None = None


@dataclass(frozen=True)
class BookQARequest:
    project_id: str
    book_id: str
    manuscript_id: str
    content_hash: str
    manifest_hash: str
    request_id: str
    coverage: tuple[Coverage, ...]
    findings: tuple[Finding, ...]
    model_invocation_refs: tuple[str, ...]
    reevaluation_of: str | None = None
    policy_version: str = POLICY_VERSION
    policy_hash: str = POLICY_HASH
    criteria_version: str = CRITERIA_VERSION
    criteria_hash: str = CRITERIA_HASH
    preflight_blockers: tuple[str, ...] = ()
    hierarchical: bool = False


@dataclass(frozen=True)
class BookQAReport:
    schema_version: int
    qa_id: str
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
    policy_version: str
    policy_hash: str
    criteria_version: str
    criteria_hash: str
    coverage: tuple[Coverage, ...]
    findings: tuple[Finding, ...]
    model_invocation_refs: tuple[str, ...]
    execution_status: str
    validation_status: str
    quality_decision: str | None
    blockers: tuple[str, ...]
    reevaluation_of: str | None
    request_id: str
    created_at: str
    completed_at: str | None
    artifact_ref: str
    report_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BookQAReport:
        try:
            payload = dict(value)
            payload["coverage"] = tuple(Coverage(**item) for item in payload["coverage"])
            payload["findings"] = tuple(Finding(**item) for item in payload["findings"])
            payload["model_invocation_refs"] = tuple(payload["model_invocation_refs"])
            payload["blockers"] = tuple(payload["blockers"])
            result = cls(**payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise BookQAIntegrityError("invalid Book QA report") from exc
        if result.schema_version != 1 or result.report_hash != _report_hash(result):
            raise BookQAIntegrityError("Book QA report hash or schema mismatch")
        if result.qa_id != "BQA-" + result.report_hash:
            raise BookQAIntegrityError("Book QA identity mismatch")
        if result.artifact_ref != _artifact_ref(result.qa_id):
            raise BookQAIntegrityError("Book QA artifact reference mismatch")
        return result


def _report_hash(report: BookQAReport) -> str:
    payload = report.to_dict()
    for key in ("qa_id", "created_at", "completed_at", "artifact_ref", "report_hash"):
        payload.pop(key)
    return _hash(payload)


def _artifact_ref(qa_id: str) -> str:
    return f"book_qa/v1/{qa_id}.json"


def reduce_book_qa(request: BookQARequest) -> tuple[str, str, str | None, tuple[str, ...]]:
    """Reduce validated evidence; missing evidence is BLOCKED, never ACCEPT."""
    blockers: list[str] = list(request.preflight_blockers)
    if any(code not in {"CONTEXT_OVERFLOW", "CONTEXT_MISSING"}
           for code in blockers):
        blockers.append("PREFLIGHT_BLOCKER_INVALID")
    if (request.policy_version, request.policy_hash) != (POLICY_VERSION, POLICY_HASH):
        blockers.append("POLICY_UNKNOWN")
    if (request.criteria_version, request.criteria_hash) != (CRITERIA_VERSION, CRITERIA_HASH):
        blockers.append("CRITERIA_UNKNOWN")
    if not request.model_invocation_refs or any(not ref for ref in request.model_invocation_refs):
        blockers.append("MODEL_PROVENANCE_MISSING")
    expected = {(level, criterion) for level in MANDATORY_LEVELS for criterion in MANDATORY_CRITERIA}
    observed: set[tuple[str, str]] = set()
    for item in request.coverage:
        pair = (item.level, item.criterion)
        if pair not in expected or pair in observed:
            blockers.append("COVERAGE_INVALID")
        observed.add(pair)
        if (not item.source_ref or len(item.source_hash) != 64 or not item.context_ref
                or len(item.context_hash) != 64 or item.result not in {"PASS", "ISSUE"}):
            blockers.append("COVERAGE_EVIDENCE_MISSING")
    if observed != expected:
        blockers.append("COVERAGE_INCOMPLETE")
    finding_pairs: set[tuple[str, str]] = set()
    for finding in request.findings:
        finding_pairs.add((finding.level, finding.criterion))
        if ((finding.level, finding.criterion) not in observed or not finding.finding_id
                or not finding.source_ref or len(finding.source_hash) != 64
                or not finding.location or not finding.evidence
                or not finding.context_ref or not finding.context_hash
                or len(finding.context_hash) != 64
                or finding.severity not in {"BLOCKER", "CRITICAL", "MAJOR", "MINOR"}):
            blockers.append("FINDING_EVIDENCE_MISSING")
        if (finding.must_fix or finding.severity in {"BLOCKER", "CRITICAL"}) and finding.classification not in {
            "repairable", "fundamental",
        }:
            blockers.append("FINDING_CLASSIFICATION_MISSING")
    if any(item.result == "ISSUE" and (item.level, item.criterion) not in finding_pairs
           for item in request.coverage):
        blockers.append("ISSUE_WITHOUT_FINDING")
    if blockers:
        return "BLOCKED", "INVALID", None, tuple(sorted(set(blockers)))
    unresolved = tuple(item for item in request.findings if not item.resolved)
    if any(item.classification == "fundamental" for item in unresolved):
        decision = "REJECT"
    elif any(item.must_fix or item.severity in {"BLOCKER", "CRITICAL"} for item in unresolved):
        decision = "REVISE"
    else:
        decision = "ACCEPT"
    return "COMPLETED", "VALID", decision, ()


def create_book_qa_report(repository: ProjectRepository, request: BookQARequest) -> BookQAReport:
    if (request.project_id, request.book_id) != (repository.context.project_id, repository.context.book_id):
        raise BookQAIntegrityError("project or book scope mismatch")
    manuscript = load_manuscript(repository, request.manuscript_id)
    if (request.content_hash, request.manifest_hash) != (manuscript.content_hash, manuscript.manifest_hash):
        raise BookQAIntegrityError("Book QA manuscript hash mismatch")
    if not request.request_id.strip():
        raise BookQAIntegrityError("request ID is required")
    from app.p20_core.gap018_receipts import reserve_request
    reserve_request(repository, "BOOK_QA", request.request_id, asdict(request))
    execution, validation, decision, blockers = reduce_book_qa(request)
    allowed_sources = {manuscript.artifact_ref: manuscript.content_hash}
    allowed_sources.update({item.artifact_ref: item.artifact_sha256 for item in manuscript.chapter_version_refs})
    allowed_sources[manuscript.book_bible_snapshot_ref] = manuscript.book_bible_hash
    allowed_sources[manuscript.canon_snapshot_ref] = manuscript.canon_snapshot_hash
    context_content_hashes = {manuscript.artifact_ref: manuscript.content_hash}
    context_content_hashes.update({item.artifact_ref: item.text_sha256
                                   for item in manuscript.chapter_version_refs})
    context_content_hashes[manuscript.book_bible_snapshot_ref] = manuscript.book_bible_hash
    context_content_hashes[manuscript.canon_snapshot_ref] = manuscript.canon_snapshot_hash
    if any(allowed_sources.get(item.source_ref) != item.source_hash for item in request.coverage):
        blockers = tuple(sorted(set((*blockers, "COVERAGE_SOURCE_MISMATCH"))))
    if any(allowed_sources.get(item.source_ref) != item.source_hash for item in request.findings):
        blockers = tuple(sorted(set((*blockers, "FINDING_SOURCE_MISMATCH"))))
    if not request.hierarchical:
        for item in request.coverage:
            package = repository.get_context_package(item.context_ref) if item.context_ref else None
            if (package is None or package.project_id != request.project_id
                    or package.book_id != request.book_id or package.context_hash != item.context_hash
                    or not package.included_items
                    or not any(context_item.source_ref == item.source_ref
                               and context_item.content_hash == context_content_hashes.get(item.source_ref)
                               for context_item in package.included_items)):
                blockers = tuple(sorted(set((*blockers, "CONTEXT_PROVENANCE_MISSING"))))
        for finding in request.findings:
            package = repository.get_context_package(finding.context_ref) if finding.context_ref else None
            if (package is None or package.project_id != request.project_id
                    or package.book_id != request.book_id or package.context_hash != finding.context_hash
                    or not any(context_item.source_ref == finding.source_ref
                               and context_item.content_hash == context_content_hashes.get(finding.source_ref)
                               for context_item in package.included_items)):
                blockers = tuple(sorted(set((*blockers, "FINDING_CONTEXT_MISMATCH"))))
    observed_contexts = {(item.context_ref, item.context_hash) for item in request.coverage}
    model_coverage: list[dict[str, Any]] = []
    model_findings: list[dict[str, Any]] = []
    for operation_id in request.model_invocation_refs:
        raw = repository.get_metadata_readonly("model_invocation.v1:" + operation_id)
        invocation = json.loads(raw) if raw else None
        if (not isinstance(invocation, dict)
                or invocation.get("project_id") != request.project_id
                or invocation.get("book_id") != request.book_id
                or (not request.hierarchical and
                    (invocation.get("context_package_id"), invocation.get("context_hash")) not in observed_contexts)
                or invocation.get("status") != "TRANSPORT_COMPLETED"
                or invocation.get("validation") != "VALID"
                or not isinstance(invocation.get("result"), dict)
                or invocation.get("result_hash") != fingerprint(invocation.get("result"))
                or (not invocation.get("requested", {}).get("model")
                    and not invocation.get("resolved", {}).get("source")
                    and not invocation.get("resolved", {}).get("decision", {}).get("source"))
                or not invocation.get("effective_model")
                or not invocation.get("attempts")
                or invocation["attempts"][-1].get("status") != "RECEIVED"):
            blockers = tuple(sorted(set((*blockers, "MODEL_PROVENANCE_INVALID"))))
            continue
        try:
            output = json.loads(invocation["result"]["text"])
            if (not isinstance(output, dict)
                    or output.get("manuscript_id") != manuscript.manuscript_id
                    or output.get("content_hash") != manuscript.content_hash
                    or output.get("manifest_hash") != manuscript.manifest_hash
                    or not isinstance(output.get("coverage"), list)
                    or not isinstance(output.get("findings"), list)):
                raise ValueError("model output is not bound to manuscript")
            model_coverage.extend(output["coverage"])
            model_findings.extend(output["findings"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            blockers = tuple(sorted(set((*blockers, "MODEL_OUTPUT_INVALID"))))
    if request.hierarchical:
        from app.p20_core.book_qa_hierarchy import verify_hierarchical_evidence
        try:
            verified_coverage, verified_findings = verify_hierarchical_evidence(
                repository, manuscript, request.model_invocation_refs,
            )
        except (ValueError, TypeError, KeyError, OSError):
            blockers = tuple(sorted(set((*blockers, "HIERARCHICAL_EVIDENCE_INVALID"))))
        else:
            if verified_coverage != request.coverage or verified_findings != request.findings:
                blockers = tuple(sorted(set((*blockers, "HIERARCHICAL_AGGREGATION_MISMATCH"))))
    else:
        if _json(model_coverage) != _json([asdict(item) for item in request.coverage]):
            blockers = tuple(sorted(set((*blockers, "COVERAGE_MODEL_MISMATCH"))))
        if _json(model_findings) != _json([asdict(item) for item in request.findings]):
            blockers = tuple(sorted(set((*blockers, "FINDINGS_MODEL_MISMATCH"))))
    if blockers:
        execution, validation, decision = "BLOCKED", "INVALID", None
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    provisional = BookQAReport(
        1, "", request.project_id, request.book_id, manuscript.manuscript_id,
        manuscript.version, manuscript.content_hash, manuscript.manifest_hash,
        manuscript.book_bible_snapshot_ref, manuscript.book_bible_hash,
        manuscript.canon_snapshot_ref, manuscript.canon_snapshot_hash,
        request.policy_version, request.policy_hash, request.criteria_version,
        request.criteria_hash, request.coverage, request.findings,
        request.model_invocation_refs, execution, validation, decision, blockers,
        request.reevaluation_of, request.request_id, created_at, created_at,
        "", "",
    )
    report_hash = _report_hash(provisional)
    qa_id = "BQA-" + report_hash
    report = BookQAReport(**{**provisional.__dict__, "qa_id": qa_id,
                             "artifact_ref": _artifact_ref(qa_id), "report_hash": report_hash})
    operation_id = "gap018-book-qa-" + report_hash
    if repository.get_cross_store_operation(operation_id) is not None:
        return load_book_qa_report(repository, qa_id)
    plan = CrossStoreOperationPlan(
        operation_id=operation_id,
        project_id=request.project_id, book_id=request.book_id,
        operation_type="BOOK_QA_REPORT_V1", source_ref=manuscript.artifact_ref,
        artifact_relative_path=report.artifact_ref,
        artifact_bytes=_json(report.to_dict()).encode("utf-8"),
        expected_versions={"manuscript_version": manuscript.version},
        provenance_refs=(manuscript.artifact_ref, *request.model_invocation_refs),
        artifact_root=ArtifactRoot.PROJECT,
    )
    outcome = CrossStoreRecoveryService(repository).execute(plan)
    if outcome.get("status") != RecoveryStatus.COMMITTED.value:
        raise BookQAIntegrityError("Book QA report did not commit")
    return load_book_qa_report(repository, qa_id)


def load_book_qa_report(repository: ProjectRepository, qa_id: str) -> BookQAReport:
    if not qa_id.startswith("BQA-") or len(qa_id) != 68:
        raise BookQAIntegrityError("invalid Book QA ID")
    operation_id = "gap018-book-qa-" + qa_id[4:]
    operation = repository.get_cross_store_operation_readonly(operation_id)
    if operation is None or operation.get("operation_type") != "BOOK_QA_REPORT_V1" or operation.get("status") != "COMMITTED":
        raise BookQAIntegrityError("Book QA commit missing")
    CrossStoreRecoveryService(repository).verify_committed_readonly(operation_id)
    path = (repository.context.project_root / _artifact_ref(qa_id)).resolve()
    path.relative_to(repository.context.project_root.resolve())
    try:
        report = BookQAReport.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise BookQAIntegrityError("Book QA artifact missing or invalid") from exc
    if (report.project_id, report.book_id, report.qa_id) != (repository.context.project_id, repository.context.book_id, qa_id):
        raise BookQAIntegrityError("Book QA owner mismatch")
    return report

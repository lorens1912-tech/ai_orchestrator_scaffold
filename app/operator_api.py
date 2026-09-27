"""Dedicated operator surface. Authentication is not domain mutation approval."""
from __future__ import annotations

import ipaddress
import os
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.p20_core.local_operator import OperatorError, authenticate, project_for_operator
from app.p20_core.project_repository import (
    SeriesAccessContext, SeriesAccessError,
    SeriesRepository, StorageResolver, SystemRepository,
    SystemStorageError, SeriesStorageError, SchemaMigrationError,
)
from app.p20_core.memory_ledger import (
    MemoryLedgerError, MemoryLedgerMigrationRequired, MemoryLedgerNotActive,
)
from app.p20_core.canon_service import operator_proposal_review, record_operator_decision, commit_canonical_proposal
from app.p20_core.adaptive_style import AdaptiveStyleError, StyleRepository
from app.p20_core.project_repository import ProjectStorageError
from app.p20_core.style_library_catalog import seed_curated_style_library

router = APIRouter(prefix="/operator", tags=["local operator"])
bearer = HTTPBearer(auto_error=False)


def challenge_ttl_seconds() -> int:
    try:
        value = int(os.environ.get("AGENTPRO_OPERATOR_CHALLENGE_TTL_SECONDS", "300"))
    except ValueError:
        raise OperatorError("INVALID_OPERATOR_SECURITY_CONFIG", 503) from None
    if not 1 <= value <= 900:
        raise OperatorError("INVALID_OPERATOR_SECURITY_CONFIG", 503)
    return value


def authenticated_operator(request: Request,
                           credential: HTTPAuthorizationCredentials | None = Depends(bearer)):
    try:
        if request.client is None or not ipaddress.ip_address(request.client.host).is_loopback:
            raise OperatorError("OPERATOR_REQUIRES_LOOPBACK")
        # Prevent browser DNS rebinding as well as non-loopback clients. No CORS.
        hostname = request.url.hostname
        if hostname != "localhost" and not ipaddress.ip_address(hostname or "").is_loopback:
            raise OperatorError("OPERATOR_REQUIRES_LOOPBACK")
    except ValueError:
        raise HTTPException(403, "OPERATOR_REQUIRES_LOOPBACK") from None
    try:
        repository = SystemRepository(StorageResolver().resolve_system())
        if request.method == "GET" and not repository.db_path.is_file():
            raise OperatorError("OPERATOR_NOT_CONFIGURED", 503)
        with repository.local_operator_transaction(read_only=request.method == "GET") as (state, registry):
            authenticate(state, None if credential is None else credential.credentials)
            return credential.credentials
    except OperatorError as exc:
        raise HTTPException(exc.status, exc.code) from None
    except SeriesAccessError:
        raise HTTPException(403, "SERIES_ACCESS_DENIED") from None
    except (SystemStorageError, SchemaMigrationError):
        raise HTTPException(409, "OPERATOR_STORAGE_CONFLICT") from None


def _with_operator(token, operation, *, read_only=False):
    try:
        # Revalidate and keep credential/registry stable through project commit.
        # Entire transaction runs in this endpoint's worker thread.
        repository = SystemRepository(StorageResolver().resolve_system())
        with repository.local_operator_transaction(read_only=read_only) as (state, registry):
            return operation(authenticate(state, token), registry)
    except OperatorError as exc:
        raise HTTPException(exc.status, exc.code) from None
    except SeriesAccessError:
        raise HTTPException(403, "SERIES_ACCESS_DENIED") from None
    except MemoryLedgerMigrationRequired:
        raise HTTPException(409, "MIGRATION_REQUIRED") from None
    except MemoryLedgerNotActive:
        raise HTTPException(409, "LEDGER_NOT_ACTIVE") from None
    except (SystemStorageError, ProjectStorageError, SeriesStorageError, SchemaMigrationError, MemoryLedgerError):
        raise HTTPException(409, "OPERATOR_STORAGE_CONFLICT") from None


def _project(registry, project_id):
    book_id = registry.get(project_id)
    if book_id is None:
        raise OperatorError("OPERATOR_PROJECT_ACCESS_DENIED")
    return project_for_operator(registry, project_id, book_id)


def _proposal_target(registry, project_id: str, proposal_id: str, *, read_only=False):
    """Resolve an authorized proposal to its sole owning local repository."""
    project = _project(registry, project_id)
    matches = []
    read_metadata = project.get_metadata_readonly if read_only else project.get_metadata
    if read_metadata("canonical_proposal.v1:" + proposal_id) is not None:
        return project, None
    book_id = registry[project_id]
    system_reader = SystemRepository(StorageResolver().resolve_system())
    for series_id, membership_book_id in system_reader.discover_series_memberships(project_id):
        if membership_book_id != book_id:
            continue
        access = SeriesAccessContext.bind(project_id, series_id)
        series = SeriesRepository(StorageResolver().resolve_series(series_id))
        if series.get_metadata_readonly(
            access, "canonical_proposal.v1:" + proposal_id,
        ) is not None:
            series.require_registered_member(access, book_id=book_id, read_only=read_only)
            matches.append((series, access))
    if not matches:
        raise OperatorError("PROPOSAL_NOT_FOUND", 404)
    if len(matches) != 1:
        raise OperatorError("PROPOSAL_SCOPE_AMBIGUOUS", 409)
    return matches[0]


@router.get("/identity")
def identity(principal=Depends(authenticated_operator)):
    return _with_operator(principal, lambda operator, registry: operator.to_dict(), read_only=True)


def _style_library_profile_response(profile):
    value = profile.to_dict()
    # The raw curation excerpt remains persisted provenance. Operator listing
    # exposes its stable hash and structured metadata without repeating the
    # full embedded source document in every response.
    metadata = dict(value["source_metadata"])
    metadata.pop("raw_profile_markdown", None)
    value["source_metadata"] = metadata
    return value


def _style_library_operation(principal, operation):
    try:
        return _with_operator(principal, operation)
    except AdaptiveStyleError as exc:
        raise HTTPException(422, str(exc)) from None
    except ProjectStorageError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/projects/{project_id}/style-library/seed")
def seed_style_library(project_id: str, principal=Depends(authenticated_operator)):
    def operation(_operator, registry):
        return seed_curated_style_library(StyleRepository(_project(registry, project_id)))
    return _style_library_operation(principal, operation)


@router.get("/projects/{project_id}/style-library")
def list_style_library(project_id: str, principal=Depends(authenticated_operator)):
    def operation(_operator, registry):
        repository = StyleRepository(_project(registry, project_id))
        return {
            "profiles": [
                _style_library_profile_response(profile)
                for profile in repository.list_library_profiles()
            ]
        }
    return _style_library_operation(principal, operation)


@router.get("/projects/{project_id}/style-library/by-name/{display_name}")
def get_style_library_profile_by_name(
    project_id: str,
    display_name: str,
    principal=Depends(authenticated_operator),
):
    def operation(_operator, registry):
        repository = StyleRepository(_project(registry, project_id))
        profile = repository.get_library_profile_by_name(display_name)
        if profile is None:
            raise HTTPException(404, "STYLE_LIBRARY_PROFILE_NOT_FOUND")
        return _style_library_profile_response(profile)
    return _style_library_operation(principal, operation)


@router.get("/projects/{project_id}/style-library/by-id/{profile_id}")
def get_style_library_profile_by_id(
    project_id: str,
    profile_id: str,
    principal=Depends(authenticated_operator),
):
    def operation(_operator, registry):
        repository = StyleRepository(_project(registry, project_id))
        profile = repository.get_library_profile(profile_id)
        if profile is None:
            raise HTTPException(404, "STYLE_LIBRARY_PROFILE_NOT_FOUND")
        return _style_library_profile_response(profile)
    return _style_library_operation(principal, operation)


@router.get("/projects/{project_id}/proposals/{proposal_id}")
def read_proposal(project_id: str, proposal_id: str, principal=Depends(authenticated_operator)):
    def operation(operator, registry):
        repository, access = _proposal_target(registry, project_id, proposal_id, read_only=True)
        result = operator_proposal_review(
            repository, proposal_id, operator, ttl_seconds=challenge_ttl_seconds(),
            issue_challenge=False, series_access=access,
        )
        if access is None:
            events, _cursor = repository.list_memory_events(
                operation=("CANONICAL_PROPOSAL", proposal_id), limit=200,
            )
        else:
            events, _cursor = repository.list_memory_events(
                access, operation=("CANONICAL_PROPOSAL", proposal_id), limit=200,
            )
        from app.p20_core.memory_ledger import MEMORY_LEDGER_COVERAGE
        result["memory_event_refs"] = [
            {"memory_event_id": event.memory_event_id, "event_type": event.event_type,
             "content_hash": event.content_hash}
            for event in events
        ]
        result["coverage"] = MEMORY_LEDGER_COVERAGE
        return result
    return _with_operator(principal, operation, read_only=True)


@router.post("/projects/{project_id}/proposals/{proposal_id}/review")
def prepare_decision(project_id: str, proposal_id: str, principal=Depends(authenticated_operator)):
    def operation(operator, registry):
        repository, access = _proposal_target(registry, project_id, proposal_id)
        return operator_proposal_review(
            repository, proposal_id, operator, ttl_seconds=challenge_ttl_seconds(),
            series_access=access,
        )
    return _with_operator(principal, operation)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    proposal_hash: str
    scope_type: Literal["PROJECT", "SERIES"]
    scope_id: str
    challenge_id: str
    decision: Literal["APPROVE", "REJECT"]


@router.post("/projects/{project_id}/proposals/{proposal_id}/decision")
def decide(project_id: str, proposal_id: str, body: DecisionRequest,
           principal=Depends(authenticated_operator)):
    def operation(operator, registry):
        repository, access = _proposal_target(registry, project_id, proposal_id)
        return record_operator_decision(
            repository, proposal_id, operator, body.model_dump(), series_access=access,
        )
    return _with_operator(principal, operation)


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    proposal_hash: str


@router.post("/projects/{project_id}/proposals/{proposal_id}/commit")
def commit(project_id: str, proposal_id: str, body: CommitRequest,
           principal=Depends(authenticated_operator)):
    def operation(operator, registry):
        repository, access = _proposal_target(registry, project_id, proposal_id)
        return commit_canonical_proposal(
            repository, proposal_id, expected_hash=body.proposal_hash,
            identity=operator, series_access=access,
        )
    return _with_operator(principal, operation)


class ResearchQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str
    research_id: str
    question: str
    purpose: str
    related_entity_refs: list[str] = Field(default_factory=list)


class EvaluationRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    evaluation_id: str
    run_id: str
    step_id: str
    expected_attempt_token: str | None = None


@router.post("/projects/{project_id}/evaluations/{operation_id}/recover")
def recover_quality_evaluation(
    project_id: str,
    operation_id: str,
    body: EvaluationRecoveryRequest,
    principal=Depends(authenticated_operator),
):
    from app.p20_core.evaluation import (
        EvaluationConflict,
        EvaluationError,
        EvaluationNeedsIntervention,
        recover_evaluation_by_identity,
    )

    def operation(operator, registry):
        result = recover_evaluation_by_identity(
            _project(registry, project_id),
            operation_id=operation_id,
            evaluation_id=body.evaluation_id,
            run_id=body.run_id,
            step_id=body.step_id,
            recovered_by=operator.operator_id,
            expected_attempt_token=body.expected_attempt_token,
        )
        return {
            "evaluation_id": result.evaluation_id,
            "attempt_token": result.attempt_token,
            "reused": result.reused,
            "record": result.record.to_dict() if result.record is not None else None,
        }

    try:
        return _with_operator(principal, operation)
    except EvaluationConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except EvaluationNeedsIntervention as exc:
        raise HTTPException(422, str(exc)) from None
    except (EvaluationError, ProjectStorageError) as exc:
        raise HTTPException(409, str(exc)) from None


class ResearchSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str
    source_id: str
    version: int = Field(ge=1)
    source_type: Literal["WEB", "PDF", "BOOK", "MAP", "REPORT", "DOCUMENT", "USER_NOTE", "OTHER"]
    title: str
    author: str | None = None
    publisher: str | None = None
    url_or_reference: str | None = None
    publication_date: str | None = None
    reliability: float | None = Field(default=None, ge=0, le=1)
    notes: str | None = None
    content: str | None = None


class ResearchRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str
    research_id: str
    action: Literal["EXTRACT", "VERIFY"]
    source_refs: list[dict] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    run_id: str
    step_id: str
    model: str | None = None


class ResearchProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    proposal_id: str
    operation_id: str
    target_fact_ids: dict[str, str]
    fiction_decision: dict | None = None


def _research_operation(principal, operation, *, read_only=False):
    from app.p20_core.research import ResearchError
    try:
        return _with_operator(principal, operation, read_only=read_only)
    except ResearchError as exc:
        raise HTTPException(422, str(exc)) from None
    except (ValueError, TypeError, KeyError) as exc:
        # Request and evidence errors expose no provider responses or credentials.
        raise HTTPException(422, "RESEARCH_INVALID:" + type(exc).__name__) from None
    except ProjectStorageError:
        raise HTTPException(409, "RESEARCH_STORAGE_CONFLICT") from None


@router.post("/projects/{project_id}/research/questions")
def research_question(project_id: str, body: ResearchQuestionRequest, principal=Depends(authenticated_operator)):
    from app.p20_core.research import create_research
    return _research_operation(principal, lambda operator, registry: create_research(
        _project(registry, project_id), requested_by=operator.operator_id, **body.model_dump()))


@router.post("/projects/{project_id}/research/sources")
def research_source(project_id: str, body: ResearchSourceRequest, principal=Depends(authenticated_operator)):
    from app.p20_core.research import import_source
    return _research_operation(principal, lambda operator, registry: import_source(
        _project(registry, project_id), **body.model_dump()))


@router.get("/projects/{project_id}/research/records/{research_id}")
def read_research(project_id: str, research_id: str, principal=Depends(authenticated_operator)):
    from app.p20_core.research import research_context
    def read(operator, registry):
        repository = _project(registry, project_id)
        with repository.connect(read_only=True) as connection:
            result = research_context(repository, research_id, include_sources=True, connection=connection)
            research_state = repository.read_research_state(connection)
            invocations = repository.list_model_invocations(connection=connection)
        operations = research_state["operations"].values()
        call_ids = {op.get("invocation", op.get("failure", {})).get("call_id")
                    for op in operations if isinstance(op.get("invocation", op.get("failure", {})), dict)
                    and op.get("request", {}).get("research_id") == research_id}
        from app.p20_core.model_provenance import project_trace
        result["model_provenance"] = [project_trace(trace)
                                      for trace in invocations
                                      if trace["operation_id"] in call_ids]
        event_refs = []
        related_source_ids = {
            source["source_id"] for source in result["sources"].values()
        }
        for operation_id, stored in sorted(research_state["operations"].items()):
            result_record = stored.get("result")
            request = stored.get("request", {})
            if not (
                isinstance(result_record, dict)
                and (
                    result_record.get("research_id") == research_id
                    or result_record.get("source_id") in related_source_ids
                )
                or request.get("research_id") == research_id
            ):
                continue
            namespace = "RESEARCH_OPERATION" if "request" in stored else "RESEARCH_COMMAND"
            events, _cursor = repository.list_memory_events(
                operation=(namespace, operation_id), limit=200,
            )
            event_refs.extend(
                {"memory_event_id": event.memory_event_id, "event_type": event.event_type,
                 "content_hash": event.content_hash}
                for event in events
            )
        from app.p20_core.memory_ledger import MEMORY_LEDGER_COVERAGE
        result["memory_event_refs"] = event_refs
        result["coverage"] = MEMORY_LEDGER_COVERAGE
        return result
    return _research_operation(principal, read, read_only=True)


@router.post("/projects/{project_id}/research/execute")
def execute_research(project_id: str, body: ResearchRunRequest, principal=Depends(authenticated_operator)):
    from app.model_policy import resolve_model
    from app.p20_core.context_runtime import ProjectExecutionContext
    from app.p20_core.research import run_research, ResearchError
    # Release the system credential/registry transaction BEFORE external work.
    repository = _with_operator(principal, lambda operator, registry: _project(registry, project_id))
    decision = resolve_model(body.model)
    if not decision.allowlist_ok and os.getenv("MODEL_POLICY_MODE", "PERMISSIVE").upper() == "STRICT":
        raise HTTPException(422, "RESEARCH_MODEL_POLICY_DENIED")
    execution = ProjectExecutionContext.create(project_id=project_id, book_id=repository.context.book_id,
        series_id=None, run_id=body.run_id, step_id=body.step_id)
    try:
        return run_research(repository, execution=execution,
            **body.model_dump(exclude={"run_id", "step_id", "model"}),
            requested_model=decision.requested_model, effective_model=decision.effective_model,
            model_routing={**decision.provenance, "request_sources": {
                "body": body.model_dump(include={"model"}, exclude_unset=True)}})
    except ResearchError as exc:
        raise HTTPException(422, str(exc)) from None
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        raise HTTPException(422, "RESEARCH_EXECUTION_FAILED:" + type(exc).__name__) from None


@router.post("/projects/{project_id}/research/operations/{operation_id}/recover")
def recover_research(project_id: str, operation_id: str, principal=Depends(authenticated_operator)):
    from app.p20_core.research import recover_operation
    return _research_operation(principal, lambda operator, registry: recover_operation(
        _project(registry, project_id), operation_id, recovered_by=operator.operator_id))


@router.post("/projects/{project_id}/research/proposals")
def propose_research(project_id: str, body: ResearchProposalRequest, principal=Depends(authenticated_operator)):
    from app.p20_core.canon_service import prepare_research_proposal
    return _research_operation(principal, lambda operator, registry: prepare_research_proposal(
        _project(registry, project_id), **body.model_dump()))


# GAP-018 keeps HTTP parsing here; all book lifecycle rules live in P20 services.
def _gap018_project(registry, project_id: str, book_id: str):
    if registry.get(project_id) != book_id:
        raise OperatorError("OPERATOR_PROJECT_ACCESS_DENIED", 403)
    return _project(registry, project_id)


def _gap018_operation(principal, operation, *, read_only=False):
    from app.p20_core.book_qa import BookQAIntegrityError
    from app.p20_core.candidate_master import CandidateIntegrityError
    from app.p20_core.cross_store_recovery import CrossStoreRecoveryError
    from app.p20_core.gap018_receipts import Gap018RequestConflict
    from app.p20_core.manuscript_version import ManuscriptBlocked, ManuscriptIntegrityError
    from app.p20_core.source_master import SourceContractError
    try:
        return _with_operator(principal, operation, read_only=read_only)
    except ManuscriptBlocked as exc:
        status = 409 if any(item.code == "MANUSCRIPT_HEAD_CHANGED" for item in exc.blockers) else 422
        raise HTTPException(status, [asdict(item) for item in exc.blockers]) from None
    except Gap018RequestConflict:
        raise HTTPException(409, "GAP018_REQUEST_ID_CONFLICT") from None
    except (ManuscriptIntegrityError, BookQAIntegrityError, CandidateIntegrityError,
            SourceContractError, CrossStoreRecoveryError):
        raise HTTPException(409, "GAP018_INTEGRITY_CONFLICT") from None
    except ValueError:
        raise HTTPException(422, "GAP018_INVALID_REQUEST") from None


class Gap018ChapterBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    chapter_id: str
    version: int
    artifact_ref: str
    artifact_sha256: str
    text_sha256: str
    evaluation_id: str
    evaluation_record_hash: str
    chapter_commit_operation_id: str
    canonical_commit_operation_id: str


class Gap018CompositionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str
    project_id: str
    book_id: str
    ordered_chapter_ids: list[str]


class Gap018ManuscriptBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int
    chapter_versions: list[Gap018ChapterBody]
    composition: Gap018CompositionBody
    book_bible_source_ref: str
    book_bible_version: str
    book_bible_hash: str
    canon_source_ref: str
    canon_revision: str
    canon_snapshot_hash: str
    source_language: str
    request_id: str
    parent_manuscript_id: str | None = None
    series_id: str | None = None
    composition_policy_version: str = "GAP018_UTF8_DOUBLE_LF_V1"


@router.post("/projects/{project_id}/books/{book_id}/manuscripts")
def gap018_seal_manuscript(project_id: str, book_id: str, body: Gap018ManuscriptBody,
                           principal=Depends(authenticated_operator)):
    from app.p20_core.manuscript_version import (
        ChapterSelection, CompositionDeclaration, SealRequest, seal_manuscript,
    )

    def operation(_operator, registry):
        data = body.model_dump()
        data["project_id"], data["book_id"] = project_id, book_id
        data["chapter_versions"] = tuple(ChapterSelection(**item) for item in data["chapter_versions"])
        composition = data["composition"]
        composition["ordered_chapter_ids"] = tuple(composition["ordered_chapter_ids"])
        data["composition"] = CompositionDeclaration(**composition)
        return seal_manuscript(_gap018_project(registry, project_id, book_id),
                               SealRequest(**data)).to_dict()
    return _gap018_operation(principal, operation)


@router.get("/projects/{project_id}/books/{book_id}/manuscripts/{manuscript_id}")
def gap018_read_manuscript(project_id: str, book_id: str, manuscript_id: str,
                           principal=Depends(authenticated_operator)):
    from app.p20_core.manuscript_version import load_manuscript
    def operation(_operator, registry):
        repository = _gap018_project(registry, project_id, book_id)
        if (manuscript_id.startswith("MV-") and len(manuscript_id) == 67
                and repository.get_cross_store_operation_readonly(
                    "gap018-manuscript-" + manuscript_id[3:]) is None):
            raise OperatorError("MANUSCRIPT_NOT_FOUND", 404)
        return load_manuscript(repository, manuscript_id).to_dict()
    return _gap018_operation(principal, operation, read_only=True)


class Gap018BookQABody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    manuscript_id: str
    content_hash: str
    manifest_hash: str
    request_id: str
    policy_version: str
    policy_hash: str
    criteria_version: str
    criteria_hash: str
    model: str | None = None
    reevaluation_of: str | None = None


@router.post("/projects/{project_id}/books/{book_id}/book-qa")
def gap018_run_book_qa(project_id: str, book_id: str, body: Gap018BookQABody,
                       principal=Depends(authenticated_operator)):
    from app.model_policy import resolve_model
    from app.p20_core.book_qa_execution import (
        BookQARecoveryRequired, BookQARunRequest, BookQATransportError,
        run_book_qa,
    )
    from app.p20_core.gap018_receipts import Gap018RequestConflict
    # A model call must not hold the system credential/registry transaction.
    repository = _gap018_operation(principal, lambda _operator, registry:
                                   _gap018_project(registry, project_id, book_id))
    decision = resolve_model(body.model)
    if not decision.allowlist_ok and os.getenv("MODEL_POLICY_MODE", "PERMISSIVE").upper() == "STRICT":
        raise HTTPException(422, "BOOK_QA_MODEL_POLICY_DENIED")
    request = BookQARunRequest(project_id, book_id, body.manuscript_id,
                               body.content_hash, body.manifest_hash, body.request_id,
                               decision.requested_model, decision.effective_model,
                               body.reevaluation_of, body.policy_version,
                               body.policy_hash, body.criteria_version,
                               body.criteria_hash)
    try:
        return run_book_qa(repository, request, routing={**decision.provenance,
            "request_sources": {"body": body.model_dump(include={"model"}, exclude_unset=True)}}).to_dict()
    except Gap018RequestConflict:
        raise HTTPException(409, "GAP018_REQUEST_ID_CONFLICT") from None
    except BookQARecoveryRequired as exc:
        raise HTTPException(409, {"status": "RECOVERY_REQUIRED",
                                  "operation_id": exc.operation_id}) from None
    except BookQATransportError as exc:
        raise HTTPException(503, {"status": "MODEL_TRANSPORT_FAILED",
                                  "operation_id": exc.operation_id}) from None
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        raise HTTPException(422, "BOOK_QA_EXECUTION_FAILED:" + type(exc).__name__) from None


@router.get("/projects/{project_id}/books/{book_id}/book-qa/{qa_id}")
def gap018_read_book_qa(project_id: str, book_id: str, qa_id: str,
                        principal=Depends(authenticated_operator)):
    from app.p20_core.book_qa import load_book_qa_report
    def operation(_operator, registry):
        repository = _gap018_project(registry, project_id, book_id)
        if (qa_id.startswith("BQA-") and len(qa_id) == 68
                and repository.get_cross_store_operation_readonly(
                    "gap018-book-qa-" + qa_id[4:]) is None):
            raise OperatorError("BOOK_QA_NOT_FOUND", 404)
        return load_book_qa_report(repository, qa_id).to_dict()
    return _gap018_operation(principal, operation, read_only=True)


class Gap018CandidateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    manuscript_id: str
    content_hash: str
    manifest_hash: str
    qa_id: str
    report_hash: str
    candidate_version: int
    request_id: str
    parent_candidate_id: str | None = None


@router.post("/projects/{project_id}/books/{book_id}/candidate-masters")
def gap018_create_candidate(project_id: str, book_id: str, body: Gap018CandidateBody,
                            principal=Depends(authenticated_operator)):
    from app.p20_core.candidate_master import CandidateRequest, create_candidate
    return _gap018_operation(principal, lambda _operator, registry: create_candidate(
        _gap018_project(registry, project_id, book_id),
        CandidateRequest(project_id=project_id, book_id=book_id, **body.model_dump())).to_dict())


@router.get("/projects/{project_id}/books/{book_id}/candidate-masters/{candidate_id}")
def gap018_read_candidate(project_id: str, book_id: str, candidate_id: str,
                          principal=Depends(authenticated_operator)):
    from app.p20_core.candidate_master import load_candidate
    def operation(_operator, registry):
        repository = _gap018_project(registry, project_id, book_id)
        if (candidate_id.startswith("CM-") and len(candidate_id) == 67
                and repository.get_cross_store_operation_readonly(
                    "gap018-candidate-" + candidate_id[3:]) is None):
            raise OperatorError("CANDIDATE_NOT_FOUND", 404)
        return load_candidate(repository, candidate_id).to_dict()
    return _gap018_operation(principal, operation, read_only=True)


@router.post("/projects/{project_id}/books/{book_id}/candidate-masters/{candidate_id}/review")
def gap018_review_candidate(project_id: str, book_id: str, candidate_id: str,
                            principal=Depends(authenticated_operator)):
    from app.p20_core.source_promotion import issue_source_review
    return _gap018_operation(principal, lambda operator, registry: issue_source_review(
        _gap018_project(registry, project_id, book_id), candidate_id, operator,
        ttl_seconds=challenge_ttl_seconds()))


class Gap018SourceHeadBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_master_id: str | None
    version: int | None
    head_hash: str | None


class Gap018DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str
    challenge_id: str
    decision: Literal["APPROVE", "REJECT"]
    candidate_hash: str
    manuscript_hash: str
    manifest_hash: str
    qa_report_hash: str
    expected_head: Gap018SourceHeadBody


@router.post("/projects/{project_id}/books/{book_id}/candidate-masters/{candidate_id}/decision")
def gap018_decide_candidate(project_id: str, book_id: str, candidate_id: str,
                            body: Gap018DecisionBody, principal=Depends(authenticated_operator)):
    from app.p20_core.source_promotion import record_source_decision
    result = _gap018_operation(principal, lambda operator, registry: record_source_decision(
        _gap018_project(registry, project_id, book_id), candidate_id,
        body.model_dump(), operator))
    if result.get("status") == "SOURCE_CONFLICT":
        raise HTTPException(409, result)
    return result


@router.get("/projects/{project_id}/books/{book_id}/source-masters/current")
def gap018_current_source(project_id: str, book_id: str,
                          principal=Depends(authenticated_operator)):
    from app.p20_core.source_promotion import current_source_master
    result = _gap018_operation(principal, lambda _operator, registry: current_source_master(
        _gap018_project(registry, project_id, book_id)), read_only=True)
    return None if result is None else asdict(result)


@router.get("/projects/{project_id}/books/{book_id}/source-masters/{source_master_id}")
def gap018_read_source(project_id: str, book_id: str, source_master_id: str,
                      principal=Depends(authenticated_operator)):
    from app.p20_core.source_promotion import load_source_master
    return _gap018_operation(principal, lambda _operator, registry: asdict(load_source_master(
        _gap018_project(registry, project_id, book_id), source_master_id)), read_only=True)


@router.post("/projects/{project_id}/books/{book_id}/source-masters/operations/{operation_id}/recover")
def gap018_recover_source(project_id: str, book_id: str, operation_id: str,
                         principal=Depends(authenticated_operator)):
    from app.p20_core.source_promotion import recover_source_promotion
    result = _gap018_operation(principal, lambda _operator, registry: recover_source_promotion(
        _gap018_project(registry, project_id, book_id), operation_id))
    if result.get("status") == "SOURCE_CONFLICT":
        raise HTTPException(409, result)
    return result

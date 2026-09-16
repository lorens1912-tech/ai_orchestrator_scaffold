"""Dedicated operator surface. Authentication is not domain mutation approval."""
from __future__ import annotations

import ipaddress
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.p20_core.local_operator import OperatorError, authenticate, project_for_operator
from app.p20_core.project_repository import (
    ensure_system_repository, SeriesAccessContext, SeriesAccessError,
    SeriesRepository, StorageResolver, SystemRepository,
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
        with ensure_system_repository().local_operator_transaction() as (state, registry):
            authenticate(state, None if credential is None else credential.credentials)
            return credential.credentials
    except OperatorError as exc:
        raise HTTPException(exc.status, exc.code) from None
    except SeriesAccessError:
        raise HTTPException(403, "SERIES_ACCESS_DENIED") from None


def _with_operator(token, operation):
    try:
        # Revalidate and keep credential/registry stable through project commit.
        # Entire transaction runs in this endpoint's worker thread.
        with ensure_system_repository().local_operator_transaction() as (state, registry):
            return operation(authenticate(state, token), registry)
    except OperatorError as exc:
        raise HTTPException(exc.status, exc.code) from None
    except SeriesAccessError:
        raise HTTPException(403, "SERIES_ACCESS_DENIED") from None


def _project(registry, project_id):
    book_id = registry.get(project_id)
    if book_id is None:
        raise OperatorError("OPERATOR_PROJECT_ACCESS_DENIED")
    return project_for_operator(registry, project_id, book_id)


def _proposal_target(registry, project_id: str, proposal_id: str):
    """Resolve an authorized proposal to its sole owning local repository."""
    project = _project(registry, project_id)
    matches = []
    if project.get_metadata("canonical_proposal.v1:" + proposal_id) is not None:
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
            series.require_registered_member(access, book_id=book_id)
            matches.append((series, access))
    if not matches:
        raise OperatorError("PROPOSAL_NOT_FOUND", 404)
    if len(matches) != 1:
        raise OperatorError("PROPOSAL_SCOPE_AMBIGUOUS", 409)
    return matches[0]


@router.get("/identity")
def identity(principal=Depends(authenticated_operator)):
    return _with_operator(principal, lambda operator, registry: operator.to_dict())


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
        repository, access = _proposal_target(registry, project_id, proposal_id)
        return operator_proposal_review(
            repository, proposal_id, operator, ttl_seconds=challenge_ttl_seconds(),
            issue_challenge=False, series_access=access,
        )
    return _with_operator(principal, operation)


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

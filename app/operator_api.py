"""Dedicated operator surface. Authentication is not domain mutation approval."""
from __future__ import annotations

import ipaddress
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.p20_core.local_operator import OperatorError, authenticate, project_for_operator
from app.p20_core.project_repository import ensure_system_repository, SeriesAccessError
from app.p20_core.canon_service import operator_proposal_review, record_operator_decision, commit_canonical_proposal

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


@router.get("/identity")
def identity(principal=Depends(authenticated_operator)):
    return _with_operator(principal, lambda operator, registry: operator.to_dict())


@router.get("/projects/{project_id}/proposals/{proposal_id}")
def read_proposal(project_id: str, proposal_id: str, principal=Depends(authenticated_operator)):
    return _with_operator(principal, lambda operator, registry:
        operator_proposal_review(_project(registry, project_id), proposal_id, operator,
                                ttl_seconds=challenge_ttl_seconds(), issue_challenge=False))


@router.post("/projects/{project_id}/proposals/{proposal_id}/review")
def prepare_decision(project_id: str, proposal_id: str, principal=Depends(authenticated_operator)):
    return _with_operator(principal, lambda operator, registry:
        operator_proposal_review(_project(registry, project_id), proposal_id, operator,
                                ttl_seconds=challenge_ttl_seconds()))


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
    return _with_operator(principal, lambda operator, registry:
        record_operator_decision(_project(registry, project_id), proposal_id, operator, body.model_dump()))


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    proposal_hash: str


@router.post("/projects/{project_id}/proposals/{proposal_id}/commit")
def commit(project_id: str, proposal_id: str, body: CommitRequest,
           principal=Depends(authenticated_operator)):
    return _with_operator(principal, lambda operator, registry:
        commit_canonical_proposal(_project(registry, project_id), proposal_id,
                                  expected_hash=body.proposal_hash, identity=operator))

"""Local single-operator authentication. Never registered as an agent tool."""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.p20_core.project_repository import SystemRepository, StorageResolver


class OperatorError(ValueError):
    def __init__(self, code: str, status: int = 403):
        super().__init__(code)
        self.code = code
        self.status = status


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OperatorIdentity:
    operator_id: str
    credential_id: str
    credential_version: int

    def to_dict(self) -> dict:
        return dict(operator_id=self.operator_id, credential_id=self.credential_id,
                    credential_version=self.credential_version)


def authenticate(state: dict, token: str | None) -> OperatorIdentity:
    if not state:
        raise OperatorError("OPERATOR_NOT_CONFIGURED", 503)
    credential = state["credentials"][-1]
    supplied = token_hash(token or "")
    matches = secrets.compare_digest(supplied, credential["token_hash"])
    if not token or not credential["active"] or not matches:
        raise OperatorError("INVALID_OPERATOR_CREDENTIAL", 401)
    return OperatorIdentity(state["operator_id"], credential["credential_id"], credential["version"])


def _credential(token: str, version: int) -> dict:
    return {"credential_id": "credential-" + uuid4().hex, "token_hash": token_hash(token),
            "version": version, "active": True, "created_at": now_iso()}


def initialize_operator(repository: SystemRepository, secret_path: Path) -> OperatorIdentity:
    """Explicit trusted local command only. No HTTP bootstrap or automatic reset."""
    from app.operator_dpapi import write_secret
    with repository.local_operator_transaction() as (state, _registry):
        if state:
            raise OperatorError("OPERATOR_ALREADY_CONFIGURED", 409)
        if secret_path.exists():
            raise OperatorError("SECRET_FILE_ALREADY_EXISTS", 409)
        token = secrets.token_urlsafe(32)
        write_secret(secret_path, token, replace=False)
        state.update(operator_id="operator-" + uuid4().hex, credentials=[_credential(token, 1)])
        return authenticate(state, token)


def rotate_operator(repository: SystemRepository, secret_path: Path) -> OperatorIdentity:
    from app.operator_dpapi import read_secret, write_secret
    token = read_secret(secret_path)
    with repository.local_operator_transaction() as (state, _registry):
        identity = authenticate(state, token)
        replacement = secrets.token_urlsafe(32)
        # Keep the old encrypted credential on disk until DB commit succeeds.
        # An interrupted installation fails closed; never auto-reset an operator.
        pending = secret_path.with_suffix(secret_path.suffix + ".pending")
        write_secret(pending, replacement, replace=False)
        state["credentials"][-1].update(active=False, revoked_at=now_iso())
        state["credentials"].append(_credential(replacement, identity.credential_version + 1))
        new_identity = authenticate(state, replacement)
    pending.replace(secret_path)
    return new_identity


def revoke_operator(repository: SystemRepository, secret_path: Path) -> None:
    from app.operator_dpapi import read_secret
    with repository.local_operator_transaction() as (state, _registry):
        authenticate(state, read_secret(secret_path))
        state["credentials"][-1].update(active=False, revoked_at=now_iso())


def project_for_operator(registry: dict, project_id: str, book_id: str):
    if registry.get(project_id) != book_id:
        raise OperatorError("OPERATOR_PROJECT_ACCESS_DENIED")
    from app.p20_core.project_repository import ProjectRepository
    repository = ProjectRepository(StorageResolver().resolve_project(project_id, book_id=book_id))
    if not repository.db_path.is_file():
        raise OperatorError("PROJECT_NOT_FOUND", 404)
    return repository

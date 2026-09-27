"""Project-owned immutable request bindings for GAP-018 commands."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.p20_core.project_repository import ProjectRepository


class Gap018RequestConflict(ValueError):
    pass


def reserve_request(repository: ProjectRepository, kind: str, request_id: str,
                    payload: Any) -> None:
    if kind not in {"MANUSCRIPT", "BOOK_QA", "BOOK_QA_RUN", "CANDIDATE"}:
        raise ValueError("invalid GAP-018 request kind")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("GAP-018 request ID is required")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False)
    request_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    record_id = (kind.lower() + "-" +
                 hashlib.sha256(request_id.encode("utf-8")).hexdigest())
    value = {"kind": kind, "request_id": request_id, "request_hash": request_hash}
    with repository.gap018_transaction() as connection:
        known = repository.get_gap018_record("REQUEST", record_id,
                                             connection=connection)
        if known is not None:
            if known != value:
                raise Gap018RequestConflict("GAP-018 request ID conflicts with prior input")
            return
        repository.put_gap018_record("REQUEST", record_id, value,
                                     connection=connection)

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.canon_check import canon_check
from app.p20_core.project_repository import ensure_project_repository_for_book
from app.p20_core.storage_paths import get_books_root, get_runs_root, get_storage_root


def canonical_proposal_hash(proposal: dict) -> str:
    """Contract v1 hash; lifecycle status is not part of the frozen content."""
    import hashlib
    content = {k: v for k, v in proposal.items() if k not in {"status", "proposal_hash"}}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _evidence_hash(value: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _validate_review_proposal(repository, proposal: dict) -> None:
    from app.p20_core.local_operator import OperatorError
    required = {
        "contract_version", "proposal_id", "project_id", "book_id", "series_id", "scope_type",
        "scope_id", "run_id", "step_id", "source_artifact_id", "source_artifact_ref",
        "source_artifact_hash", "source_artifact_version", "source_scene_id", "context_package_id",
        "context_hash", "extraction_candidate_set_id", "extraction_candidate_hash", "verification_ref",
        "proposed_mutations", "source", "actor_ref", "authority_ref", "policy_ref",
        "proposal_version", "proposal_hash", "status", "created_at",
    }
    if set(proposal) != required or proposal["contract_version"] != "1.0":
        raise OperatorError("INVALID_PROPOSAL_SCHEMA", 422)
    import re
    for name in ("proposal_id", "run_id", "step_id", "source_artifact_id", "source_artifact_ref",
                 "source_scene_id", "context_package_id", "extraction_candidate_set_id", "source", "created_at"):
        if not isinstance(proposal[name], str) or not proposal[name].strip():
            raise OperatorError("INVALID_PROPOSAL_SCHEMA", 422)
    for name in ("proposal_hash", "source_artifact_hash", "context_hash", "extraction_candidate_hash"):
        if not isinstance(proposal[name], str) or not re.fullmatch("[0-9a-f]{64}", proposal[name]):
            raise OperatorError("INVALID_PROPOSAL_HASH", 422)
    if type(proposal["source_artifact_version"]) is not int or proposal["source_artifact_version"] < 1:
        raise OperatorError("INVALID_ARTIFACT_VERSION", 422)
    if proposal["status"] != "AWAITING_USER_APPROVAL":
        raise OperatorError("PROPOSAL_NOT_READY_FOR_REVIEW", 409)
    for name in ("verification_ref", "actor_ref", "authority_ref", "policy_ref"):
        if not proposal[name]:
            raise OperatorError("MISSING_PROPOSAL_EVIDENCE", 422)
    if (proposal["project_id"] != repository.scope.scope_id
            or proposal["book_id"] != repository.context.book_id
            or proposal["scope_type"] != "PROJECT" or proposal["scope_id"] != repository.scope.scope_id):
        raise OperatorError("PROPOSAL_SCOPE_MISMATCH")
    if proposal["series_id"] is not None:
        from app.p20_core.project_repository import SeriesRepository, SeriesAccessContext, StorageResolver
        series = SeriesRepository(StorageResolver().resolve_series(proposal["series_id"]))
        if not series.db_path.exists():
            raise OperatorError("SERIES_ACCESS_DENIED")
        series.require_registered_member(
            SeriesAccessContext.bind(proposal["project_id"], proposal["series_id"]),
            book_id=proposal["book_id"],
        )
    if type(proposal["proposal_version"]) is not int or proposal["proposal_version"] < 1:
        raise OperatorError("INVALID_PROPOSAL_VERSION", 422)
    mutations = proposal["proposed_mutations"]
    keys = {"target_entity_type", "target_entity_id", "operation_type", "expected_current_version",
            "expected_current_hash", "proposed_state", "provenance"}
    if not isinstance(mutations, list) or not mutations:
        raise OperatorError("INVALID_PROPOSAL_MUTATIONS", 422)
    identities = []
    from app.p20_core.domain_records import DomainId
    for mutation in mutations:
        if not isinstance(mutation, dict) or set(mutation) != keys:
            raise OperatorError("INVALID_PROPOSAL_MUTATION", 422)
        identity = DomainId.parse(mutation["target_entity_id"])
        if mutation["target_entity_type"] not in (identity.namespace.name, identity.namespace.value):
            raise OperatorError("INVALID_TARGET_TYPE", 422)
        operation = mutation["operation_type"]
        if operation not in {"CREATE", "UPDATE", "REPLACE"}:
            raise OperatorError("INVALID_OPERATION_TYPE", 422)
        if not isinstance(mutation["proposed_state"], dict) or not mutation["provenance"]:
            raise OperatorError("INVALID_PROPOSED_STATE", 422)
        if operation == "CREATE":
            if mutation["expected_current_version"] is not None or mutation["expected_current_hash"] is not None:
                raise OperatorError("INVALID_CREATE_BASE", 422)
        elif type(mutation["expected_current_version"]) is not int or mutation["expected_current_version"] < 1:
            raise OperatorError("INVALID_UPDATE_BASE", 422)
        elif not isinstance(mutation["expected_current_hash"], str) or not re.fullmatch("[0-9a-f]{64}", mutation["expected_current_hash"]):
            raise OperatorError("INVALID_UPDATE_BASE_HASH", 422)
        new_version = mutation["proposed_state"].get("version")
        if type(new_version) is not int or new_version <= (mutation["expected_current_version"] or 0):
            raise OperatorError("INVALID_PROPOSED_VERSION", 422)
        if any(type(mutation["proposed_state"].get(flag)) is not bool for flag in ("frozen", "author_locked")):
            raise OperatorError("MISSING_PROTECTION_STATE", 422)
        identities.append((mutation["target_entity_type"], mutation["target_entity_id"], operation))
    if identities != sorted(identities) or len({i[:2] for i in identities}) != len(identities):
        raise OperatorError("DUPLICATE_OR_UNSORTED_MUTATIONS", 422)
    if canonical_proposal_hash(proposal) != proposal["proposal_hash"]:
        raise OperatorError("PROPOSAL_HASH_MISMATCH", 409)


def _validate_review_basis(proposal: dict, snapshot: dict) -> None:
    from app.p20_core.local_operator import OperatorError
    current = {(r["record_type"], r["record_id"]): json.loads(r["payload_json"])
               for r in snapshot["records"]}
    for mutation in proposal["proposed_mutations"]:
        record = current.get((mutation["target_entity_type"], mutation["target_entity_id"]))
        if mutation["operation_type"] == "CREATE":
            valid = record is None
        else:
            valid = (record is not None and record.get("version") == mutation["expected_current_version"]
                     and _evidence_hash(record) == mutation["expected_current_hash"])
        if not valid:
            raise OperatorError("STALE_PROPOSAL_VERSION", 409)


def save_canonical_proposal_for_review(repository, proposal: dict, *, impact: dict, initial_guard: dict) -> dict:
    """Persist the minimal frozen review record for the future pipeline producer.

    No HTTP/model tool publishes proposals. This does not verify extraction,
    grant domain authority, or perform a canonical commit.
    """
    from app.p20_core.local_operator import OperatorError
    _validate_review_proposal(repository, proposal)
    if proposal["status"] != "AWAITING_USER_APPROVAL":
        raise OperatorError("PROPOSAL_NOT_READY_FOR_REVIEW", 409)
    binding = {k: proposal[k] for k in ("proposal_id", "proposal_hash", "project_id", "scope_type", "scope_id")}
    for evidence in (impact, initial_guard):
        if any(evidence.get(k) != v for k, v in binding.items()):
            raise OperatorError("REVIEW_EVIDENCE_BINDING_MISMATCH", 409)
    if (not impact.get("impact_id") or not impact.get("result")
            or initial_guard.get("outcome") != "REQUIRE_USER_APPROVAL"):
        raise OperatorError("REVIEW_EVIDENCE_MISSING", 409)
    with repository.canonical_proposal_transaction(proposal["proposal_id"]) as (document, snapshot):
        _validate_review_basis(proposal, snapshot)
        version = str(proposal["proposal_version"])
        record = {"proposal": proposal, "impact": impact, "initial_guard": initial_guard,
                  "basis_hash": _evidence_hash(snapshot), "challenges": {}, "decision": None}
        if not document:
            if version != "1":
                raise OperatorError("PROPOSAL_VERSION_CONFLICT", 409)
            document.update(current_version=version, versions={version: record})
        elif version in document["versions"]:
            existing = document["versions"][version]
            if any(existing[k] != record[k] for k in ("proposal", "impact", "initial_guard", "basis_hash")):
                raise OperatorError("IMMUTABLE_PROPOSAL_CONFLICT", 409)
        else:
            if int(version) != int(document["current_version"]) + 1:
                raise OperatorError("PROPOSAL_VERSION_CONFLICT", 409)
            document["versions"][document["current_version"]]["superseded"] = True
            document["versions"][version] = record
            document["current_version"] = version
    return proposal


def operator_proposal_review(repository, proposal_id: str, identity, *, ttl_seconds: int,
                             issue_challenge: bool = True) -> dict:
    import secrets
    import time
    from app.p20_core.local_operator import OperatorError
    with repository.canonical_proposal_transaction(proposal_id) as (document, snapshot):
        if not document:
            raise OperatorError("PROPOSAL_NOT_FOUND", 404)
        record = document["versions"][document["current_version"]]
        proposal = record["proposal"]
        _validate_review_proposal(repository, proposal)
        _validate_review_basis(proposal, snapshot)
        if record["basis_hash"] != _evidence_hash(snapshot):
            raise OperatorError("STALE_PROPOSAL_ANALYSIS", 409)
        challenge = None
        if record["decision"] is None and issue_challenge:
            challenge_id = "challenge-" + secrets.token_hex(32)
            challenge = {"challenge_id": challenge_id, **identity.to_dict(),
                         "proposal_hash": proposal["proposal_hash"], "expires_at": time.time() + ttl_seconds,
                         "basis_hash": record["basis_hash"], "used": False}
            record["challenges"][challenge_id] = challenge
        return {"proposal": proposal, "impact": record["impact"], "initial_guard": record["initial_guard"],
                "challenge": challenge, "decision": record["decision"], "canonical_commit": False}


def record_operator_decision(repository, proposal_id: str, identity, request: dict) -> dict:
    import time
    from uuid import uuid4
    from app.p20_core.local_operator import OperatorError, now_iso
    with repository.canonical_proposal_transaction(proposal_id) as (document, snapshot):
        if not document:
            raise OperatorError("PROPOSAL_NOT_FOUND", 404)
        record = document["versions"][document["current_version"]]
        proposal = record["proposal"]
        if any(request[k] != proposal[k] for k in ("proposal_hash", "scope_type", "scope_id")):
            raise OperatorError("DECISION_BINDING_MISMATCH", 409)
        challenge = record["challenges"].get(request["challenge_id"])
        if not challenge or any(challenge[k] != v for k, v in identity.to_dict().items()):
            raise OperatorError("INVALID_DECISION_CHALLENGE", 409)
        if challenge["proposal_hash"] != proposal["proposal_hash"]:
            raise OperatorError("STALE_DECISION_CHALLENGE", 409)
        if challenge["used"]:
            previous = record["decision"]
            if previous and previous["challenge_id"] == request["challenge_id"] and previous["decision"] == request["decision"]:
                return previous  # Read-only replay, not another approval or canonical write.
            raise OperatorError("CHALLENGE_ALREADY_USED", 409)
        if record["decision"] is not None:
            raise OperatorError("DECISION_ALREADY_RECORDED", 409)
        if challenge["expires_at"] <= time.time():
            raise OperatorError("DECISION_CHALLENGE_EXPIRED", 409)
        _validate_review_proposal(repository, proposal)
        _validate_review_basis(proposal, snapshot)
        if challenge["basis_hash"] != _evidence_hash(snapshot):
            raise OperatorError("STALE_PROPOSAL_ANALYSIS", 409)
        if request["decision"] not in {"APPROVE", "REJECT"}:
            raise OperatorError("INVALID_OPERATOR_DECISION", 422)
        reference = "authorization-" + uuid4().hex
        evidence = {"authorization_ref": reference, "approval_id": "approval-" + uuid4().hex,
                    **identity.to_dict(), "approved_by": "USER", "actor_ref": identity.operator_id,
                    **{k: proposal[k] for k in ("project_id", "scope_type", "scope_id", "proposal_id", "proposal_hash")},
                    "decision": request["decision"], "authentication_method": "LOCAL_OPERATOR_BEARER_V1",
                    "challenge_id": request["challenge_id"], "created_at": now_iso(),
                    "impact_id": record["impact"]["impact_id"], "impact_result_hash": _evidence_hash(record["impact"]),
                    "canonical_commit": False}
        record["decision"] = evidence
        challenge["used"] = True
        # Decision evidence only. No transition to COMMITTED or bypass of the domain guard.
        return evidence

APP_VERSION = "P20.0-novel-core"
REPO_ROOT = Path(__file__).resolve().parents[2]
BOOKS_ROOT = REPO_ROOT / "books"
RUNS_ROOT = REPO_ROOT / "runs"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def sha256_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_load(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def json_write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _path_for_read(path: Path) -> Path:
    if path.is_absolute():
        return path

    if path.parts and path.parts[0] in {"books", "runs", "audit", "novel_runs"}:
        return get_storage_root() / path

    return REPO_ROOT / path


def _public_path(path: Path) -> str:
    resolved = path.resolve()
    for root in (get_storage_root(), REPO_ROOT):
        try:
            return str(resolved.relative_to(root.resolve())).replace("\\", "/")
        except ValueError:
            pass
    return str(resolved).replace("\\", "/")


def ensure_book_dirs(
    book_id: str,
    *,
    project_id: str | None = None,
    domain_book_id: str | None = None,
) -> Path:
    ensure_project_repository_for_book(
        str(domain_book_id or book_id),
        project_id=project_id,
    )
    book_dir = get_books_root() / str(book_id)
    (book_dir / "memory").mkdir(parents=True, exist_ok=True)
    (book_dir / "artifacts" / "canon").mkdir(parents=True, exist_ok=True)
    (book_dir / "chapters").mkdir(parents=True, exist_ok=True)
    (book_dir / "audit").mkdir(parents=True, exist_ok=True)

    book_bible = book_dir / "book_bible.json"
    if not book_bible.exists():
        json_write(book_bible, {})

    canon_json = book_dir / "memory" / "canon.json"
    if not canon_json.exists():
        json_write(canon_json, {"timeline": [], "decisions": {}, "facts": {}, "approved_chapters": []})

    return book_dir


def ensure_run_dirs(run_id: str) -> Path:
    run_dir = get_runs_root() / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def load_run_state(run_id: str) -> Dict[str, Any]:
    return json_load(ensure_run_dirs(run_id) / "run_state.json", {})


def save_run_state(
    run_id: str,
    *,
    book_id: str,
    status: str,
    last_modes: List[str],
    last_artifact_paths: List[str],
    chapter_path: Optional[str],
    project_id: Optional[str] = None,
    domain_book_id: Optional[str] = None,
    series_id: Optional[str] = None,
    step_id: Optional[str] = None,
) -> Dict[str, Any]:
    run_dir = ensure_run_dirs(run_id)
    state = {
        "run_id": run_id,
        "book_id": book_id,
        "status": status,
        "last_modes": list(last_modes or []),
        "last_artifact_paths": list(last_artifact_paths or []),
        "chapter_path": chapter_path,
        "updated_at": utc_now_iso(),
        "engine": APP_VERSION,
    }
    if project_id is not None:
        state["project_id"] = project_id
    if domain_book_id is not None:
        state["domain_book_id"] = domain_book_id
    if series_id is not None:
        state["series_id"] = series_id
    if step_id is not None:
        state["step_id"] = step_id
    json_write(run_dir / "run_state.json", state)
    return state


def resolve_resume_run_id(
    book_id: str,
    payload_run_id: Optional[str],
    resume: bool,
    *,
    project_id: str | None = None,
    domain_book_id: str | None = None,
) -> str:
    explicit = str(payload_run_id or "").strip()
    if explicit:
        return explicit

    book_dir = ensure_book_dirs(
        book_id,
        project_id=project_id,
        domain_book_id=domain_book_id,
    )
    runs_dir = get_runs_root()

    def _new_run_id_local() -> str:
        from datetime import datetime, timezone
        from uuid import uuid4
        return f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"

    def _state_for(run_dir: Path) -> Dict[str, Any]:
        state = json_load(run_dir / "run_state.json", {})
        if isinstance(state, dict) and state:
            return state
        state = json_load(run_dir / "state.json", {})
        return state if isinstance(state, dict) else {}

    def _same_book_existing(candidate: str) -> Optional[str]:
        rid = str(candidate or "").strip()
        if not rid:
            return None
        run_dir = runs_dir / rid
        if not run_dir.exists():
            return None
        state = _state_for(run_dir)
        stored_book_id = str(
            state.get("domain_book_id") or state.get("book_id") or ""
        )
        if stored_book_id not in {str(book_id), str(domain_book_id or book_id)}:
            return None
        stored_project_id = str(state.get("project_id") or "")
        if project_id is not None and stored_project_id and stored_project_id != project_id:
            return None
        return rid

    if not resume:
        return _new_run_id_local()

    marker = book_dir / "audit" / "latest_run_id.txt"
    if marker.exists():
        marker_rid = str(marker.read_text(encoding="utf-8") or "").strip()
        resolved = _same_book_existing(marker_rid)
        if resolved:
            return resolved

    candidates: list[tuple[str, str]] = []
    if runs_dir.exists():
        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            state = _state_for(run_dir)
            stored_book_id = str(
                state.get("domain_book_id") or state.get("book_id") or ""
            )
            if stored_book_id not in {str(book_id), str(domain_book_id or book_id)}:
                continue
            stored_project_id = str(state.get("project_id") or "")
            if project_id is not None and stored_project_id and stored_project_id != project_id:
                continue
            rid = str(state.get("run_id") or run_dir.name)
            updated = str(state.get("updated_at") or state.get("created_at") or "")
            candidates.append((updated, rid))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    return _new_run_id_local()

def update_latest_run_marker(
    book_id: str,
    run_id: str,
    *,
    project_id: str | None = None,
    domain_book_id: str | None = None,
) -> None:
    book_dir = ensure_book_dirs(
        book_id,
        project_id=project_id,
        domain_book_id=domain_book_id,
    )
    marker = book_dir / "audit" / "latest_run_id.txt"
    marker.write_text(str(run_id), encoding="utf-8")


def merge_dicts(a: Any, b: Any) -> Any:
    if not isinstance(a, dict):
        a = {}
    if not isinstance(b, dict):
        b = {}
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_dicts(out[k], v)
        else:
            out[k] = v
    return out


def _chapter_sort_key(path: Path) -> Tuple[int, str]:
    stem = path.stem
    try:
        idx = int(stem.split("_")[-1])
    except Exception:
        idx = 0
    return idx, path.name


def rebuild_canon_from_chapters(
    book_id: str,
    *,
    project_id: str | None = None,
    domain_book_id: str | None = None,
) -> Dict[str, Any]:
    book_dir = ensure_book_dirs(
        book_id,
        project_id=project_id,
        domain_book_id=domain_book_id,
    )
    canon_path = book_dir / "memory" / "canon.json"
    canon = json_load(canon_path, {"timeline": [], "decisions": {}, "facts": {}, "approved_chapters": []})
    if not isinstance(canon, dict):
        canon = {"timeline": [], "decisions": {}, "facts": {}, "approved_chapters": []}

    chapters_dir = book_dir / "chapters"
    chapter_files = sorted(chapters_dir.glob("chapter_*.json"), key=_chapter_sort_key)

    approved: List[Dict[str, Any]] = []
    last_accepted: Optional[Dict[str, Any]] = None

    for path in chapter_files:
        data = json_load(path, {})
        if not isinstance(data, dict):
            continue

        chapter_id = str(data.get("chapter_id") or path.stem)
        chapter_rel = _public_path(path)
        run_id = str(data.get("run_id") or "")
        sha256 = str(data.get("sha256") or "")
        accepted_at = str(data.get("created_at") or utc_now_iso())

        item = {
            "chapter_id": chapter_id,
            "chapter_path": chapter_rel,
            "run_id": run_id,
            "sha256": sha256,
            "accepted_at": accepted_at,
        }
        approved.append(item)
        last_accepted = {
            "chapter_id": chapter_id,
            "chapter_path": chapter_rel,
            "run_id": run_id,
            "sha256": sha256,
        }

    canon["approved_chapters"] = approved

    if last_accepted is not None:
        canon["last_accepted_chapter"] = last_accepted
        canon["chapter_count"] = len(approved)
    else:
        canon.pop("last_accepted_chapter", None)
        canon.pop("chapter_count", None)

    canon["updated_at"] = utc_now_iso()
    canon["engine"] = APP_VERSION

    json_write(canon_path, canon)
    return canon


def load_canon_snapshot(
    book_id: str,
    *,
    project_id: str | None = None,
    domain_book_id: str | None = None,
) -> Tuple[Dict[str, Any], Path]:
    book_dir = ensure_book_dirs(
        book_id,
        project_id=project_id,
        domain_book_id=domain_book_id,
    )
    book_bible = json_load(book_dir / "book_bible.json", {})
    canon_memory = rebuild_canon_from_chapters(
        book_id,
        project_id=project_id,
        domain_book_id=domain_book_id,
    )

    snapshot = merge_dicts(book_bible, canon_memory)
    snapshot["_meta"] = {
        "book_id": book_id,
        "generated_at": utc_now_iso(),
        "engine": APP_VERSION,
    }

    snapshot_path = book_dir / "artifacts" / "canon" / f"canon_snapshot_{utc_stamp()}.json"
    json_write(snapshot_path, snapshot)
    return snapshot, snapshot_path


def commit_chapter_to_canon(
    *,
    book_id: str,
    run_id: str,
    chapter_path: str,
    chapter_id: str,
    chapter_sha256: str,
    project_id: str | None = None,
    domain_book_id: str | None = None,
) -> Dict[str, Any]:
    book_dir = ensure_book_dirs(
        book_id,
        project_id=project_id,
        domain_book_id=domain_book_id,
    )
    canon_path = book_dir / "memory" / "canon.json"
    canon = json_load(canon_path, {"timeline": [], "decisions": {}, "facts": {}, "approved_chapters": []})
    if not isinstance(canon, dict):
        canon = {"timeline": [], "decisions": {}, "facts": {}, "approved_chapters": []}

    approved = canon.get("approved_chapters")
    if not isinstance(approved, list):
        approved = []

    already = False
    for item in approved:
        if isinstance(item, dict) and item.get("chapter_id") == chapter_id:
            already = True
            break

    if not already:
        approved.append(
            {
                "chapter_id": chapter_id,
                "chapter_path": chapter_path,
                "run_id": run_id,
                "sha256": chapter_sha256,
                "accepted_at": utc_now_iso(),
            }
        )

    canon["approved_chapters"] = approved
    canon["last_accepted_chapter"] = {
        "chapter_id": chapter_id,
        "chapter_path": chapter_path,
        "run_id": run_id,
        "sha256": chapter_sha256,
    }
    canon["chapter_count"] = len(approved)
    canon["updated_at"] = utc_now_iso()
    canon["engine"] = APP_VERSION

    json_write(canon_path, canon)
    return rebuild_canon_from_chapters(
        book_id,
        project_id=project_id,
        domain_book_id=domain_book_id,
    )


def coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "body", "input", "topic"):
            if key in value and isinstance(value[key], str):
                return value[key]
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    if isinstance(value, list):
        return "\n".join(coerce_text(x) for x in value)
    return str(value)


def read_artifact_text(path_value: str) -> str:
    p = Path(path_value)
    if not p.is_absolute():
        p = _path_for_read(p)
    if not p.exists():
        return ""

    data = json_load(p, {})
    if not isinstance(data, dict):
        return ""

    candidates = [
        data.get("content"),
        data.get("text"),
        ((data.get("result") or {}).get("content") if isinstance(data.get("result"), dict) else None),
        (((data.get("result") or {}).get("payload") or {}).get("content") if isinstance((data.get("result") or {}).get("payload"), dict) else None),
        (((data.get("result") or {}).get("payload") or {}).get("text") if isinstance((data.get("result") or {}).get("payload"), dict) else None),
        ((data.get("payload") or {}).get("content") if isinstance(data.get("payload"), dict) else None),
        ((data.get("payload") or {}).get("text") if isinstance(data.get("payload"), dict) else None),
    ]

    for candidate in candidates:
        txt = coerce_text(candidate).strip()
        if txt:
            return txt

    return ""


def run_canon_check(text: str, canon: Dict[str, Any], scene_ref: Optional[str]) -> Dict[str, Any]:
    if not str(text or "").strip():
        return {"ok": True, "status": "skipped", "reason": "empty_text"}

    try:
        result = canon_check(text, canon, scene_ref)
    except TypeError:
        try:
            result = canon_check(text=text, canon=canon, scene_ref=scene_ref)
        except Exception as e:
            return {"ok": False, "status": "error", "detail": f"canon_check_failed: {e}"}
    except Exception as e:
        return {"ok": False, "status": "error", "detail": f"canon_check_failed: {e}"}

    if isinstance(result, dict):
        return result

    return {"ok": True, "status": "unknown_format", "raw": result}


def canon_blocks(report: Dict[str, Any]) -> bool:
    if not isinstance(report, dict):
        return True

    if report.get("status") == "skipped":
        return False

    for key in ("ok", "passed", "valid"):
        if key in report and report.get(key) is False:
            return True

    for key in ("errors", "violations", "issues", "must_fix", "blocking_issues", "mismatches"):
        value = report.get(key)
        if isinstance(value, list) and len(value) > 0:
            return True
        if isinstance(value, dict) and len(value) > 0:
            return True
        if isinstance(value, int) and value > 0:
            return True
        if isinstance(value, str) and value.strip():
            return True

    return False


def next_chapter_path(book_dir: Path) -> Path:
    chapters_dir = book_dir / "chapters"
    existing = sorted(chapters_dir.glob("chapter_*.json"), key=_chapter_sort_key)
    max_idx = 0
    for p in existing:
        try:
            max_idx = max(max_idx, int(p.stem.split("_")[-1]))
        except Exception:
            pass
    return chapters_dir / f"chapter_{max_idx + 1:03d}.json"


def save_chapter(
    book_id: str,
    run_id: str,
    text: str,
    source_artifact: Optional[str],
    canon_snapshot_path: Path,
    pre_report: Dict[str, Any],
    post_report: Dict[str, Any],
    project_id: str | None = None,
    domain_book_id: str | None = None,
) -> Optional[str]:
    if not str(text or "").strip():
        return None

    book_dir = ensure_book_dirs(
        book_id,
        project_id=project_id,
        domain_book_id=domain_book_id,
    )
    chapter_path = next_chapter_path(book_dir)
    chapter_id = chapter_path.stem

    doc = {
        "chapter_id": chapter_id,
        "book_id": book_id,
        "run_id": run_id,
        "created_at": utc_now_iso(),
        "engine": APP_VERSION,
        "source_artifact": source_artifact,
        "canon_snapshot_path": _public_path(canon_snapshot_path),
        "sha256": sha256_text(text),
        "content": text,
        "text": text,
        "pre_canon_check": pre_report,
        "post_canon_check": post_report,
    }
    json_write(chapter_path, doc)
    return _public_path(chapter_path)


def write_audit(
    book_id: str,
    run_id: str,
    modes: List[str],
    artifact_paths: List[str],
    decision: str,
    chapter_path: Optional[str],
    canon_snapshot_path: Path,
    master_canon: Optional[Dict[str, Any]] = None,
    project_truth: Optional[Dict[str, Any]] = None,
    project_id: Optional[str] = None,
    domain_book_id: Optional[str] = None,
    series_id: Optional[str] = None,
    step_id: Optional[str] = None,
    context_packages: Optional[List[Dict[str, Any]]] = None,
) -> None:
    book_dir = ensure_book_dirs(
        book_id,
        project_id=project_id,
        domain_book_id=domain_book_id,
    )
    audit_doc = {
        "ts": utc_now_iso(),
        "book_id": book_id,
        "run_id": run_id,
        "modes": modes,
        "artifact_paths": artifact_paths,
        "decision": decision,
        "chapter_path": chapter_path,
        "canon_snapshot_path": _public_path(canon_snapshot_path),
        "master_canon": master_canon,
        "project_truth": project_truth,
        "project_id": project_id,
        "domain_book_id": domain_book_id,
        "series_id": series_id,
        "step_id": step_id,
        "context_packages": list(context_packages or []),
        "engine": APP_VERSION,
    }
    if context_packages:
        audit_doc["context_package_id"] = context_packages[-1].get(
            "context_package_id"
        )
        audit_doc["context_hash"] = context_packages[-1].get("context_hash")

    append_jsonl(
        book_dir / "audit" / "audit_log.jsonl",
        audit_doc,
    )

    run_dir = get_runs_root() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    json_write(run_dir / "audit.json", audit_doc)

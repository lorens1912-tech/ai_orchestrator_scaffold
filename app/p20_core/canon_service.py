from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
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


@contextmanager
def _canonical_transaction(repository, proposal_id: str, *, series_access=None,
                           include_writer: bool = False):
    from app.p20_core.project_repository import SeriesRepository
    if isinstance(repository, SeriesRepository):
        with repository.canonical_proposal_transaction(
            series_access, proposal_id, include_writer=include_writer,
        ) as transaction:
            yield transaction
    else:
        with repository.canonical_proposal_transaction(
            proposal_id, include_writer=include_writer,
        ) as transaction:
            yield transaction


@contextmanager
def _pipeline_transaction(repository, operation_id: str, *, series_access=None):
    from app.p20_core.project_repository import SeriesRepository
    if isinstance(repository, SeriesRepository):
        with repository.canonical_pipeline_operation(series_access, operation_id) as state:
            yield state
    else:
        with repository.canonical_pipeline_operation(operation_id) as state:
            yield state


def _validate_review_proposal(repository, proposal: dict, *, series_access=None) -> None:
    from app.p20_core.local_operator import OperatorError
    required = {
        "contract_version", "proposal_id", "project_id", "book_id", "series_id", "scope_type",
        "scope_id", "run_id", "step_id", "source_artifact_id", "source_artifact_ref",
        "source_artifact_hash", "source_artifact_version", "source_scene_id", "context_package_id",
        "context_hash", "extraction_candidate_set_id", "extraction_candidate_hash", "verification_ref",
        "proposed_mutations", "source", "actor_ref", "authority_ref", "policy_ref",
        "proposal_version", "proposal_hash", "status", "created_at",
    }
    research = proposal.get("contract_version") == "2.0" and proposal.get("source_kind") == "RESEARCH"
    source_fields = {"source_artifact_id", "source_artifact_ref", "source_artifact_hash",
                     "source_artifact_version", "source_scene_id", "extraction_candidate_set_id",
                     "extraction_candidate_hash", "verification_ref"}
    if research:
        required = (required - source_fields) | {"source_kind", "research_evidence", "fiction_decision"}
        if proposal.get("scope_type") != "PROJECT" or proposal.get("authority_ref") != "P20_VERIFIED_RESEARCH_V1":
            raise OperatorError("RESEARCH_AUTHORITY_SCOPE_MISMATCH", 422)
    if set(proposal) != required or proposal["contract_version"] != ("2.0" if research else "1.0"):
        raise OperatorError("INVALID_PROPOSAL_SCHEMA", 422)
    import re
    for name in ("proposal_id", "run_id", "step_id", "source_artifact_id", "source_artifact_ref",
                 "source_scene_id", "context_package_id", "extraction_candidate_set_id", "source", "created_at"):
        if research and name in source_fields:
            continue
        if not isinstance(proposal[name], str) or not proposal[name].strip():
            raise OperatorError("INVALID_PROPOSAL_SCHEMA", 422)
    for name in ("proposal_hash", "source_artifact_hash", "context_hash", "extraction_candidate_hash"):
        if research and name in source_fields:
            continue
        if not isinstance(proposal[name], str) or not re.fullmatch("[0-9a-f]{64}", proposal[name]):
            raise OperatorError("INVALID_PROPOSAL_HASH", 422)
    if not research and (type(proposal["source_artifact_version"]) is not int or proposal["source_artifact_version"] < 1):
        raise OperatorError("INVALID_ARTIFACT_VERSION", 422)
    if proposal["status"] not in {"AWAITING_USER_APPROVAL", "APPROVED_FOR_COMMIT", "READY_FOR_ANALYSIS", "COMMITTED", "STALE", "REJECTED", "FAILED"}:
        raise OperatorError("PROPOSAL_NOT_READY_FOR_REVIEW", 409)
    for name in ("verification_ref", "actor_ref", "authority_ref", "policy_ref"):
        if research and name == "verification_ref":
            continue
        if not proposal[name]:
            raise OperatorError("MISSING_PROPOSAL_EVIDENCE", 422)
    from app.p20_core.project_repository import (
        ProjectRepository, SeriesRepository, SeriesAccessContext, StorageResolver,
    )
    if isinstance(repository, ProjectRepository):
        if (proposal["project_id"] != repository.scope.scope_id
                or proposal["book_id"] != repository.context.book_id
                or proposal["scope_type"] != "PROJECT"
                or proposal["scope_id"] != repository.scope.scope_id):
            raise OperatorError("PROPOSAL_SCOPE_MISMATCH")
        if proposal["series_id"] is not None:
            series = SeriesRepository(StorageResolver().resolve_series(proposal["series_id"]))
            if not series.db_path.exists():
                raise OperatorError("SERIES_ACCESS_DENIED")
            series.require_registered_member(
                SeriesAccessContext.bind(proposal["project_id"], proposal["series_id"]),
                book_id=proposal["book_id"],
            )
    elif isinstance(repository, SeriesRepository):
        if not isinstance(series_access, SeriesAccessContext):
            raise OperatorError("SERIES_ACCESS_DENIED")
        series_access.require_project(proposal["project_id"])
        series_access.require_series(repository.scope.scope_id)
        if (proposal["series_id"] != repository.scope.scope_id
                or proposal["scope_type"] != "SERIES"
                or proposal["scope_id"] != repository.scope.scope_id):
            raise OperatorError("PROPOSAL_SCOPE_MISMATCH")
        repository.require_registered_member(series_access, book_id=proposal["book_id"])
    else:
        raise OperatorError("PROPOSAL_SCOPE_MISMATCH")
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
        from app.p20_core.memory_extraction import SUPPORTED_MEMORY_RECORD_TYPES
        namespace = {"CHARACTER_STATE": "CONTEXT", "KNOWLEDGE_EVENT": "KNOWLEDGE",
                     "RELATIONSHIP_CHANGE": "RELATIONSHIP"}.get(mutation["target_entity_type"], mutation["target_entity_type"])
        if mutation["target_entity_type"] not in SUPPORTED_MEMORY_RECORD_TYPES or namespace != identity.namespace.name:
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


def save_canonical_proposal_for_review(repository, proposal: dict, *, impact: dict,
                                       initial_guard: dict, series_access=None) -> dict:
    """Persist the minimal frozen review record for the future pipeline producer.

    No HTTP/model tool publishes proposals. This does not verify extraction,
    grant domain authority, or perform a canonical commit.
    """
    from app.p20_core.local_operator import OperatorError
    _validate_review_proposal(repository, proposal, series_access=series_access)
    if proposal["status"] != "AWAITING_USER_APPROVAL":
        raise OperatorError("PROPOSAL_NOT_READY_FOR_REVIEW", 409)
    binding = {k: proposal[k] for k in ("proposal_id", "proposal_hash", "project_id", "scope_type", "scope_id")}
    for evidence in (impact, initial_guard):
        if any(evidence.get(k) != v for k, v in binding.items()):
            raise OperatorError("REVIEW_EVIDENCE_BINDING_MISMATCH", 409)
    if (not impact.get("impact_id") or not impact.get("result")
            or initial_guard.get("outcome") != "REQUIRE_USER_APPROVAL"):
        raise OperatorError("REVIEW_EVIDENCE_MISSING", 409)
    with _canonical_transaction(
        repository, proposal["proposal_id"], series_access=series_access,
    ) as (document, snapshot):
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
                             issue_challenge: bool = True, series_access=None) -> dict:
    import secrets
    import time
    from app.p20_core.local_operator import OperatorError
    with _canonical_transaction(
        repository, proposal_id, series_access=series_access,
    ) as (document, snapshot):
        if not document:
            raise OperatorError("PROPOSAL_NOT_FOUND", 404)
        record = document["versions"][document["current_version"]]
        proposal = record["proposal"]
        _validate_review_proposal(repository, proposal, series_access=series_access)
        if record.get("receipt"):
            return {"proposal": proposal, "impact": record["impact"], "initial_guard": record["initial_guard"],
                    "challenge": None, "decision": record["decision"], "canonical_commit": True,
                    "receipt": record["receipt"]}
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


def record_operator_decision(repository, proposal_id: str, identity, request: dict,
                             *, series_access=None) -> dict:
    import time
    from uuid import uuid4
    from app.p20_core.local_operator import OperatorError, now_iso
    with _canonical_transaction(
        repository, proposal_id, series_access=series_access,
    ) as (document, snapshot):
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
        _validate_review_proposal(repository, proposal, series_access=series_access)
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
        if "pipeline" in record or proposal.get("source_kind") == "RESEARCH":
            proposal["status"] = "APPROVED_FOR_COMMIT" if request["decision"] == "APPROVE" else "REJECTED"
        # Decision evidence only. No transition to COMMITTED or bypass of the domain guard.
        return evidence

def _pipeline_evidence(record: dict) -> None:
    """Reconstruct typed upstream evidence; a seeded review alone grants no authority."""
    from app.p20_core.memory_extraction import (
        SceneMemorySource, StructuredMemoryExtractionCandidate, ModelInvocation,
        decode_memory_entities, verify_memory_extraction_candidate,
    )
    from app.p20_core.local_operator import OperatorError
    proposal = record["proposal"]
    evidence = record.get("pipeline")
    if not evidence:
        raise OperatorError("PIPELINE_EVIDENCE_REQUIRED", 409)
    source = evidence["source"]
    if (sha256_text(source["text"]) != proposal["source_artifact_hash"]
            or source["artifact_ref"] != proposal["source_artifact_ref"]):
        raise OperatorError("SOURCE_EVIDENCE_MISMATCH", 409)
    candidate = StructuredMemoryExtractionCandidate.from_source(
        candidate_id=proposal["extraction_candidate_set_id"],
        source=SceneMemorySource.from_text(project_id=proposal["project_id"],
            source_scene_id=proposal["source_scene_id"], source_artifact_ref=source["artifact_ref"],
            source_text=source["text"]), extraction_attempt=evidence["attempt"],
        candidate_records=decode_memory_entities(evidence["extractor"]["result"]["records"]),
        extractor_call=ModelInvocation(**evidence["extractor"]["invocation"]),
        created_at=evidence["candidate_created_at"], scene_quality_status="ACCEPT")
    verification = verify_memory_extraction_candidate(candidate,
        source=SceneMemorySource(proposal["project_id"], proposal["source_scene_id"],
                                 source["artifact_ref"], proposal["source_artifact_hash"]),
        verifier_call=ModelInvocation(**evidence["verifier"]["invocation"]),
        **evidence["verifier"]["result"])
    if (candidate.candidate_hash != proposal["extraction_candidate_hash"]
            or verification.to_dict() != evidence["verification"]
            or _evidence_hash(verification.to_dict()) != proposal["verification_ref"]["hash"]
            or verification.decision.value != "ACCEPT" or verification.escalation_required):
        raise OperatorError("VERIFICATION_EVIDENCE_MISMATCH", 409)
    states = {str(entity.record_id): entity.to_dict() for entity in candidate.memory_entities}
    if states != {m["target_entity_id"]: m["proposed_state"] for m in proposal["proposed_mutations"]}:
        raise OperatorError("CANDIDATE_MUTATION_MISMATCH", 409)
    for call, role in ((evidence["extractor"], "EXTRACTOR"), (evidence["verifier"], "VERIFIER")):
        if (call["input"]["project_id"] != proposal["project_id"]
                or call["input"]["book_id"] != proposal["book_id"]
                or call["input"]["series_id"] != proposal["series_id"]
                or call["invocation"]["role"] != role or call["input"]["source"] != source
                or call["invocation"]["metadata"]["context_hash"] != call["input"]["context_hash"]):
            raise OperatorError("MODEL_EVIDENCE_BINDING_MISMATCH", 409)
    if evidence["verifier"]["input"]["candidate"] != candidate.to_dict():
        raise OperatorError("VERIFIER_INPUT_MISMATCH", 409)


def _validate_canonical_record_set(proposal: dict, snapshot: dict) -> None:
    """Existing typed validation plus references resolved against the whole target set."""
    from app.p20_core.memory_extraction import decode_memory_entities
    from app.p20_core.local_operator import OperatorError
    states = {r["record_id"]: json.loads(r["payload_json"]) for r in snapshot["records"]}
    states.update({m["target_entity_id"]: m["proposed_state"] for m in proposal["proposed_mutations"]})
    known = set(states) | {proposal.get("source_scene_id"), proposal["project_id"], proposal["book_id"]}
    # A persisted CharacterState binds its stable character identity to this project.
    character_states = {r["record_id"] for r in snapshot["records"] if r["record_type"] == "CHARACTER_STATE"}
    character_states.update(m["target_entity_id"] for m in proposal["proposed_mutations"] if m["target_entity_type"] == "CHARACTER_STATE")
    known.update(states[identity]["character_id"] for identity in character_states)
    reference_fields = {
        "FACT": ("subject_id", "object_id", "established_event_id", "established_scene_id"),
        "CHARACTER_STATE": ("character_id", "source_scene_id", "source_event_id"),
        "EVENT": ("location_id", "participant_ids", "cause_refs", "effect_refs", "source_scene_id"),
        "KNOWLEDGE_EVENT": ("character_id", "fact_id", "event_id", "learned_at_scene_id", "learned_at_event_id", "learned_from_character_id"),
        "THREAD": ("opened_scene_id", "closed_scene_id", "actual_payoff_ref"),
        "SETUP": ("created_scene_id", "actual_payoff_scene_id"),
        "PAYOFF": ("setup_id", "completed_scene_id"),
        "RELATIONSHIP_CHANGE": ("subject_character_id", "object_character_id", "source_scene_id"),
    }
    entities = decode_memory_entities([{"record_type": m["target_entity_type"], "payload": m["proposed_state"]}
                                      for m in proposal["proposed_mutations"]])
    for mutation, entity in zip(proposal["proposed_mutations"], entities):
        if str(entity.project_id) != proposal["project_id"] or str(entity.record_id) != mutation["target_entity_id"]:
            raise OperatorError("CANONICAL_RECORD_SCOPE_MISMATCH", 409)
        for name in reference_fields[entity.memory_record_type]:
            value = getattr(entity, name)
            values = value if isinstance(value, (tuple, list)) else (value,)
            if any(v is not None and str(v) not in known for v in values):
                raise OperatorError("CANONICAL_REFERENCE_UNRESOLVED", 409)


def _canonical_impact(repository, proposal: dict, snapshot: dict, *, series_access=None) -> dict:
    from app.p20_core.impact_analysis import analyze_impact, ImpactRequest
    from app.p20_core.domain_records import DomainId
    # N edges bound every simple path. Above this budget analysis must fail closed.
    depth = max(1, len(snapshot["edges"]) + 1)
    from app.p20_core.local_operator import OperatorError
    if depth > 10000:
        raise OperatorError("IMPACT_COVERAGE_INSUFFICIENT", 409)
    results = [analyze_impact(
        repository,
        ImpactRequest(
            proposal["project_id"], proposal["scope_type"], proposal["scope_id"],
            DomainId.parse(m["target_entity_id"]).namespace.name,
            m["target_entity_id"], m["operation_type"],
        ),
        max_depth=depth,
        max_edges=10000,
        series_access=series_access,
    ).to_dict() for m in proposal["proposed_mutations"]]
    if any(item["impact_class"] == "DERIVED" for result in results for item in result["impacts"]):
        raise OperatorError("DERIVED_REBUILD_UNSUPPORTED", 409)
    binding = {k: proposal[k] for k in ("proposal_id", "proposal_hash", "project_id", "scope_type", "scope_id", "proposal_version")}
    return dict(binding, impact_id="impact-" + proposal["proposal_hash"], result=results,
                basis_hash=_evidence_hash(snapshot), policy_version=1,
                coverage=("BOUNDED_PROJECT_GRAPH" if proposal["scope_type"] == "PROJECT"
                          else "BOUNDED_SERIES_GRAPH"),
                result_hash=_evidence_hash({"results": results}),
                created_at=utc_now_iso())


def commit_canonical_proposal(repository, proposal_id: str, *, expected_hash: str,
                              identity=None, series_access=None) -> dict:
    """Final validation and canonical write share one scope-local transaction."""
    from app.p20_core.local_operator import OperatorError
    from app.p20_core.domain_mutation_guard import DomainMutationGuard
    with _canonical_transaction(
        repository, proposal_id, series_access=series_access, include_writer=True,
    ) as (document, snapshot, conn):
        if not document:
            raise OperatorError("PROPOSAL_NOT_FOUND", 404)
        record = document["versions"][document["current_version"]]
        proposal = record["proposal"]
        if proposal["proposal_hash"] != expected_hash:
            raise OperatorError("PROPOSAL_HASH_MISMATCH", 409)
        if record.get("receipt"):
            return record["receipt"]
        if proposal["status"] in {"STALE", "REJECTED", "FAILED"}:
            return {"status": proposal["status"], "canonical_commit": False, "proposal_id": proposal_id}
        _validate_review_proposal(repository, proposal, series_access=series_access)
        binding = {
            key: proposal[key]
            for key in ("proposal_id", "proposal_hash", "project_id", "scope_type", "scope_id")
        }
        impact = record.get("impact")
        initial_guard = record.get("initial_guard")
        if (
            not isinstance(impact, dict)
            or not isinstance(initial_guard, dict)
            or any(evidence.get(key) != value for evidence in (impact, initial_guard)
                   for key, value in binding.items())
            or not impact.get("impact_id")
            or not impact.get("result")
        ):
            raise OperatorError("REVIEW_EVIDENCE_MISSING", 409)
        metadata_table = "series_metadata" if proposal["scope_type"] == "SERIES" else "project_metadata"
        if proposal.get("source_kind") == "RESEARCH":
            from app.p20_core.research import validate_promotion_evidence
            try:
                validate_promotion_evidence(proposal, snapshot["research"])
            except ValueError as exc:
                proposal["status"] = "STALE"
                record["failure"] = str(exc)
                return {"status": "STALE", "reason": str(exc), "canonical_commit": False}
        else:
            _pipeline_evidence(record)
            source_key = proposal["source_artifact_ref"].removeprefix(
                "series_metadata:" if proposal["scope_type"] == "SERIES" else "project_metadata:"
            ).removesuffix("#source")
            source_row = conn.execute(
                f"SELECT value FROM {metadata_table} WHERE key=?", (source_key,)
            ).fetchone()
            if source_row is None or json.loads(source_row["value"])["source"] != record["pipeline"]["source"]:
                raise OperatorError("SOURCE_VERSION_UNAVAILABLE", 409)
        _validate_canonical_record_set(proposal, snapshot)
        try:
            _validate_review_basis(proposal, snapshot)
            if record["basis_hash"] != _evidence_hash(snapshot):
                raise OperatorError("STALE_PROPOSAL_ANALYSIS", 409)
        except OperatorError as exc:
            proposal["status"] = "STALE"
            record["failure"] = exc.code
            return {"status": "STALE", "reason": exc.code, "canonical_commit": False, "proposal_id": proposal_id}
        approval = record["decision"]
        if approval is not None:
            if (identity is None or any(approval.get(k) != v for k, v in identity.to_dict().items())
                    or approval["impact_result_hash"] != _evidence_hash(impact)):
                raise OperatorError("COMMIT_OPERATOR_BINDING_MISMATCH", 403)
        guard = DomainMutationGuard().evaluate_canonical(
            proposal=proposal, snapshot=snapshot, impact=impact, approval=approval)
        record["final_guard"] = guard
        if guard["outcome"] != "ALLOW":
            proposal["status"] = "AWAITING_USER_APPROVAL" if guard["outcome"] == "REQUIRE_USER_APPROVAL" else "REJECTED"
            return {"status": proposal["status"], "canonical_commit": False, "proposal_id": proposal_id,
                    "proposal_hash": expected_hash, "guard": guard}
        # Repository repeats the same guard at the physical write boundary.
        from app.p20_core.project_repository import SeriesRepository
        if isinstance(repository, SeriesRepository):
            repository.apply_canonical_record_set(
                series_access, conn, proposal, snapshot, impact=impact, approval=approval,
            )
        else:
            repository.apply_canonical_record_set(
                conn, proposal, snapshot, impact=impact, approval=approval,
            )
        affected = sorted({item["entity_id"] for result in impact["result"] for item in result["impacts"]})
        receipt = {"status": "COMMITTED", "canonical_commit": True,
            **{k: proposal[k] for k in ("proposal_id", "proposal_hash", "project_id", "scope_type", "scope_id", "run_id", "step_id")},
            "operation_id": _evidence_hash({k: proposal[k] for k in ("project_id", "scope_type", "scope_id", "proposal_id", "proposal_hash")}),
            "resulting_versions": {m["target_entity_id"]: m["proposed_state"]["version"] for m in proposal["proposed_mutations"]},
            "authorization_ref": None if approval is None else approval["authorization_ref"],
            "impact_id": impact["impact_id"], "guard": guard,
            "invalidation": {"affected_ids": affected, "context_packages": "HISTORICAL_ONLY", "rebuild": "NEXT_CONTEXT_BUILD"},
            "created_at": utc_now_iso()}
        proposal["status"] = "COMMITTED"
        record["receipt"] = receipt
        if proposal.get("source_kind") == "RESEARCH":
            from app.p20_core.research import AuthorDecision
            state = repository.read_research_state(conn)
            for mutation in proposal["proposed_mutations"]:
                claim_id = mutation["provenance"]["claim_id"]
                fiction = proposal["fiction_decision"]
                decision = AuthorDecision(
                    decision_id="DECISION-" + _evidence_hash([proposal_id, proposal["proposal_hash"], claim_id])[:32],
                    scope_id=proposal["scope_id"], subject_id=claim_id,
                    decision_type="FICTION" if fiction else "CANONICAL_PROMOTION",
                    decision=mutation["proposed_state"]["reality_status"],
                    reason=fiction["reason"] if fiction else "Explicit approval of verified research promotion",
                    impact_refs=[impact["impact_id"]], created_at=receipt["created_at"],
                    created_by=approval["operator_id"], proposal_hash=proposal["proposal_hash"],
                    evidence_hash=mutation["provenance"]["claim_hash"],
                    target_fact_id=mutation["target_entity_id"],
                    target_state_hash=mutation["provenance"]["state_hash"],
                ).model_dump()
                state["decisions"][decision["decision_id"]] = decision
                if fiction:
                    for conflict in state["conflicts"].values():
                        if conflict["entity_id"] == claim_id:
                            conflict.update(status="RESOLVED", resolution=fiction["reason"],
                                            resolved_by=decision["decision_id"], resolved_at=receipt["created_at"])
            repository.write_research_state(conn, state)
        # This metadata is the durable commit audit, atomic with versions and state.
        conn.execute(
            f"INSERT INTO {metadata_table}(key,value) VALUES (?,?)",
            ("canonical_commit.v1:" + receipt["operation_id"], json.dumps(receipt, sort_keys=True)),
        )
        return receipt


def prepare_research_proposal(repository, *, proposal_id: str, operation_id: str,
                              target_fact_ids: dict, fiction_decision=None) -> dict:
    """Freeze a PROJECT research proposal; never grant approval or commit."""
    from app.p20_core.research import verified_evidence, validate_promotion_evidence, digest
    from app.p20_core.domain_records import FactRecord
    from app.p20_core.domain_mutation_guard import DomainMutationGuard
    from app.p20_core.local_operator import OperatorError
    with _canonical_transaction(repository, proposal_id, include_writer=True) as (document, snapshot, conn):
        state = repository.read_research_state(conn)
        snapshot["research"] = state
        evidence = verified_evidence(state, operation_id)
        request_hash = _evidence_hash({"operation_id": operation_id, "targets": target_fact_ids,
                                      "fiction": fiction_decision, "evidence": digest(evidence)})
        if document:
            current_record = document["versions"][document["current_version"]]
            if (current_record.get("research_request_hash") == request_hash
                    and (current_record.get("receipt") or (
                        current_record["proposal"]["status"] not in {"STALE", "REJECTED", "FAILED"}
                        and current_record["basis_hash"] == _evidence_hash(snapshot)))):
                return current_record["proposal"]
            # An explicit new source/assessment under the same proposal ID is
            # a new version, preserving the old decision and receipt.
            if current_record["proposal"]["status"] != "COMMITTED":
                current_record["proposal"]["status"] = "STALE"
            proposal_version = int(document["current_version"]) + 1
        else:
            proposal_version = 1
        op = evidence["operation"]
        claims = op["result"]["claims"]
        if set(target_fact_ids) != {c["claim_id"] for c in claims}:
            raise OperatorError("RESEARCH_SET_MAPPING_REQUIRED", 422)
        current = {r["record_id"]: json.loads(r["payload_json"]) for r in snapshot["records"]}
        mutations = []
        created_at = utc_now_iso()
        for claim in claims:
            identity = target_fact_ids[claim["claim_id"]]
            old = current.get(identity)
            fact = FactRecord(fact_id=identity, project_id=repository.scope.scope_id,
                subject_id=identity, predicate="research_assertion", object_type="TEXT", object_id=None,
                object_value=claim["claim"], reality_status="REAL_VERIFIED" if fiction_decision is None else fiction_decision["reality_status"],
                verification_status=claim["verification_status"], confidence=claim["confidence"],
                frozen=False if old is None else old.get("frozen"), author_locked=False if old is None else old.get("author_locked"),
                valid_from=None, valid_to=None, established_event_id=None, established_scene_id=None,
                source_artifact_ref="project.db#research.v1/operations/" + operation_id,
                source_refs=[f"{r['source_id']}:v{r['version']}:{r['content_hash']}" for r in claim["source_refs"]],
                canon_version=1 if old is None else old["canon_version"] + 1,
                version=1 if old is None else old["version"] + 1,
                created_at=created_at if old is None else old["created_at"], updated_at=created_at).to_dict()
            mutations.append(dict(target_entity_type="FACT", target_entity_id=identity,
                operation_type="CREATE" if old is None else "UPDATE", expected_current_version=None if old is None else old["version"],
                expected_current_hash=None if old is None else _evidence_hash(old), proposed_state=fact,
                provenance={"claim_id": claim["claim_id"], "claim_hash": digest(claim), "state_hash": digest(fact)}))
        invocation = op["invocation"]
        proposal = dict(contract_version="2.0", source_kind="RESEARCH", proposal_id=proposal_id,
            project_id=repository.scope.scope_id, book_id=repository.context.book_id, series_id=None,
            scope_type="PROJECT", scope_id=repository.scope.scope_id,
            run_id=invocation["run_id"], step_id=invocation["step_id"],
            context_package_id=invocation["context_package_id"], context_hash=invocation["context_hash"],
            research_evidence={"operation_id": operation_id, "hash": digest(evidence)},
            fiction_decision=fiction_decision, source="RESEARCH_CLAIM", actor_ref=invocation["call_id"],
            authority_ref="P20_VERIFIED_RESEARCH_V1", policy_ref="CANONICAL_CHANGE_V1",
            proposal_version=proposal_version, proposal_hash="", status="READY_FOR_ANALYSIS", created_at=created_at,
            proposed_mutations=sorted(mutations, key=lambda m: (m["target_entity_type"], m["target_entity_id"], m["operation_type"])))
        proposal["proposal_hash"] = canonical_proposal_hash(proposal)
        _validate_review_proposal(repository, proposal)
        validate_promotion_evidence(proposal, state)
        _validate_canonical_record_set(proposal, snapshot)
        impact = _canonical_impact(repository, proposal, snapshot)
        guard = DomainMutationGuard().evaluate_canonical(proposal=proposal, snapshot=snapshot, impact=impact)
        if guard["outcome"] != "REQUIRE_USER_APPROVAL":
            raise OperatorError("RESEARCH_PROMOTION_DENIED:" + guard["reason"], 409)
        proposal["status"] = "AWAITING_USER_APPROVAL"
        document.setdefault("versions", {})[str(proposal_version)] = dict(proposal=proposal,
            research_request_hash=request_hash, basis_hash=_evidence_hash(snapshot),
            impact=impact, initial_guard=guard, challenges={}, decision=None)
        document["current_version"] = str(proposal_version)
        return proposal


def process_accepted_artifact(*, execution_context, text: str, source_trace: dict,
                              context_sources: dict, requested_model: str | None,
                              effective_model: str, scope_type: str = "PROJECT") -> dict:
    """Active P20 producer. Persist source/calls, then freeze, analyze and guard."""
    from dataclasses import replace
    from app.p20_core.project_repository import (
        ProjectRepository, SeriesAccessContext, SeriesRepository, StorageResolver,
    )
    from app.p20_core.executor import MemoryModelInvocationError, invoke_memory_model
    from app.p20_core.memory_extraction import (
        SceneMemorySource, StructuredMemoryExtractionCandidate, ModelInvocation,
        decode_memory_entities, verify_memory_extraction_candidate, DEFAULT_MEMORY_EXTRACTION_POLICY,
    )
    from app.p20_core.local_operator import OperatorError
    from app.p20_core.domain_mutation_guard import DomainMutationGuard
    scope_type = str(scope_type).upper()
    series_access = None
    if scope_type == "PROJECT":
        repository = ProjectRepository(StorageResolver().resolve_project(
            execution_context.project_id, book_id=execution_context.book_id))
        scope_id = execution_context.project_id
        metadata_prefix = "project_metadata:"
    elif scope_type == "SERIES" and execution_context.series_id:
        series_access = SeriesAccessContext.bind(
            execution_context.project_id, execution_context.series_id,
        )
        repository = SeriesRepository(
            StorageResolver().resolve_series(execution_context.series_id)
        )
        try:
            repository.require_registered_member(
                series_access, book_id=execution_context.book_id,
            )
        except (ValueError, sqlite3.Error):
            return {"status": "FAILED", "canonical_commit": False,
                    "reason": "SERIES_ACCESS_DENIED"}
        scope_id = execution_context.series_id
        metadata_prefix = "series_metadata:"
    else:
        return {"status": "FAILED", "canonical_commit": False,
                "reason": "INVALID_CANONICAL_SCOPE"}
    operation = _evidence_hash({"project": execution_context.project_id, "book": execution_context.book_id,
        "run": execution_context.run_id, "step": execution_context.step_id, "source_hash": sha256_text(text),
        "source_version": 1, "scope_type": scope_type, "scope_id": scope_id,
        "operation": "CANONICAL_PROMOTION"})
    proposal_id = "proposal-" + operation
    cached = repository.get_metadata("canonical_proposal.v1:" + proposal_id)
    if cached:
        record = json.loads(cached)["versions"]["1"]
        if record.get("receipt"):
            return record["receipt"]
        return {"status": record["proposal"]["status"], "proposal_id": proposal_id,
                "proposal_hash": record["proposal"]["proposal_hash"], "canonical_commit": False}
    with _pipeline_transaction(
        repository, operation, series_access=series_access,
    ) as state:
        if state.get("result"):
            return state["result"]
        if state.get("started"):
            return {"status": "FAILED", "reason": "PIPELINE_OUTCOME_REQUIRES_REVIEW", "canonical_commit": False}
        source_ref = metadata_prefix + "canonical_pipeline.v1:" + operation + "#source"
        source = {"text": text, "artifact_ref": source_ref, "artifact_id": "artifact-" + operation,
                  "version": 1, "hash": sha256_text(text), "scene_id": "SCENE-" + operation,
                  "accepted_step_artifact": source_trace["artifact_path"],
                  "context_package_id": source_trace["context_package_id"], "context_hash": source_trace["context_hash"]}
        state.update(started=True, source=source, attempts=[])
    try:
        for attempt in range(1, DEFAULT_MEMORY_EXTRACTION_POLICY.max_attempts + 1):
            source_contract = SceneMemorySource.from_text(project_id=execution_context.project_id,
                source_scene_id=source["scene_id"], source_artifact_ref=source_ref, source_text=text)
            extractor = invoke_memory_model(execution_context=replace(execution_context,
                step_id=execution_context.step_id + f":canonical:{operation}:extract:{attempt}", technical_retry=False),
                role="EXTRACTOR", requested_model=requested_model, effective_model=effective_model,
                source=source, candidate=None, context_sources=context_sources,
                model_routing=source_trace.get("model_routing"))
            with _pipeline_transaction(
                repository, operation, series_access=series_access,
            ) as state:
                state["last_extractor"] = extractor
            candidate_time = utc_now_iso()
            candidate = StructuredMemoryExtractionCandidate.from_source(candidate_id="candidate-" + operation + f"-{attempt}",
                source=source_contract, extraction_attempt=attempt,
                candidate_records=decode_memory_entities(extractor["result"]["records"]),
                extractor_call=ModelInvocation(**extractor["invocation"]), created_at=candidate_time,
                scene_quality_status="ACCEPT")
            verifier = invoke_memory_model(execution_context=replace(execution_context,
                step_id=execution_context.step_id + f":canonical:{operation}:verify:{attempt}", technical_retry=False),
                role="VERIFIER", requested_model=requested_model, effective_model=effective_model,
                source=source, candidate=candidate.to_dict(), context_sources=context_sources,
                model_routing=source_trace.get("model_routing"))
            with _pipeline_transaction(
                repository, operation, series_access=series_access,
            ) as state:
                state["last_verifier"] = verifier
            verification = verify_memory_extraction_candidate(candidate, source=source_contract,
                verifier_call=ModelInvocation(**verifier["invocation"]), **verifier["result"])
            evidence = {"source": source, "extractor": extractor, "verifier": verifier,
                "candidate_created_at": candidate_time, "attempt": attempt, "verification": verification.to_dict()}
            with _pipeline_transaction(
                repository, operation, series_access=series_access,
            ) as state:
                state["attempts"].append(evidence)
            if verification.decision.value == "ACCEPT":
                break
            if verification.decision.value == "REJECT" or verification.escalation_required:
                result = {"status": verification.memory_extraction_status.value,
                          "canonical_commit": False, "verification": verification.to_dict()}
                with _pipeline_transaction(
                    repository, operation, series_access=series_access,
                ) as state:
                    state["result"] = result
                return result
        if any(type(getattr(entity, flag, None)) is not bool for entity in candidate.memory_entities
               for flag in ("frozen", "author_locked")):
            raise OperatorError("CANONICAL_RECORD_PROTECTION_REQUIRED", 409)
        with _canonical_transaction(
            repository, proposal_id, series_access=series_access,
        ) as (document, snapshot):
            current = {(r["record_type"], r["record_id"]): json.loads(r["payload_json"]) for r in snapshot["records"]}
            mutations = []
            for entity in candidate.memory_entities:
                old = current.get((entity.memory_record_type, str(entity.record_id)))
                mutations.append(dict(target_entity_type=entity.memory_record_type, target_entity_id=str(entity.record_id),
                    operation_type="CREATE" if old is None else "UPDATE", expected_current_version=None if old is None else old["version"],
                    expected_current_hash=None if old is None else _evidence_hash(old), proposed_state=entity.to_dict(),
                    provenance={"candidate_id": candidate.candidate_id, "candidate_hash": candidate.candidate_hash,
                        "source_hash": source["hash"], "source_version": 1, "source_scene_id": source["scene_id"]}))
            proposal = dict(contract_version="1.0", proposal_id=proposal_id, project_id=execution_context.project_id,
                book_id=execution_context.book_id, series_id=execution_context.series_id,
                scope_type=scope_type, scope_id=scope_id,
                run_id=execution_context.run_id, step_id=execution_context.step_id,
                source_artifact_id=source["artifact_id"], source_artifact_ref=source_ref, source_artifact_hash=source["hash"],
                source_artifact_version=1, source_scene_id=source["scene_id"], context_package_id=source["context_package_id"],
                context_hash=source["context_hash"], extraction_candidate_set_id=candidate.candidate_id,
                extraction_candidate_hash=candidate.candidate_hash, verification_ref={"id": "verification-" + operation,
                "hash": _evidence_hash(verification.to_dict())}, proposed_mutations=sorted(mutations, key=lambda m: (m["target_entity_type"], m["target_entity_id"], m["operation_type"])),
                source="AUTOMATION", actor_ref=execution_context.operation_id, authority_ref="P20_VERIFIED_EXTRACTION_V1",
                policy_ref="CANONICAL_CHANGE_V1", proposal_version=1, proposal_hash="", status="READY_FOR_ANALYSIS", created_at=utc_now_iso())
            proposal["proposal_hash"] = canonical_proposal_hash(proposal)
            _validate_review_proposal(repository, proposal, series_access=series_access)
            _validate_canonical_record_set(proposal, snapshot)
            document.update(current_version="1", versions={"1": {"proposal": proposal, "pipeline": evidence,
                "basis_hash": _evidence_hash(snapshot), "challenges": {}, "decision": None}})
        # Frozen proposal exists before analysis. A new snapshot must match its basis.
        with _canonical_transaction(
            repository, proposal_id, series_access=series_access,
        ) as (document, snapshot):
            record = document["versions"]["1"]
            if record["basis_hash"] != _evidence_hash(snapshot):
                raise OperatorError("STALE_PROPOSAL_ANALYSIS", 409)
            proposal = record["proposal"]
            impact = _canonical_impact(
                repository, proposal, snapshot, series_access=series_access,
            )
            guard = DomainMutationGuard().evaluate_canonical(proposal=proposal, snapshot=snapshot, impact=impact)
            record.update(impact=impact, initial_guard=guard)
            proposal["status"] = {"ALLOW": "APPROVED_FOR_COMMIT", "REQUIRE_USER_APPROVAL": "AWAITING_USER_APPROVAL", "DENY": "REJECTED"}[guard["outcome"]]
        return commit_canonical_proposal(
            repository, proposal_id, expected_hash=proposal["proposal_hash"],
            series_access=series_access,
        )
    except (ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error) as exc:
        # No model exception text (which may echo inputs/secrets) enters the audit.
        result = {"status": "FAILED", "canonical_commit": False,
                  "reason": (exc.failure_reason if isinstance(exc, MemoryModelInvocationError)
                             else exc.code if isinstance(exc, OperatorError)
                             else type(exc).__name__)}
        with _canonical_transaction(
            repository, proposal_id, series_access=series_access,
        ) as (document, snapshot):
            if document:
                record = document["versions"]["1"]
                if record.get("receipt"):
                    return record["receipt"]
                record["proposal"]["status"] = "FAILED"
                record["failure"] = result["reason"]
        with _pipeline_transaction(
            repository, operation, series_access=series_access,
        ) as state:
            if isinstance(exc, MemoryModelInvocationError):
                role = exc.evidence["invocation"]["role"].lower()
                state["last_" + role] = exc.evidence
            state["result"] = result
        return result


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
    # Windows can briefly deny replacement of a just-written file. Retry only
    # this idempotent rename, keeping the completed temp file and a strict bound.
    import time
    for attempt in range(5):
        try:
            tmp.replace(path)
            break
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32} or attempt == 4:
                raise
            time.sleep(0.01 * (2 ** attempt))


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
    """Register accepted chapter metadata only; never promote extracted domain state.

    Canonical structured records are owned by commit_canonical_proposal. Fields
    such as facts/proposed_mutations in a chapter are deliberately not imported.
    """
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
    chapter_lineage: Optional[Dict[str, Any]] = None,
    adaptive_style: Optional[Dict[str, Any]] = None,
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
        "chapter_lineage": chapter_lineage,
        "adaptive_style": adaptive_style,
        "engine": APP_VERSION,
    }
    if context_packages:
        audit_doc["context_package_id"] = context_packages[-1].get(
            "context_package_id"
        )
        audit_doc["context_hash"] = context_packages[-1].get("context_hash")

    if project_id and domain_book_id:
        from app.p20_core.model_provenance import public_trace
        from app.p20_core.project_repository import ProjectRepository, StorageResolver
        repository = ProjectRepository(StorageResolver().resolve_project(project_id, book_id=domain_book_id))
        audit_doc["model_provenance"] = public_trace(repository, run_id=run_id)

    append_jsonl(
        book_dir / "audit" / "audit_log.jsonl",
        audit_doc,
    )

    run_dir = get_runs_root() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    json_write(run_dir / "audit.json", audit_doc)

"""Closed Memory Ledger v1 record codec and SQLite persistence primitives.

This module deliberately owns neither a database nor an application workflow.
ProjectRepository and SeriesRepository pass their already-open owner connection
to these functions so a ledger append participates in the domain transaction.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping


MEMORY_LEDGER_CONTROL_KEY = "memory_ledger_control.v1"
MEMORY_LEDGER_SCHEMA_VERSION = 1
MEMORY_LEDGER_COVERAGE = "MEMORY_PIPELINES_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_MEMORY_EVENT_ID_RE = re.compile(r"^MEV-[0-9a-f]{64}$")
_POSITIVE_DECIMAL_RE = re.compile(r"^[1-9][0-9]*$")
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_SCOPES = {"PROJECT", "SERIES"}
_NAMESPACES = {
    "CANONICAL_PIPELINE", "CANONICAL_PROPOSAL", "RESEARCH_COMMAND",
    "RESEARCH_OPERATION", "CROSS_STORE_OPERATION", "SERIES_VOLUME_OPERATION",
    "LEDGER_BOOTSTRAP", "LEDGER_CORRECTION",
}
_EVENT_TYPES = {
    "LEDGER_BASELINE_OBJECT", "LEDGER_ACTIVATED", "EXTRACTION_STARTED",
    "EXTRACTION_ATTEMPT_RECORDED", "EXTRACTION_FINISHED", "PROPOSAL_RECORDED",
    "PROPOSAL_STATE_RECORDED", "AUTHOR_DECISION_RECORDED",
    "CANONICAL_ENTITY_CHANGED", "CANONICAL_COMMITTED", "RESEARCH_COMMAND_RECORDED",
    "RESEARCH_RESULT_RECORDED", "ARTIFACT_WRITE_INTENDED", "SERIES_VOLUME_CLOSED",
    "ARTIFACT_WRITE_CONFIRMED", "CHAPTER_VERSION_RECORDED", "MEMORY_EVENT_CORRECTION",
}
_REFERENCE_KINDS = {
    "MEMORY_EVENT", "ENTITY_VERSION", "PROPOSAL", "APPROVAL", "ARTIFACT", "SOURCE",
    "EVALUATION", "MODEL_INVOCATION", "RECOVERY_OPERATION", "CONTEXT_PACKAGE",
    "SNAPSHOT", "BASELINE_OBJECT",
}
_BASELINE_CATEGORIES = {
    "PROJECT_STRUCTURED_MEMORY", "PROJECT_FACT", "PROJECT_CHARACTER_STATE",
    "PROJECT_EDGE", "PROJECT_METADATA", "SERIES_STATE_RECORD", "SERIES_EDGE",
    "SERIES_VOLUME_SNAPSHOT", "SERIES_MEMBERSHIP", "SERIES_OPERATION",
    "SERIES_METADATA",
}
_INTERNAL_LOCATOR_PREFIXES = (
    "metadata:", "table:", "project_metadata:", "series_metadata:",
    "context:", "canonical_commit.v1:", "series_operations:",
)
_ENTITY_ID_NAMESPACES = {
    "FACT": "FACT",
    "CHARACTER_STATE": "CONTEXT",
    "EVENT": "EVENT",
    "KNOWLEDGE_EVENT": "KNOWLEDGE",
    "THREAD": "THREAD",
    "SETUP": "SETUP",
    "PAYOFF": "PAYOFF",
    "RELATIONSHIP": "REL",
    "RELATIONSHIP_CHANGE": "REL",
}
_PROPOSAL_STATES = {
    "READY_FOR_ANALYSIS", "AWAITING_USER_APPROVAL", "APPROVED_FOR_COMMIT",
    "REJECTED", "COMMITTED", "STALE", "FAILED",
}
_CANONICAL_REASON_CODES = {
    "INVALID_CANONICAL_SCOPE", "IMPACT_REQUIRED", "RESEARCH_EVIDENCE_INVALID",
    "AUTHORITY_UNRESOLVED", "EXTRACTION_CONTRACT_MISMATCH", "POLICY_UNRESOLVED",
    "RESEARCH_CANONICAL_APPROVAL_REQUIRED", "CREATE_TARGET_EXISTS_OR_INVALID_BASE",
    "STALE_TARGET_BASE", "UNSUPPORTED_OPERATION", "RECORD_SCOPE_MISMATCH",
    "CURRENT_PROTECTION_UNKNOWN", "PROTECTION_DOWNGRADE", "SILENT_UNFREEZE_DENIED",
    "SILENT_UNLOCK_DENIED", "DOMAIN_ID_CHANGED", "VERSION_NOT_INCREMENTED",
    "PROTECTED_MUTATION", "INVALID_BOUND_APPROVAL", "FROZEN_RECORD",
    "AUTHOR_LOCKED_RECORD", "FROZEN_AND_AUTHOR_LOCKED_RECORD",
    "NEW_RESEARCH_EVIDENCE", "RESEARCH_EVIDENCE_STALE", "STALE_PROPOSAL_VERSION",
    "STALE_PROPOSAL_ANALYSIS", "UNKNOWN_FAILURE",
}
_RESEARCH_REASON_CODES = {
    "RESEARCH_MODEL_CONFIGURATION_MISSING", "RESEARCH_MODEL_TRANSPORT_FAILED",
    "RESEARCH_MODEL_REFUSED", "SOURCE_CONTENT_UNAVAILABLE", "STALE_RESEARCH_CLAIM",
    "OPERATOR_RECOVERY", "UNKNOWN_FAILURE",
}
_EXTRACTION_REASON_CODES = _CANONICAL_REASON_CODES | {
    "MODEL_CONFIGURATION_MISSING", "MODEL_TRANSPORT_FAILED", "MODEL_REFUSED",
}
_EVENT_REASON_CODES = {
    "EXTRACTION_ATTEMPT_RECORDED": _EXTRACTION_REASON_CODES,
    "EXTRACTION_FINISHED": _EXTRACTION_REASON_CODES,
    "PROPOSAL_STATE_RECORDED": _CANONICAL_REASON_CODES,
    "RESEARCH_RESULT_RECORDED": _RESEARCH_REASON_CODES,
}


class MemoryLedgerError(ValueError):
    pass


class MemoryLedgerIntegrityError(MemoryLedgerError):
    pass


class MemoryLedgerIdentityConflict(MemoryLedgerError):
    pass


class MemoryLedgerNotActive(MemoryLedgerError):
    pass


class MemoryLedgerMigrationRequired(MemoryLedgerError):
    pass


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MemoryLedgerError("JSON contains duplicate object key")
        result[key] = value
    return result


def parse_json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MemoryLedgerError("invalid ledger JSON") from exc
    if not isinstance(value, dict):
        raise MemoryLedgerError("ledger JSON must be an object")
    return value


def _validate_json(value: Any, field_name: str) -> None:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise MemoryLedgerError(f"{field_name} contains an invalid Unicode scalar") from exc
        return
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if not _INT64_MIN <= value <= _INT64_MAX:
            raise MemoryLedgerError(f"{field_name} integer is outside signed 64-bit range")
        return
    if isinstance(value, float):
        raise MemoryLedgerError(f"{field_name} must not contain float")
    if isinstance(value, list):
        for item in value:
            _validate_json(item, field_name)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise MemoryLedgerError(f"{field_name} object keys must be strings")
            _validate_json(item, field_name)
        return
    raise MemoryLedgerError(f"{field_name} must be JSON compatible")


def canonical_json(value: Any) -> str:
    _validate_json(value, "value")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        encoded.encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as exc:
        raise MemoryLedgerError("value cannot be canonicalized") from exc
    return encoded


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MemoryLedgerError(f"{field_name} must be a non-empty trimmed string")
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    return None if value is None else _required_text(value, field_name)


def _hash(value: Any, field_name: str) -> str:
    value = _required_text(value, field_name)
    if not _HASH_RE.fullmatch(value):
        raise MemoryLedgerError(f"{field_name} must be a lowercase SHA-256 hash")
    return value


def _sorted_unique_dicts(values: Iterable[Mapping[str, Any]], field_name: str) -> tuple[dict[str, Any], ...]:
    if isinstance(values, (str, bytes)) or values is None:
        raise MemoryLedgerError(f"{field_name} must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, Mapping):
            raise MemoryLedgerError(f"{field_name} entries must be objects")
        normalized = dict(item)
        key = canonical_json(normalized)
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return tuple(sorted(result, key=canonical_json))


def _validate_reference(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {"kind", "owner_scope_type", "owner_scope_id", "locator", "version", "hash_scheme", "hash"}
    if set(value) != expected:
        raise MemoryLedgerError("reference fields are closed")
    kind = _required_text(value["kind"], "reference.kind")
    if kind not in _REFERENCE_KINDS:
        raise MemoryLedgerError("unsupported reference kind")
    scope_type = _required_text(value["owner_scope_type"], "reference.owner_scope_type")
    if scope_type not in _SCOPES:
        raise MemoryLedgerError("unsupported reference owner scope")
    from app.p20_core.domain_records import DomainId, DomainNamespace

    owner_scope_id = _required_text(value["owner_scope_id"], "reference.owner_scope_id")
    try:
        DomainId.parse(owner_scope_id).require_namespace(
            DomainNamespace.PROJECT if scope_type == "PROJECT" else DomainNamespace.SERIES,
            "reference.owner_scope_id",
        )
    except ValueError as exc:
        raise MemoryLedgerError("reference owner scope identity is invalid") from exc
    locator = _validate_reference_locator(kind, value["locator"])
    result = {
        "kind": kind,
        "owner_scope_type": scope_type,
        "owner_scope_id": owner_scope_id,
        "locator": locator,
        "version": _optional_text(value["version"], "reference.version"),
        "hash_scheme": _optional_text(value["hash_scheme"], "reference.hash_scheme"),
        "hash": _optional_text(value["hash"], "reference.hash"),
    }
    if (result["hash"] is None) != (result["hash_scheme"] is None):
        raise MemoryLedgerError("reference hash and hash_scheme must be bound together")
    if result["hash"] is not None:
        _hash(result["hash"], "reference.hash")
    if result["hash"] is None and kind != "BASELINE_OBJECT":
        raise MemoryLedgerError("only baseline references may omit hash")
    return result


def _validate_reference_locator(kind: str, value: Any) -> str:
    text = _required_text(value, "reference.locator")
    if kind == "MEMORY_EVENT":
        if _MEMORY_EVENT_ID_RE.fullmatch(text) is None:
            raise MemoryLedgerError("memory event reference locator is invalid")
        return text
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise MemoryLedgerError("reference locator contains control characters")
    internal_prefix = next(
        (prefix for prefix in _INTERNAL_LOCATOR_PREFIXES if text.startswith(prefix)),
        None,
    )
    if internal_prefix is not None:
        suffix = text[len(internal_prefix):]
        if (
            not suffix
            or "/" in suffix
            or "\\" in suffix
            or re.match(r"^[A-Za-z]:", suffix)
            or any(part in {"", ".", ".."} for part in re.split(r"[/\\]", suffix))
        ):
            raise MemoryLedgerError("internal reference locator is invalid")
        if internal_prefix == "table:":
            match = re.fullmatch(r"([a-z][a-z0-9_]*):key:(\{.*\})", suffix)
            if match is None:
                raise MemoryLedgerError("table reference locator is invalid")
            key = parse_json_object(match.group(2))
            if not key or canonical_json(key) != match.group(2):
                raise MemoryLedgerError("table reference locator key is not canonical")
        return text
    scheme = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text)
    if (
        "\\" in text
        or re.match(r"^[A-Za-z]:", text)
        or scheme is not None
        or any(":" in part for part in text.split("/")[1:])
    ):
        raise MemoryLedgerError("reference locator must not be a system path or URI")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in text.split("/")):
        raise MemoryLedgerError("reference locator must be confined and normalized")
    return text


def _validate_baseline_locator(value: Any) -> str:
    text = _required_text(value, "baseline.locator")
    if text.startswith("metadata:"):
        suffix = text.removeprefix("metadata:")
        if not suffix or any(character in suffix for character in ("/", "\\", "\x00")):
            raise MemoryLedgerError("baseline metadata locator is invalid")
        return text
    match = re.fullmatch(r"table:([a-z][a-z0-9_]*):key:(\{.*\})", text)
    if match is None:
        raise MemoryLedgerError("baseline locator is outside the frozen registry")
    key = parse_json_object(match.group(2))
    if not key or canonical_json(key) != match.group(2):
        raise MemoryLedgerError("baseline table locator key is not canonical")
    return text


def _validate_entity_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"record_type", "entity_id", "version"}:
        raise MemoryLedgerError("entity reference fields are closed")
    version = value["version"]
    if version is not None and (not isinstance(version, int) or isinstance(version, bool) or version < 1):
        raise MemoryLedgerError("entity reference version must be positive or null")
    record_type = _required_text(value["record_type"], "entity_ref.record_type")
    entity_id = _required_text(value["entity_id"], "entity_ref.entity_id")
    try:
        from app.p20_core.domain_records import DomainId, DomainNamespace
        namespace = DomainNamespace(_ENTITY_ID_NAMESPACES[record_type])
        DomainId.parse(entity_id).require_namespace(namespace, "entity_ref.entity_id")
    except (KeyError, ValueError) as exc:
        raise MemoryLedgerError("entity reference identity is invalid") from exc
    return {"record_type": record_type, "entity_id": entity_id, "version": version}


def _validate_payload(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise MemoryLedgerError("structured_payload must be an object")
    result = dict(payload)
    _validate_json(result, "structured_payload")
    shapes: dict[str, tuple[set[str], set[str]]] = {
        "LEDGER_BASELINE_OBJECT": (
            {"category", "locator", "bytes_hash", "snapshot_text", "immutable_ref",
             "observed_schema_version", "history_completeness"},
            {"category", "locator", "bytes_hash", "observed_schema_version", "history_completeness"},
        ),
        "LEDGER_ACTIVATED": (
            {"baseline_count", "manifest_hash", "coverage", "origin"},
            {"baseline_count", "manifest_hash", "coverage", "origin"},
        ),
        "EXTRACTION_STARTED": (
            {"phase", "attempt_ordinal", "source_refs", "outcome"},
            {"phase", "attempt_ordinal", "source_refs", "outcome"},
        ),
        "EXTRACTION_ATTEMPT_RECORDED": (
            {"phase", "attempt_ordinal", "source_refs", "candidate_refs", "verification_refs",
             "invocation_refs", "outcome", "reason_code"},
            {"phase", "attempt_ordinal", "source_refs", "candidate_refs", "verification_refs",
             "invocation_refs", "outcome"},
        ),
        "EXTRACTION_FINISHED": (
            {"phase", "attempt_ordinal", "candidate_refs", "verification_refs", "outcome", "reason_code"},
            {"phase", "attempt_ordinal", "outcome"},
        ),
        "PROPOSAL_RECORDED": (
            {"phase", "proposal_id", "proposal_version", "proposal_hash", "new_state",
             "basis_hash", "outcome"},
            {"phase", "proposal_id", "proposal_version", "proposal_hash", "new_state",
             "basis_hash", "outcome"},
        ),
        "PROPOSAL_STATE_RECORDED": (
            {"phase", "proposal_id", "proposal_version", "proposal_hash", "previous_state",
             "new_state", "basis_hash", "impact_refs", "guard_refs", "outcome", "reason_code"},
            {"phase", "proposal_id", "proposal_version", "proposal_hash", "previous_state",
             "new_state", "basis_hash", "outcome"},
        ),
        "AUTHOR_DECISION_RECORDED": (
            {"approval_id", "authorization_ref", "proposal_id", "proposal_version",
             "proposal_hash", "decision", "outcome"},
            {"approval_id", "authorization_ref", "proposal_id", "proposal_version",
             "proposal_hash", "decision", "outcome"},
        ),
        "CANONICAL_ENTITY_CHANGED": (
            {"proposal_id", "proposal_version", "proposal_hash", "entity_type", "entity_id",
             "old_version", "old_hash", "new_version", "new_hash", "commit_operation_ref",
             "outcome", "world_time", "narrative_order"},
            {"proposal_id", "proposal_version", "proposal_hash", "entity_type", "entity_id",
             "old_version", "old_hash", "new_version", "new_hash", "commit_operation_ref", "outcome"},
        ),
        "CANONICAL_COMMITTED": (
            {"proposal_id", "proposal_version", "proposal_hash", "receipt_ref", "receipt_hash",
             "entity_event_ids", "outcome"},
            {"proposal_id", "proposal_version", "proposal_hash", "receipt_ref", "receipt_hash",
             "entity_event_ids", "outcome"},
        ),
        "RESEARCH_COMMAND_RECORDED": (
            {"command", "action", "result_refs", "outcome"},
            {"command", "action", "result_refs", "outcome"},
        ),
        "RESEARCH_RESULT_RECORDED": (
            {"action", "claim_refs", "result_refs", "outcome", "reason_code"},
            {"action", "result_refs", "outcome"},
        ),
        "ARTIFACT_WRITE_INTENDED": (
            {"operation_id", "input_hash", "artifact_type", "confined_path", "expected_hash",
             "status", "outcome"},
            {"operation_id", "input_hash", "artifact_type", "confined_path", "expected_hash",
             "status", "outcome"},
        ),
        "ARTIFACT_WRITE_CONFIRMED": (
            {"operation_id", "input_hash", "artifact_type", "confined_path", "expected_hash",
             "observed_hash", "status", "outcome"},
            {"operation_id", "input_hash", "artifact_type", "confined_path", "expected_hash",
             "observed_hash", "status", "outcome"},
        ),
        "CHAPTER_VERSION_RECORDED": (
            {"operation_id", "input_hash", "artifact_type", "confined_path", "observed_hash",
             "chapter_id", "version", "status", "text_hash", "quality_evaluation_ref", "outcome"},
            {"operation_id", "input_hash", "artifact_type", "confined_path", "observed_hash",
             "chapter_id", "version", "status", "text_hash", "quality_evaluation_ref", "outcome"},
        ),
        "SERIES_VOLUME_CLOSED": (
            {"snapshot_id", "semantic_identity", "receipt_ref", "transferred_entity_refs", "outcome"},
            {"snapshot_id", "semantic_identity", "receipt_ref", "transferred_entity_refs", "outcome"},
        ),
        "MEMORY_EVENT_CORRECTION": (
            {"target_memory_event_id", "target_content_hash", "reason", "corrected_description"},
            {"target_memory_event_id", "target_content_hash", "reason", "corrected_description"},
        ),
    }
    allowed, required = shapes[event_type]
    unknown = set(result) - allowed
    if unknown:
        raise MemoryLedgerError(f"structured_payload has unknown fields: {sorted(unknown)!r}")
    missing = required - set(result)
    if missing:
        raise MemoryLedgerError(f"structured_payload missing required fields: {sorted(missing)!r}")
    if event_type == "LEDGER_BASELINE_OBJECT":
        if ("snapshot_text" in result) == ("immutable_ref" in result):
            raise MemoryLedgerError("baseline requires exactly one snapshot representation")
        if result["category"] not in _BASELINE_CATEGORIES:
            raise MemoryLedgerError("baseline category is outside the frozen registry")
        _validate_baseline_locator(result["locator"])
        if "immutable_ref" in result:
            _validate_reference_locator("BASELINE_OBJECT", result["immutable_ref"])
        if result["history_completeness"] != "UNKNOWN_BEFORE_BOUNDARY":
            raise MemoryLedgerError("baseline history completeness must be UNKNOWN_BEFORE_BOUNDARY")
        _positive_int(result["observed_schema_version"], "observed_schema_version")
    if event_type == "LEDGER_ACTIVATED":
        if result["origin"] not in {"EMPTY_STORE", "LEGACY_CUTOVER"}:
            raise MemoryLedgerError("unsupported activation origin")
        _nonnegative_int(result["baseline_count"], "baseline_count")
        if result["coverage"] != MEMORY_LEDGER_COVERAGE:
            raise MemoryLedgerError("activation coverage is invalid")
    for field in ("proposal_version", "new_version", "version"):
        if field in result:
            _positive_int(result[field], field)
    if "old_version" in result and result["old_version"] is not None:
        _positive_int(result["old_version"], "old_version")
    if "attempt_ordinal" in result:
        _nonnegative_int(result["attempt_ordinal"], "attempt_ordinal")
    required_hashes = {
        "LEDGER_BASELINE_OBJECT": ("bytes_hash",),
        "LEDGER_ACTIVATED": ("manifest_hash",),
        "PROPOSAL_RECORDED": ("proposal_hash", "basis_hash"),
        "PROPOSAL_STATE_RECORDED": ("proposal_hash", "basis_hash"),
        "AUTHOR_DECISION_RECORDED": ("proposal_hash",),
        "CANONICAL_ENTITY_CHANGED": ("proposal_hash", "new_hash", "commit_operation_ref"),
        "CANONICAL_COMMITTED": ("proposal_hash", "receipt_hash"),
        "ARTIFACT_WRITE_INTENDED": ("input_hash", "expected_hash"),
        "ARTIFACT_WRITE_CONFIRMED": ("input_hash", "expected_hash", "observed_hash"),
        "CHAPTER_VERSION_RECORDED": ("input_hash", "observed_hash", "text_hash"),
        "SERIES_VOLUME_CLOSED": ("semantic_identity",),
        "MEMORY_EVENT_CORRECTION": ("target_content_hash",),
    }
    for field in required_hashes.get(event_type, ()):
        _hash(result[field], field)
    if "old_hash" in result and result["old_hash"] is not None:
        _hash(result["old_hash"], "old_hash")
    for field in ("source_refs", "candidate_refs", "verification_refs", "invocation_refs",
                  "impact_refs", "guard_refs", "entity_event_ids", "claim_refs", "result_refs"):
        if field in result:
            _text_list(result[field], field)
    if "entity_event_ids" in result and result["entity_event_ids"] != sorted(set(result["entity_event_ids"])):
        raise MemoryLedgerError("entity_event_ids must be sorted and unique")
    for field in (
        "category", "locator", "snapshot_text", "immutable_ref", "coverage", "origin", "phase",
        "outcome", "proposal_id", "new_state", "previous_state", "approval_id",
        "authorization_ref", "entity_type", "entity_id", "commit_operation_ref", "receipt_ref",
        "command", "action", "operation_id", "artifact_type", "snapshot_id", "chapter_id",
        "status", "target_memory_event_id", "reason", "corrected_description",
    ):
        if field in result:
            _required_text(result[field], field)
    if "quality_evaluation_ref" in result:
        _optional_text(result["quality_evaluation_ref"], "quality_evaluation_ref")
    if "reason_code" in result:
        reason_code = _required_text(result["reason_code"], "reason_code")
        if reason_code not in _EVENT_REASON_CODES.get(event_type, set()):
            raise MemoryLedgerError("reason_code is not allowed for this event type")
    if "decision" in result and result["decision"] not in {"APPROVE", "REJECT"}:
        raise MemoryLedgerError("decision is invalid")
    if "confined_path" in result:
        _confined_path(result["confined_path"])
    if "transferred_entity_refs" in result:
        if not isinstance(result["transferred_entity_refs"], list):
            raise MemoryLedgerError("transferred_entity_refs must be a list")
        for item in result["transferred_entity_refs"]:
            if not isinstance(item, Mapping) or set(item) != {"record_type", "entity_id", "version", "target"}:
                raise MemoryLedgerError("transferred entity reference fields are closed")
            _required_text(item["record_type"], "transferred_entity_ref.record_type")
            _required_text(item["entity_id"], "transferred_entity_ref.entity_id")
            _positive_int(item["version"], "transferred_entity_ref.version")
            _required_text(item["target"], "transferred_entity_ref.target")
    if "world_time" in result:
        world_time = result["world_time"]
        if not isinstance(world_time, Mapping) or set(world_time) != {"valid_from", "valid_to"}:
            raise MemoryLedgerError("world_time fields are closed")
        _optional_text(world_time["valid_from"], "world_time.valid_from")
        _optional_text(world_time["valid_to"], "world_time.valid_to")
    if "narrative_order" in result and result["narrative_order"] is not None:
        _nonnegative_int(result["narrative_order"], "narrative_order")
    if event_type == "EXTRACTION_STARTED" and (
        result["phase"] != "STARTED" or result["outcome"] != "STARTED" or not result["source_refs"]
    ):
        raise MemoryLedgerError("extraction start payload is invalid")
    if event_type == "EXTRACTION_ATTEMPT_RECORDED" and (
        result["phase"] != "VERIFIED_ATTEMPT"
        or result["outcome"] not in {"ACCEPT", "REVISE", "REJECT"}
        or result["attempt_ordinal"] < 1
        or not all(result[field] for field in (
            "source_refs", "candidate_refs", "verification_refs", "invocation_refs",
        ))
    ):
        raise MemoryLedgerError("extraction attempt payload is invalid")
    if event_type == "EXTRACTION_FINISHED" and (
        result["phase"] != "TERMINAL"
        or result["outcome"] not in {"ACCEPT", "REVISE", "REJECT", "ESCALATED", "FAILED"}
    ):
        raise MemoryLedgerError("extraction terminal payload is invalid")
    if event_type == "EXTRACTION_FINISHED" and result["outcome"] != "FAILED" and (
        result["attempt_ordinal"] < 1
        or not result.get("verification_refs")
    ):
        raise MemoryLedgerError("extraction terminal evidence is incomplete")
    if (
        event_type == "EXTRACTION_FINISHED"
        and result["outcome"] == "ACCEPT"
        and not result.get("candidate_refs")
    ):
        raise MemoryLedgerError("accepted extraction candidate evidence is incomplete")
    if event_type in {"EXTRACTION_ATTEMPT_RECORDED", "EXTRACTION_FINISHED"}:
        negative = result["outcome"] in {"FAILED", "REJECT", "REVISE", "ESCALATED"}
        if "reason_code" in result and not negative:
            raise MemoryLedgerError("successful extraction must not carry a reason_code")
        if (
            event_type == "EXTRACTION_FINISHED"
            and result["outcome"] == "FAILED"
            and "reason_code" not in result
        ):
            raise MemoryLedgerError("failed extraction requires a reason_code")
    if event_type == "PROPOSAL_RECORDED" and (
        result["phase"] != "PROPOSAL_FROZEN"
        or result["new_state"] not in {"READY_FOR_ANALYSIS", "AWAITING_USER_APPROVAL"}
        or result["outcome"] != "RECORDED"
    ):
        raise MemoryLedgerError("proposal record payload is invalid")
    if event_type == "PROPOSAL_STATE_RECORDED":
        phase = result["phase"]
        previous_state = result["previous_state"]
        new_state = result["new_state"]
        outcome = result["outcome"]
        if previous_state not in _PROPOSAL_STATES or new_state not in _PROPOSAL_STATES:
            raise MemoryLedgerError("proposal state is invalid")
        valid_transition = False
        if phase == "INITIAL_ANALYSIS":
            valid_transition = (
                previous_state == "READY_FOR_ANALYSIS"
                and (outcome, new_state) in {
                    ("ALLOW", "APPROVED_FOR_COMMIT"),
                    ("REQUIRE_USER_APPROVAL", "AWAITING_USER_APPROVAL"),
                    ("DENY", "REJECTED"),
                }
            )
        elif phase == "FINAL_VALIDATION":
            valid_transition = previous_state in {
                "APPROVED_FOR_COMMIT", "AWAITING_USER_APPROVAL",
            } and (outcome, new_state) in {
                ("ALLOW", "COMMITTED"),
                ("DENY", "REJECTED"),
                ("REQUIRE_USER_APPROVAL", "AWAITING_USER_APPROVAL"),
                ("STALE", "STALE"),
            }
        elif phase == "SUPERSEDED":
            valid_transition = outcome == "STALE" and new_state == "STALE"
        elif phase == "FAILED":
            valid_transition = outcome == "FAILED" and new_state == "FAILED"
        if not valid_transition:
            raise MemoryLedgerError("proposal state transition is invalid")
        if phase == "INITIAL_ANALYSIS" and (
            not result.get("impact_refs") or not result.get("guard_refs")
        ):
            raise MemoryLedgerError("initial proposal analysis evidence is incomplete")
        if phase == "FINAL_VALIDATION" and (
            not result.get("impact_refs")
            or (outcome != "STALE" and not result.get("guard_refs"))
        ):
            raise MemoryLedgerError("final proposal validation evidence is incomplete")
        requires_reason = outcome in {"DENY", "REQUIRE_USER_APPROVAL", "STALE", "FAILED"}
        if requires_reason != ("reason_code" in result):
            raise MemoryLedgerError("proposal failure reason binding is invalid")
    if event_type == "AUTHOR_DECISION_RECORDED" and result["outcome"] != "RECORDED":
        raise MemoryLedgerError("author decision outcome is invalid")
    if (
        event_type == "CANONICAL_ENTITY_CHANGED"
        and result["outcome"] not in {"CREATE", "UPDATE", "REPLACE"}
    ):
        raise MemoryLedgerError("canonical entity outcome is invalid")
    if event_type == "CANONICAL_ENTITY_CHANGED":
        try:
            from app.p20_core.domain_records import DomainId, DomainNamespace
            namespace = DomainNamespace(_ENTITY_ID_NAMESPACES[result["entity_type"]])
            DomainId.parse(result["entity_id"]).require_namespace(namespace, "entity_id")
        except (KeyError, ValueError) as exc:
            raise MemoryLedgerError("canonical entity identity is invalid") from exc
        old_version, old_hash = result["old_version"], result["old_hash"]
        if result["outcome"] == "CREATE":
            if old_version is not None or old_hash is not None or result["new_version"] != 1:
                raise MemoryLedgerError("canonical CREATE version binding is invalid")
        elif (
            old_version is None
            or old_hash is None
            or result["new_version"] != old_version + 1
        ):
            raise MemoryLedgerError("canonical mutation version binding is invalid")
    if event_type == "CANONICAL_COMMITTED":
        if result["outcome"] != "COMMITTED":
            raise MemoryLedgerError("canonical commit outcome is invalid")
        if not result["entity_event_ids"] or any(
            _MEMORY_EVENT_ID_RE.fullmatch(value) is None for value in result["entity_event_ids"]
        ):
            raise MemoryLedgerError("canonical commit entity event id is invalid")
    if event_type == "RESEARCH_COMMAND_RECORDED" and (
        (result["command"], result["action"]) not in {
            ("QUESTION", "CREATE"), ("SOURCE", "IMPORT"),
        }
        or result["outcome"] != "COMPLETED"
    ):
        raise MemoryLedgerError("research command payload is invalid")
    if event_type == "RESEARCH_RESULT_RECORDED":
        if (
            result["action"] not in {"EXTRACT", "VERIFY", "RECOVERY"}
            or result["outcome"] not in {"COMPLETED", "FAILED"}
        ):
            raise MemoryLedgerError("research result payload is invalid")
        if (result["outcome"] == "FAILED") != ("reason_code" in result):
            raise MemoryLedgerError("research result reason binding is invalid")
        if not result["result_refs"]:
            raise MemoryLedgerError("research result requires durable result_refs")
    if event_type == "ARTIFACT_WRITE_INTENDED" and (
        result["status"] != "INTENDED" or result["outcome"] != "PENDING"
    ):
        raise MemoryLedgerError("artifact intent payload is invalid")
    if event_type == "ARTIFACT_WRITE_CONFIRMED" and (
        result["status"] != "CONFIRMED"
        or result["outcome"] != "COMMITTED"
        or result["expected_hash"] != result["observed_hash"]
    ):
        raise MemoryLedgerError("artifact confirmation payload is invalid")
    if event_type == "CHAPTER_VERSION_RECORDED" and (
        result["status"] not in {"SUPERSEDED", "ACCEPTED"}
        or result["outcome"] != "RECORDED"
    ):
        raise MemoryLedgerError("chapter version payload is invalid")
    if event_type == "CHAPTER_VERSION_RECORDED":
        chapter_id = result["chapter_id"]
        # Chapter lineage predates DomainId and its active producer persists the
        # confined chapter filename stem.  Keep that contract, while closing the
        # path/URI ambiguity that would make the event slot unsafe.
        if (
            chapter_id in {".", ".."}
            or "/" in chapter_id
            or "\\" in chapter_id
            or ":" in chapter_id
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", chapter_id) is None
        ):
            raise MemoryLedgerError("chapter version identity is invalid")
    if event_type == "SERIES_VOLUME_CLOSED" and result["outcome"] != "CLOSED":
        raise MemoryLedgerError("series volume outcome is invalid")
    if event_type == "SERIES_VOLUME_CLOSED" and any(
        item["target"] not in {"SERIES_MEMORY", "SERIES_CANON"}
        for item in result["transferred_entity_refs"]
    ):
        raise MemoryLedgerError("series transfer target is invalid")
    if event_type == "MEMORY_EVENT_CORRECTION" and _MEMORY_EVENT_ID_RE.fullmatch(
        result["target_memory_event_id"]
    ) is None:
        raise MemoryLedgerError("correction target memory event id is invalid")
    return result


def _positive_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= _INT64_MAX:
        raise MemoryLedgerError(f"{field_name} must be a positive signed 64-bit integer")
    return value


def _nonnegative_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= _INT64_MAX:
        raise MemoryLedgerError(f"{field_name} must be a nonnegative signed 64-bit integer")
    return value


def _text_list(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise MemoryLedgerError(f"{field_name} must be a list")
    return tuple(_required_text(item, field_name) for item in value)


def _confined_path(value: Any) -> str:
    text = _required_text(value, "confined_path")
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or "\\" in text
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text)
        or any(":" in part for part in path.parts)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise MemoryLedgerError("confined_path must be a normalized relative POSIX path")
    return text


def _validate_event_contract(event: "MemoryEventRecord") -> None:
    expected_namespace = {
        "LEDGER_BASELINE_OBJECT": "LEDGER_BOOTSTRAP",
        "LEDGER_ACTIVATED": "LEDGER_BOOTSTRAP",
        "EXTRACTION_STARTED": "CANONICAL_PIPELINE",
        "EXTRACTION_ATTEMPT_RECORDED": "CANONICAL_PIPELINE",
        "EXTRACTION_FINISHED": "CANONICAL_PIPELINE",
        "PROPOSAL_RECORDED": "CANONICAL_PROPOSAL",
        "PROPOSAL_STATE_RECORDED": "CANONICAL_PROPOSAL",
        "AUTHOR_DECISION_RECORDED": "CANONICAL_PROPOSAL",
        "CANONICAL_ENTITY_CHANGED": "CANONICAL_PROPOSAL",
        "CANONICAL_COMMITTED": "CANONICAL_PROPOSAL",
        "RESEARCH_COMMAND_RECORDED": "RESEARCH_COMMAND",
        "RESEARCH_RESULT_RECORDED": "RESEARCH_OPERATION",
        "ARTIFACT_WRITE_INTENDED": "CROSS_STORE_OPERATION",
        "ARTIFACT_WRITE_CONFIRMED": "CROSS_STORE_OPERATION",
        "CHAPTER_VERSION_RECORDED": "CROSS_STORE_OPERATION",
        "SERIES_VOLUME_CLOSED": "SERIES_VOLUME_OPERATION",
        "MEMORY_EVENT_CORRECTION": "LEDGER_CORRECTION",
    }[event.event_type]
    if event.operation["namespace"] != expected_namespace:
        raise MemoryLedgerError("event_type is not allowed for operation namespace")
    payload, slot = event.structured_payload, event.event_slot
    valid = False
    if event.event_type == "LEDGER_BASELINE_OBJECT":
        valid = slot == ("object", payload["category"], payload["locator"])
    elif event.event_type == "LEDGER_ACTIVATED":
        valid = slot == ("activated",)
    elif event.event_type == "EXTRACTION_STARTED":
        valid = slot == ("start",) and payload["attempt_ordinal"] == 0
    elif event.event_type == "EXTRACTION_ATTEMPT_RECORDED":
        valid = (len(slot) == 2 and slot[0] == "attempt"
                 and _POSITIVE_DECIMAL_RE.fullmatch(slot[1]) is not None
                 and int(slot[1]) == payload["attempt_ordinal"])
    elif event.event_type == "EXTRACTION_FINISHED":
        valid = slot == ("terminal",)
    elif event.event_type == "PROPOSAL_RECORDED":
        valid = slot == ("proposal", str(payload["proposal_version"]))
    elif event.event_type == "PROPOSAL_STATE_RECORDED":
        phase_slot = {
            "INITIAL_ANALYSIS": "initial-analysis", "FINAL_VALIDATION": "final-validation",
            "SUPERSEDED": "superseded", "FAILED": "failed",
        }.get(payload["phase"])
        valid = (phase_slot is not None and len(slot) in {3, 4}
                 and slot[:3] == ("state", str(payload["proposal_version"]), phase_slot)
                 and ((phase_slot == "final-validation") == (len(slot) == 4))
                 and (len(slot) != 4 or slot[3] == "NONE" or (
                     slot[3].startswith("approval-") and len(slot[3]) > len("approval-")
                 )))
    elif event.event_type == "AUTHOR_DECISION_RECORDED":
        valid = slot == ("decision", str(payload["proposal_version"]))
    elif event.event_type == "CANONICAL_ENTITY_CHANGED":
        valid = slot == ("entity", str(payload["proposal_version"]), payload["entity_type"], payload["entity_id"])
    elif event.event_type == "CANONICAL_COMMITTED":
        valid = slot == ("commit", str(payload["proposal_version"]))
    elif event.event_type == "RESEARCH_COMMAND_RECORDED":
        valid = slot == ("command",)
    elif event.event_type == "RESEARCH_RESULT_RECORDED":
        valid = len(slot) == 2 and slot[0] == "result" and _POSITIVE_DECIMAL_RE.fullmatch(slot[1]) is not None
    elif event.event_type == "ARTIFACT_WRITE_INTENDED":
        valid = slot == ("intent",) and payload["operation_id"] == event.operation["id"]
    elif event.event_type == "ARTIFACT_WRITE_CONFIRMED":
        valid = slot == ("confirmed",) and payload["operation_id"] == event.operation["id"]
    elif event.event_type == "CHAPTER_VERSION_RECORDED":
        valid = (slot == ("chapter-version", payload["chapter_id"], str(payload["version"]))
                 and payload["operation_id"] == event.operation["id"])
    elif event.event_type == "SERIES_VOLUME_CLOSED":
        valid = slot == ("closed",)
    elif event.event_type == "MEMORY_EVENT_CORRECTION":
        valid = slot == ("correction",)
    if not valid:
        raise MemoryLedgerError("event_slot does not match event payload contract")
    if event.operation["namespace"] == "LEDGER_BOOTSTRAP":
        if event.actor != {
            "kind": "SYSTEM", "id": "MEMORY_LEDGER_BOOTSTRAP_V1", "evidence_ref": None,
        }:
            raise MemoryLedgerError("bootstrap event actor is invalid")
    elif event.event_type not in {
        "AUTHOR_DECISION_RECORDED", "MEMORY_EVENT_CORRECTION",
    } and not (
        event.event_type == "RESEARCH_RESULT_RECORDED"
        and event.structured_payload.get("reason_code") == "OPERATOR_RECOVERY"
    ) and event.actor["kind"] != "SYSTEM":
        raise MemoryLedgerError("event requires system actor authority")
    if "proposal_id" in payload and payload["proposal_id"] != event.operation["id"]:
        raise MemoryLedgerError("proposal_id does not match operation id")
    if event.event_type == "AUTHOR_DECISION_RECORDED" and (
        event.actor["kind"] != "OPERATOR"
        or event.actor["evidence_ref"] != payload["authorization_ref"]
    ):
        raise MemoryLedgerError("author decision requires bound operator authority")
    if event.event_type == "PROPOSAL_STATE_RECORDED" and payload["phase"] == "FINAL_VALIDATION":
        approval_revision = slot[3]
        approvals = tuple(
            reference for reference in event.parent_refs
            if reference["kind"] == "APPROVAL"
            and reference["owner_scope_type"] == event.scope_type
            and reference["owner_scope_id"] == event.scope_id
            and reference["locator"] == approval_revision
            and reference["hash"] is not None
        )
        if (approval_revision == "NONE" and approvals) or (
            approval_revision != "NONE" and len(approvals) != 1
        ):
            raise MemoryLedgerError("final proposal approval revision is not bound")
    if event.event_type == "RESEARCH_RESULT_RECORDED":
        operator_recovery = payload.get("reason_code") == "OPERATOR_RECOVERY"
        if operator_recovery != (event.actor["kind"] == "OPERATOR"):
            raise MemoryLedgerError("research recovery actor authority is invalid")
        if payload["action"] == "RECOVERY" and not operator_recovery:
            raise MemoryLedgerError("research RECOVERY action requires operator recovery evidence")
    if event.event_type == "CANONICAL_ENTITY_CHANGED":
        expected_entity = {
            "record_type": payload["entity_type"],
            "entity_id": payload["entity_id"],
            "version": payload["new_version"],
        }
        if event.entity_refs != (expected_entity,):
            raise MemoryLedgerError("canonical entity event reference binding is invalid")
    if event.event_type == "MEMORY_EVENT_CORRECTION":
        if event.actor["kind"] != "OPERATOR" or not event.actor["evidence_ref"]:
            raise MemoryLedgerError("correction requires authenticated operator evidence")
        matching = tuple(
            reference
            for reference in event.parent_refs
            if reference["kind"] == "MEMORY_EVENT"
            and reference["owner_scope_type"] == event.scope_type
            and reference["owner_scope_id"] == event.scope_id
            and reference["locator"] == payload["target_memory_event_id"]
            and reference["hash"] == payload["target_content_hash"]
        )
        if len(matching) != 1:
            raise MemoryLedgerError("correction requires the exact target memory event parent")
    for reference in (*event.parent_refs, *event.artifact_refs, *event.source_refs):
        if (
            reference["kind"] == "BASELINE_OBJECT"
            and reference["hash"] is None
            and event.operation["namespace"] != "LEDGER_BOOTSTRAP"
        ):
            raise MemoryLedgerError("hashless baseline reference is bootstrap-only")


@dataclass(frozen=True)
class MemoryEventRecord:
    memory_event_id: str
    sequence: int
    scope_type: str
    scope_id: str
    operation: dict[str, str]
    event_slot: tuple[str, ...]
    event_type: str
    timestamp: str
    created_at: str
    actor: dict[str, str | None]
    project_id: str | None
    book_id: str | None
    series_id: str | None
    run_id: str | None
    step_id: str | None
    entity_refs: tuple[dict[str, Any], ...]
    parent_refs: tuple[dict[str, Any], ...]
    artifact_refs: tuple[dict[str, Any], ...]
    source_refs: tuple[dict[str, Any], ...]
    structured_payload: dict[str, Any]
    content_hash: str
    schema_version: int = MEMORY_LEDGER_SCHEMA_VERSION
    hash_version: str = "memory-event-content.v1"

    def __post_init__(self) -> None:
        _positive_int(self.sequence, "sequence")
        if self.scope_type not in _SCOPES:
            raise MemoryLedgerError("unsupported scope_type")
        from app.p20_core.domain_records import DomainId, DomainNamespace

        scope_namespace = DomainNamespace.PROJECT if self.scope_type == "PROJECT" else DomainNamespace.SERIES
        try:
            DomainId.parse(self.scope_id).require_namespace(scope_namespace, "scope_id")
        except ValueError as exc:
            raise MemoryLedgerError("scope_id is not a valid domain id") from exc
        if self.scope_type == "PROJECT":
            if self.project_id != self.scope_id or self.book_id is None or self.series_id is not None:
                raise MemoryLedgerError("PROJECT event identity is invalid")
        elif self.series_id != self.scope_id or (
            self.operation.get("namespace") != "LEDGER_BOOTSTRAP"
            and (self.project_id is None or self.book_id is None)
        ):
            raise MemoryLedgerError("SERIES event identity is invalid")
        for value, namespace, field_name in (
            (self.project_id, DomainNamespace.PROJECT, "project_id"),
            (self.book_id, DomainNamespace.BOOK, "book_id"),
            (self.series_id, DomainNamespace.SERIES, "series_id"),
        ):
            if value is not None:
                try:
                    DomainId.parse(value).require_namespace(namespace, field_name)
                except ValueError as exc:
                    raise MemoryLedgerError(f"{field_name} is not a valid domain id") from exc
        if set(self.operation) != {"namespace", "id"} or self.operation["namespace"] not in _NAMESPACES:
            raise MemoryLedgerError("operation is invalid")
        _required_text(self.operation["id"], "operation.id")
        if not self.event_slot or any(not isinstance(value, str) or not value for value in self.event_slot):
            raise MemoryLedgerError("event_slot must be a non-empty string list")
        if self.event_type not in _EVENT_TYPES:
            raise MemoryLedgerError("unsupported event_type")
        if self.schema_version != MEMORY_LEDGER_SCHEMA_VERSION or self.hash_version != "memory-event-content.v1":
            raise MemoryLedgerError("unsupported memory event schema")
        if not _TIME_RE.fullmatch(self.timestamp) or self.timestamp != self.created_at:
            raise MemoryLedgerError("timestamp and created_at must be identical RFC3339 UTC values")
        if set(self.actor) != {"kind", "id", "evidence_ref"} or self.actor["kind"] not in {"SYSTEM", "OPERATOR"}:
            raise MemoryLedgerError("actor is invalid")
        _required_text(self.actor["id"], "actor.id")
        _optional_text(self.actor["evidence_ref"], "actor.evidence_ref")
        if self.actor["kind"] == "OPERATOR" and self.actor["evidence_ref"] is None:
            raise MemoryLedgerError("operator event requires durable evidence_ref")
        for field_name in ("run_id", "step_id"):
            _optional_text(getattr(self, field_name), field_name)
        expected_id = memory_event_id(self.scope_type, self.scope_id, self.operation, self.event_slot)
        if self.memory_event_id != expected_id:
            raise MemoryLedgerError("memory_event_id does not match key preimage")
        for collection, validator, name in (
            (self.entity_refs, _validate_entity_ref, "entity_refs"),
            (self.parent_refs, _validate_reference, "parent_refs"),
            (self.artifact_refs, _validate_reference, "artifact_refs"),
            (self.source_refs, _validate_reference, "source_refs"),
        ):
            normalized = _sorted_unique_dicts(collection, name)
            if tuple(validator(item) for item in normalized) != collection:
                raise MemoryLedgerError(f"{name} must be canonical sorted unique values")
        if _validate_payload(self.event_type, self.structured_payload) != self.structured_payload:
            raise MemoryLedgerError("structured_payload is not canonical")
        _validate_event_contract(self)
        _hash(self.content_hash, "content_hash")
        if self.content_hash != sha256_text(canonical_json(self._without_hash())):
            raise MemoryLedgerIntegrityError("memory event content_hash mismatch")

    def _without_hash(self) -> dict[str, Any]:
        data = self.to_dict(include_hash=False)
        return data

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "memory_event_id": self.memory_event_id,
            "sequence": self.sequence,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "operation": self.operation,
            "event_slot": list(self.event_slot),
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "created_at": self.created_at,
            "actor": self.actor,
            "project_id": self.project_id,
            "book_id": self.book_id,
            "series_id": self.series_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "entity_refs": list(self.entity_refs),
            "parent_refs": list(self.parent_refs),
            "artifact_refs": list(self.artifact_refs),
            "source_refs": list(self.source_refs),
            "structured_payload": self.structured_payload,
            "schema_version": self.schema_version,
            "hash_version": self.hash_version,
        }
        if include_hash:
            data["content_hash"] = self.content_hash
        return data

    def comparison_payload(self) -> dict[str, Any]:
        data = self.to_dict(include_hash=False)
        for name in ("sequence", "timestamp", "created_at"):
            data.pop(name)
        return data

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MemoryEventRecord":
        expected = {
            "memory_event_id", "sequence", "scope_type", "scope_id", "operation", "event_slot",
            "event_type", "timestamp", "created_at", "actor", "project_id", "book_id", "series_id",
            "run_id", "step_id", "entity_refs", "parent_refs", "artifact_refs", "source_refs",
            "structured_payload", "content_hash", "schema_version", "hash_version",
        }
        if set(value) != expected:
            raise MemoryLedgerError("memory event fields are closed")
        if not isinstance(value["operation"], Mapping):
            raise MemoryLedgerError("operation must be an object")
        if not isinstance(value["actor"], Mapping):
            raise MemoryLedgerError("actor must be an object")
        if not isinstance(value["structured_payload"], Mapping):
            raise MemoryLedgerError("structured_payload must be an object")
        if not isinstance(value["event_slot"], list):
            raise MemoryLedgerError("event_slot must be a list")
        for name in ("entity_refs", "parent_refs", "artifact_refs", "source_refs"):
            if not isinstance(value[name], list) or any(
                not isinstance(item, Mapping) for item in value[name]
            ):
                raise MemoryLedgerError(f"{name} must be a list of objects")
        return cls(
            memory_event_id=_required_text(value["memory_event_id"], "memory_event_id"),
            sequence=value["sequence"], scope_type=value["scope_type"], scope_id=value["scope_id"],
            operation=dict(value["operation"]), event_slot=tuple(value["event_slot"]), event_type=value["event_type"],
            timestamp=value["timestamp"], created_at=value["created_at"], actor=dict(value["actor"]),
            project_id=value["project_id"], book_id=value["book_id"], series_id=value["series_id"],
            run_id=value["run_id"], step_id=value["step_id"],
            entity_refs=tuple(dict(item) for item in value["entity_refs"]),
            parent_refs=tuple(dict(item) for item in value["parent_refs"]),
            artifact_refs=tuple(dict(item) for item in value["artifact_refs"]),
            source_refs=tuple(dict(item) for item in value["source_refs"]),
            structured_payload=dict(value["structured_payload"]), content_hash=value["content_hash"],
            schema_version=value["schema_version"], hash_version=value["hash_version"],
        )


def memory_event_id(scope_type: str, scope_id: str, operation: Mapping[str, str], event_slot: Iterable[str]) -> str:
    preimage = {
        "key_version": "memory-event-key.v1", "scope_type": scope_type, "scope_id": scope_id,
        "operation": dict(operation), "event_slot": list(event_slot),
    }
    return "MEV-" + sha256_text(canonical_json(preimage))


def build_memory_event(*, sequence: int, scope_type: str, scope_id: str, operation: Mapping[str, str],
                       event_slot: Iterable[str], event_type: str, actor: Mapping[str, str | None],
                       project_id: str | None, book_id: str | None, series_id: str | None,
                       structured_payload: Mapping[str, Any], run_id: str | None = None,
                       step_id: str | None = None, entity_refs: Iterable[Mapping[str, Any]] = (),
                       parent_refs: Iterable[Mapping[str, Any]] = (), artifact_refs: Iterable[Mapping[str, Any]] = (),
                       source_refs: Iterable[Mapping[str, Any]] = (), timestamp: str | None = None) -> MemoryEventRecord:
    time_value = timestamp or utc_now()
    op = {"namespace": operation.get("namespace"), "id": operation.get("id")}
    slot = tuple(event_slot)
    data = {
        "memory_event_id": memory_event_id(scope_type, scope_id, op, slot), "sequence": sequence,
        "scope_type": scope_type, "scope_id": scope_id, "operation": op, "event_slot": list(slot),
        "event_type": event_type, "timestamp": time_value, "created_at": time_value, "actor": dict(actor),
        "project_id": project_id, "book_id": book_id, "series_id": series_id, "run_id": run_id,
        "step_id": step_id, "entity_refs": list(_sorted_unique_dicts(entity_refs, "entity_refs")),
        "parent_refs": list(_sorted_unique_dicts(parent_refs, "parent_refs")),
        "artifact_refs": list(_sorted_unique_dicts(artifact_refs, "artifact_refs")),
        "source_refs": list(_sorted_unique_dicts(source_refs, "source_refs")),
        "structured_payload": dict(structured_payload), "schema_version": MEMORY_LEDGER_SCHEMA_VERSION,
        "hash_version": "memory-event-content.v1",
    }
    data["content_hash"] = sha256_text(canonical_json(data))
    return MemoryEventRecord.from_dict(data)


def create_memory_ledger_schema(
    conn: sqlite3.Connection, *, scope_type: str, identity_table: str, identity_column: str,
    replace_triggers: bool = False,
) -> None:
    if scope_type not in _SCOPES or identity_table not in {"project_identity", "series_identity"}:
        raise MemoryLedgerError("invalid ledger owner schema")
    namespaces = ",".join(f"'{value}'" for value in sorted(_NAMESPACES))
    event_types = ",".join(f"'{value}'" for value in sorted(_EVENT_TYPES))
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS memory_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_event_id TEXT NOT NULL UNIQUE CHECK (
                length(memory_event_id) = 68
                AND substr(memory_event_id, 1, 4) = 'MEV-'
                AND substr(memory_event_id, 5) NOT GLOB '*[^0-9a-f]*'
            ),
            event_key_json TEXT NOT NULL UNIQUE CHECK (length(trim(event_key_json)) > 0),
            scope_type TEXT NOT NULL CHECK (scope_type = '{scope_type}'),
            scope_id TEXT NOT NULL CHECK (length(trim(scope_id)) > 0),
            operation_namespace TEXT NOT NULL CHECK (operation_namespace IN ({namespaces})),
            operation_id TEXT NOT NULL CHECK (length(trim(operation_id)) > 0),
            event_type TEXT NOT NULL CHECK (event_type IN ({event_types})),
            schema_version INTEGER NOT NULL CHECK (schema_version = 1),
            record_json TEXT NOT NULL CHECK (length(trim(record_json)) > 0),
            content_hash TEXT NOT NULL CHECK (
                length(content_hash) = 64
                AND content_hash NOT GLOB '*[^0-9a-f]*'
            )
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_event_entities (
            sequence INTEGER NOT NULL REFERENCES memory_events(sequence) ON DELETE RESTRICT,
            record_type TEXT NOT NULL CHECK (length(trim(record_type)) > 0),
            entity_id TEXT NOT NULL CHECK (length(trim(entity_id)) > 0),
            PRIMARY KEY (sequence, record_type, entity_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS memory_events_operation_lookup ON memory_events(scope_type, scope_id, operation_namespace, operation_id, sequence)")
    conn.execute("CREATE INDEX IF NOT EXISTS memory_events_type_lookup ON memory_events(scope_type, scope_id, event_type, sequence)")
    conn.execute("CREATE INDEX IF NOT EXISTS memory_event_entities_lookup ON memory_event_entities(record_type, entity_id, sequence)")
    if replace_triggers:
        for trigger in ("memory_events_reject_insert", "memory_events_reject_update", "memory_events_reject_delete", "memory_event_entities_reject_insert", "memory_event_entities_reject_update", "memory_event_entities_reject_delete"):
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS memory_events_reject_insert BEFORE INSERT ON memory_events
        WHEN NEW.scope_type != '{scope_type}' OR NOT EXISTS (
            SELECT 1 FROM {identity_table} WHERE id = 1 AND {identity_column} = NEW.scope_id
        ) OR EXISTS (
            SELECT 1 FROM memory_events WHERE sequence = NEW.sequence
               OR memory_event_id = NEW.memory_event_id OR event_key_json = NEW.event_key_json
        )
        BEGIN SELECT RAISE(ABORT, 'memory_events append-only collision or owner mismatch'); END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_events_reject_update BEFORE UPDATE ON memory_events
        BEGIN SELECT RAISE(ABORT, 'memory_events are append-only'); END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_events_reject_delete BEFORE DELETE ON memory_events
        BEGIN SELECT RAISE(ABORT, 'memory_events are append-only'); END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_event_entities_reject_insert BEFORE INSERT ON memory_event_entities
        WHEN EXISTS (SELECT 1 FROM memory_event_entities WHERE sequence = NEW.sequence AND record_type = NEW.record_type AND entity_id = NEW.entity_id)
          OR NOT EXISTS (
            SELECT 1 FROM json_each((SELECT record_json FROM memory_events WHERE sequence = NEW.sequence), '$.entity_refs')
            WHERE json_extract(value, '$.record_type') = NEW.record_type
              AND json_extract(value, '$.entity_id') = NEW.entity_id
          )
        BEGIN SELECT RAISE(ABORT, 'memory_event_entities append-only collision'); END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_event_entities_reject_update BEFORE UPDATE ON memory_event_entities
        BEGIN SELECT RAISE(ABORT, 'memory_event_entities are append-only'); END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_event_entities_reject_delete BEFORE DELETE ON memory_event_entities
        BEGIN SELECT RAISE(ABORT, 'memory_event_entities are append-only'); END
    """)


def _schema_sql_tokens(sql: str | None) -> tuple[str, ...]:
    """Tokenize schema SQL while preserving literals and ignoring comments."""
    if sql is None:
        return ()
    value = str(sql)
    tokens: list[str] = []
    cursor = 0
    paired_operators = {"<=", ">=", "!=", "<>", "==", "||", "<<", ">>"}
    while cursor < len(value):
        character = value[cursor]
        if character.isspace():
            cursor += 1
            continue
        if value.startswith("--", cursor):
            cursor += 2
            while cursor < len(value) and value[cursor] != "\n":
                cursor += 1
            continue
        if value.startswith("/*", cursor):
            end = value.find("*/", cursor + 2)
            if end < 0:
                raise MemoryLedgerIntegrityError("schema SQL block comment is malformed")
            cursor = end + 2
            continue
        if character == "'":
            start = cursor
            cursor += 1
            closed = False
            while cursor < len(value):
                if value[cursor] == "'":
                    cursor += 1
                    if cursor < len(value) and value[cursor] == "'":
                        cursor += 1
                        continue
                    closed = True
                    break
                cursor += 1
            if not closed:
                raise MemoryLedgerIntegrityError("schema SQL string literal is malformed")
            tokens.append(value[start:cursor])
            continue
        if character in {'"', "`"}:
            quote = character
            start = cursor
            cursor += 1
            identifier: list[str] = []
            closed = False
            while cursor < len(value):
                if value[cursor] == quote:
                    cursor += 1
                    if cursor < len(value) and value[cursor] == quote:
                        identifier.append(quote)
                        cursor += 1
                        continue
                    closed = True
                    break
                identifier.append(value[cursor])
                cursor += 1
            if not closed:
                raise MemoryLedgerIntegrityError("schema SQL quoted identifier is malformed")
            raw_identifier = "".join(identifier)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", raw_identifier):
                tokens.append(_canonical_sqlite_identifier(raw_identifier))
            else:
                # SQLite accepts double-quoted string literals in some schema
                # positions.  Preserve their bytes so literal changes do not
                # disappear during schema comparison.
                tokens.append(value[start:cursor])
            continue
        if character == "[":
            end = value.find("]", cursor + 1)
            if end < 0:
                raise MemoryLedgerIntegrityError("schema SQL quoted identifier is malformed")
            raw_identifier = value[cursor + 1:end]
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", raw_identifier):
                tokens.append(_canonical_sqlite_identifier(raw_identifier))
            else:
                tokens.append(value[cursor:end + 1])
            cursor = end + 1
            continue
        if character.isalnum() or character in {"_", "$"}:
            start = cursor
            cursor += 1
            while cursor < len(value) and (
                value[cursor].isalnum() or value[cursor] in {"_", "$"}
            ):
                cursor += 1
            tokens.append(value[start:cursor].lower())
            continue
        pair = value[cursor:cursor + 2]
        if pair in paired_operators:
            tokens.append(pair)
            cursor += 2
            continue
        tokens.append(character)
        cursor += 1
    return tuple(tokens)


def _canonical_sqlite_identifier(value: str) -> str:
    """Apply SQLite's locale-independent ASCII identifier case semantics."""
    return "".join(
        chr(ord(character) + 32) if "A" <= character <= "Z" else character
        for character in str(value)
    )


def _without_redundant_parentheses(tokens: tuple[str, ...]) -> tuple[str, ...]:
    while len(tokens) >= 2 and tokens[0] == "(" and tokens[-1] == ")":
        depth = 0
        closes_at_end = False
        for index, token in enumerate(tokens):
            if token == "(":
                depth += 1
            elif token == ")":
                depth -= 1
                if depth == 0:
                    closes_at_end = index == len(tokens) - 1
                    break
        if not closes_at_end:
            break
        tokens = tokens[1:-1]
    return tokens


def _schema_check_signatures(sql: str | None) -> tuple[tuple[str, ...], ...]:
    """Return formatting-independent signatures for every CHECK expression.

    Separate CHECK clauses are sorted, so harmless clause reordering does not
    change the signature. SQL keywords and identifiers are case-insensitive,
    while quoted values retain their case-sensitive SQLite meaning.
    """
    tokens = _schema_sql_tokens(sql)
    expressions: list[tuple[str, ...]] = []
    cursor = 0
    while cursor < len(tokens):
        if tokens[cursor] != "check" or cursor + 1 >= len(tokens) or tokens[cursor + 1] != "(":
            cursor += 1
            continue
        start = cursor + 2
        cursor = start
        depth = 1
        while cursor < len(tokens):
            if tokens[cursor] == "(":
                depth += 1
            elif tokens[cursor] == ")":
                depth -= 1
                if depth == 0:
                    expressions.append(
                        _without_redundant_parentheses(tokens[start:cursor])
                    )
                    cursor += 1
                    break
            cursor += 1
        else:
            raise MemoryLedgerIntegrityError("table CHECK constraint is malformed")
    return tuple(sorted(expressions))


def _canonical_schema_sql(sql: str | None) -> str:
    return "".join(_schema_sql_tokens(sql))


def _schema_has_token_sequence(sql: str | None, sequence: tuple[str, ...]) -> bool:
    tokens = _schema_sql_tokens(sql)
    width = len(sequence)
    return width > 0 and any(
        tokens[index:index + width] == sequence
        for index in range(len(tokens) - width + 1)
    )


def _sqlite_table_xinfo_signature(
    conn: sqlite3.Connection, table: str,
) -> tuple[tuple[str, str, int, object, int, int], ...]:
    quoted = '"' + table.replace('"', '""') + '"'
    return tuple(
        (
            str(row[1]), str(row[2]).upper(), int(row[3]), row[4],
            int(row[5]), int(row[6]),
        )
        for row in conn.execute(f"PRAGMA table_xinfo({quoted})").fetchall()
    )


def _sqlite_index_key_signature(
    conn: sqlite3.Connection, index_name: str,
) -> tuple[tuple[str | None, int, str], ...]:
    quoted = '"' + index_name.replace('"', '""') + '"'
    rows = conn.execute(f"PRAGMA index_xinfo({quoted})").fetchall()
    return tuple(
        (
            None if row[2] is None else str(row[2]),
            int(row[3]),
            str(row[4]).upper(),
        )
        for row in rows
        if int(row[5]) == 1
    )


def validate_memory_ledger_schema(conn: sqlite3.Connection, *, scope_type: str) -> None:
    if scope_type not in _SCOPES:
        raise MemoryLedgerIntegrityError("unsupported memory ledger scope")
    identity_table, identity_column = (
        ("project_identity", "project_id") if scope_type == "PROJECT"
        else ("series_identity", "series_id")
    )

    expected_columns = {
        "memory_events": (
            ("sequence", "INTEGER", 0, None, 1, 0),
            ("memory_event_id", "TEXT", 1, None, 0, 0),
            ("event_key_json", "TEXT", 1, None, 0, 0),
            ("scope_type", "TEXT", 1, None, 0, 0),
            ("scope_id", "TEXT", 1, None, 0, 0),
            ("operation_namespace", "TEXT", 1, None, 0, 0),
            ("operation_id", "TEXT", 1, None, 0, 0),
            ("event_type", "TEXT", 1, None, 0, 0),
            ("schema_version", "INTEGER", 1, None, 0, 0),
            ("record_json", "TEXT", 1, None, 0, 0),
            ("content_hash", "TEXT", 1, None, 0, 0),
        ),
        "memory_event_entities": (
            ("sequence", "INTEGER", 1, None, 1, 0),
            ("record_type", "TEXT", 1, None, 2, 0),
            ("entity_id", "TEXT", 1, None, 3, 0),
        ),
    }
    namespace_sql = ",".join(f"'{value}'" for value in sorted(_NAMESPACES))
    event_type_sql = ",".join(f"'{value}'" for value in sorted(_EVENT_TYPES))
    required_table_fragments = {
        "memory_events": (
            "sequenceintegerprimarykeyautoincrement",
            "memory_event_idtextnotnulluniquecheck(length(memory_event_id)=68andsubstr(memory_event_id,1,4)='MEV-'andsubstr(memory_event_id,5)notglob'*[^0-9a-f]*')",
            "event_key_jsontextnotnulluniquecheck(length(trim(event_key_json))>0)",
            f"scope_typetextnotnullcheck(scope_type='{scope_type}')",
            "scope_idtextnotnullcheck(length(trim(scope_id))>0)",
            f"operation_namespacetextnotnullcheck(operation_namespacein({namespace_sql}))",
            "operation_idtextnotnullcheck(length(trim(operation_id))>0)",
            f"event_typetextnotnullcheck(event_typein({event_type_sql}))",
            "schema_versionintegernotnullcheck(schema_version=1)",
            "record_jsontextnotnullcheck(length(trim(record_json))>0)",
            "content_hashtextnotnullcheck(length(content_hash)=64andcontent_hashnotglob'*[^0-9a-f]*')",
        ),
        "memory_event_entities": (
            "sequenceintegernotnullreferencesmemory_events(sequence)ondeleterestrict",
            "record_typetextnotnullcheck(length(trim(record_type))>0)",
            "entity_idtextnotnullcheck(length(trim(entity_id))>0)",
            "primarykey(sequence,record_type,entity_id)",
        ),
    }
    expected_check_sql = {
        "memory_events": (
            "CHECK(length(memory_event_id) = 68 AND substr(memory_event_id, 1, 4) = 'MEV-' "
            "AND substr(memory_event_id, 5) NOT GLOB '*[^0-9a-f]*')",
            "CHECK(length(trim(event_key_json)) > 0)",
            f"CHECK(scope_type = '{scope_type}')",
            "CHECK(length(trim(scope_id)) > 0)",
            f"CHECK(operation_namespace IN ({namespace_sql}))",
            "CHECK(length(trim(operation_id)) > 0)",
            f"CHECK(event_type IN ({event_type_sql}))",
            "CHECK(schema_version = 1)",
            "CHECK(length(trim(record_json)) > 0)",
            "CHECK(length(content_hash) = 64 "
            "AND content_hash NOT GLOB '*[^0-9a-f]*')",
        ),
        "memory_event_entities": (
            "CHECK(length(trim(record_type)) > 0)",
            "CHECK(length(trim(entity_id)) > 0)",
        ),
    }
    expected_indexes = {
        "memory_events": {
            (None, 1, "u", 0, (("memory_event_id", 0, "BINARY"),)),
            (None, 1, "u", 0, (("event_key_json", 0, "BINARY"),)),
            ("memory_events_operation_lookup", 0, "c", 0,
             (("scope_type", 0, "BINARY"), ("scope_id", 0, "BINARY"),
              ("operation_namespace", 0, "BINARY"), ("operation_id", 0, "BINARY"),
              ("sequence", 0, "BINARY"))),
            ("memory_events_type_lookup", 0, "c", 0,
             (("scope_type", 0, "BINARY"), ("scope_id", 0, "BINARY"),
              ("event_type", 0, "BINARY"), ("sequence", 0, "BINARY"))),
        },
        "memory_event_entities": {
            (None, 1, "pk", 0,
             (("sequence", 0, "BINARY"), ("record_type", 0, "BINARY"),
              ("entity_id", 0, "BINARY"))),
            ("memory_event_entities_lookup", 0, "c", 0,
             (("record_type", 0, "BINARY"), ("entity_id", 0, "BINARY"),
              ("sequence", 0, "BINARY"))),
        },
    }
    expected_foreign_keys = {
        "memory_events": (),
        "memory_event_entities": (
            ("memory_events", "sequence", "sequence", "NO ACTION", "RESTRICT", "NONE"),
        ),
    }
    expected_triggers = {
        "memory_events_reject_insert": f"""
            CREATE TRIGGER memory_events_reject_insert BEFORE INSERT ON memory_events
            WHEN NEW.scope_type != '{scope_type}' OR NOT EXISTS (
                SELECT 1 FROM {identity_table} WHERE id = 1 AND {identity_column} = NEW.scope_id
            ) OR EXISTS (
                SELECT 1 FROM memory_events WHERE sequence = NEW.sequence
                   OR memory_event_id = NEW.memory_event_id OR event_key_json = NEW.event_key_json
            )
            BEGIN SELECT RAISE(ABORT, 'memory_events append-only collision or owner mismatch'); END
        """,
        "memory_events_reject_update": """
            CREATE TRIGGER memory_events_reject_update BEFORE UPDATE ON memory_events
            BEGIN SELECT RAISE(ABORT, 'memory_events are append-only'); END
        """,
        "memory_events_reject_delete": """
            CREATE TRIGGER memory_events_reject_delete BEFORE DELETE ON memory_events
            BEGIN SELECT RAISE(ABORT, 'memory_events are append-only'); END
        """,
        "memory_event_entities_reject_insert": """
            CREATE TRIGGER memory_event_entities_reject_insert BEFORE INSERT ON memory_event_entities
            WHEN EXISTS (SELECT 1 FROM memory_event_entities WHERE sequence = NEW.sequence AND record_type = NEW.record_type AND entity_id = NEW.entity_id)
              OR NOT EXISTS (
                SELECT 1 FROM json_each((SELECT record_json FROM memory_events WHERE sequence = NEW.sequence), '$.entity_refs')
                WHERE json_extract(value, '$.record_type') = NEW.record_type
                  AND json_extract(value, '$.entity_id') = NEW.entity_id
              )
            BEGIN SELECT RAISE(ABORT, 'memory_event_entities append-only collision'); END
        """,
        "memory_event_entities_reject_update": """
            CREATE TRIGGER memory_event_entities_reject_update BEFORE UPDATE ON memory_event_entities
            BEGIN SELECT RAISE(ABORT, 'memory_event_entities are append-only'); END
        """,
        "memory_event_entities_reject_delete": """
            CREATE TRIGGER memory_event_entities_reject_delete BEFORE DELETE ON memory_event_entities
            BEGIN SELECT RAISE(ABORT, 'memory_event_entities are append-only'); END
        """,
    }

    try:
        for table, expected in expected_columns.items():
            actual = _sqlite_table_xinfo_signature(conn, table)
            if actual != expected:
                raise MemoryLedgerIntegrityError(f"{table} column definition is invalid")
            sql_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            raw_table_sql = None if sql_row is None else sql_row[0]
            table_sql = _canonical_schema_sql(raw_table_sql)
            if (
                _schema_has_token_sequence(raw_table_sql, ("on", "conflict"))
                or "collate" in _schema_sql_tokens(raw_table_sql)
            ):
                raise MemoryLedgerIntegrityError(
                    f"{table} SQL modifiers are outside the frozen schema"
                )
            non_check_fragments = tuple(
                fragment for fragment in required_table_fragments[table]
                if "check(" not in fragment.lower()
            )
            if any(
                _canonical_schema_sql(fragment) not in table_sql
                for fragment in non_check_fragments
            ):
                raise MemoryLedgerIntegrityError(f"{table} SQL constraints are invalid")
            expected_checks = _schema_check_signatures(
                " ".join(expected_check_sql[table])
            )
            if _schema_check_signatures(raw_table_sql) != expected_checks:
                raise MemoryLedgerIntegrityError(
                    f"{table} CHECK constraints are outside the frozen schema"
                )
            actual_indexes = set()
            for row in conn.execute(f"PRAGMA index_list({table})").fetchall():
                name, unique, origin, partial = str(row[1]), int(row[2]), str(row[3]), int(row[4])
                keys = _sqlite_index_key_signature(conn, name)
                actual_indexes.add((name if origin == "c" else None, unique, origin, partial, keys))
            if actual_indexes != expected_indexes[table]:
                raise MemoryLedgerIntegrityError(f"{table} index definition is invalid")
            foreign_keys = tuple(
                (
                    str(row[2]), str(row[3]), str(row[4]),
                    str(row[5]), str(row[6]), str(row[7]),
                )
                for row in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
            )
            if foreign_keys != expected_foreign_keys[table]:
                raise MemoryLedgerIntegrityError(
                    f"{table} foreign key definition is invalid"
                )
        ledger_tables = {"memory_events", "memory_event_entities"}
        trigger_rows = tuple(
            row for row in conn.execute(
                "SELECT name,tbl_name,sql FROM sqlite_master "
                "WHERE type='trigger' ORDER BY name"
            ).fetchall()
            if _canonical_sqlite_identifier(str(row[1])) in ledger_tables
        )
        actual_triggers = {
            str(row[0]): _canonical_schema_sql(row[2]) for row in trigger_rows
        }
        if set(actual_triggers) != set(expected_triggers):
            raise MemoryLedgerIntegrityError("memory ledger trigger set is outside the frozen schema")
        for name, expected_sql in expected_triggers.items():
            if actual_triggers[name] != _canonical_schema_sql(expected_sql):
                raise MemoryLedgerIntegrityError(f"memory ledger trigger definition is invalid: {name}")
    except sqlite3.Error as exc:
        raise MemoryLedgerIntegrityError("memory ledger schema is incomplete") from exc


def event_key_json(event: MemoryEventRecord) -> str:
    return canonical_json({
        "key_version": "memory-event-key.v1", "scope_type": event.scope_type,
        "scope_id": event.scope_id, "operation": event.operation, "event_slot": list(event.event_slot),
    })


def append_memory_event(conn: sqlite3.Connection, event: MemoryEventRecord) -> MemoryEventRecord:
    if not isinstance(event, MemoryEventRecord):
        raise MemoryLedgerError("memory ledger append requires MemoryEventRecord")
    # The dataclass is frozen, but its nested JSON values remain mutable.  Take
    # a detached canonical snapshot and re-run the full closed-schema/hash
    # validation at the public persistence boundary.  A stale hash is rejected;
    # it is never repaired implicitly.
    event = MemoryEventRecord.from_dict(parse_json_object(event.to_json()))
    row = conn.execute("SELECT memory_event_id FROM memory_events WHERE event_key_json = ?", (event_key_json(event),)).fetchone()
    if row is not None:
        existing = read_memory_event(conn, str(row[0]))
        if existing is None:
            raise MemoryLedgerIntegrityError("memory event identity index is inconsistent")
        if existing.comparison_payload() != event.comparison_payload():
            raise MemoryLedgerIdentityConflict("MEMORY_EVENT_IDENTITY_CONFLICT")
        return existing
    try:
        conn.execute(
            "INSERT INTO memory_events(sequence,memory_event_id,event_key_json,scope_type,scope_id,operation_namespace,operation_id,event_type,schema_version,record_json,content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (event.sequence, event.memory_event_id, event_key_json(event), event.scope_type, event.scope_id,
             event.operation["namespace"], event.operation["id"], event.event_type, event.schema_version,
             event.to_json(), event.content_hash),
        )
        for entity in event.entity_refs:
            conn.execute("INSERT INTO memory_event_entities(sequence,record_type,entity_id) VALUES (?,?,?)", (event.sequence, entity["record_type"], entity["entity_id"]))
    except sqlite3.IntegrityError as exc:
        raise MemoryLedgerIdentityConflict("memory event insert collision") from exc
    return event


def read_memory_event(conn: sqlite3.Connection, memory_event_id_value: str) -> MemoryEventRecord | None:
    row = conn.execute(
        "SELECT sequence,memory_event_id,event_key_json,scope_type,scope_id,"
        "operation_namespace,operation_id,event_type,schema_version,record_json,content_hash "
        "FROM memory_events WHERE memory_event_id = ?",
        (memory_event_id_value,),
    ).fetchone()
    if row is None:
        return None
    event = MemoryEventRecord.from_dict(parse_json_object(str(row["record_json"])))
    indexed = {
        "sequence": int(row["sequence"]),
        "memory_event_id": str(row["memory_event_id"]),
        "event_key_json": str(row["event_key_json"]),
        "scope_type": str(row["scope_type"]),
        "scope_id": str(row["scope_id"]),
        "operation_namespace": str(row["operation_namespace"]),
        "operation_id": str(row["operation_id"]),
        "event_type": str(row["event_type"]),
        "schema_version": int(row["schema_version"]),
        "content_hash": str(row["content_hash"]),
    }
    expected = {
        "sequence": event.sequence,
        "memory_event_id": event.memory_event_id,
        "event_key_json": event_key_json(event),
        "scope_type": event.scope_type,
        "scope_id": event.scope_id,
        "operation_namespace": event.operation["namespace"],
        "operation_id": event.operation["id"],
        "event_type": event.event_type,
        "schema_version": event.schema_version,
        "content_hash": event.content_hash,
    }
    if indexed != expected:
        raise MemoryLedgerIntegrityError("memory event indexed fields do not match record")
    validate_memory_event_indexes(conn, event)
    return event


def validate_memory_event_indexes(conn: sqlite3.Connection, event: MemoryEventRecord) -> None:
    observed = tuple(
        {"record_type": str(row[0]), "entity_id": str(row[1])}
        for row in conn.execute(
            "SELECT record_type,entity_id FROM memory_event_entities WHERE sequence = ? ORDER BY record_type,entity_id",
            (event.sequence,),
        ).fetchall()
    )
    expected = tuple(
        {"record_type": item["record_type"], "entity_id": item["entity_id"]}
        for item in event.entity_refs
    )
    if observed != expected:
        raise MemoryLedgerIntegrityError("memory event entity index does not match record")


def next_sequence(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(sequence), 0) FROM memory_events").fetchone()
    table_sequence = int(row[0])
    sqlite_row = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'memory_events'"
    ).fetchone()
    sqlite_sequence = 0 if sqlite_row is None else int(sqlite_row[0])
    value = max(table_sequence, sqlite_sequence) + 1
    if value > 9_223_372_036_854_775_807:
        raise MemoryLedgerError("memory event sequence overflow")
    return value

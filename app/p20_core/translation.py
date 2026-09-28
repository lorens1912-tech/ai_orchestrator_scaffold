"""Project-owned GAP-019 translation lifecycle. HTTP and model transport live elsewhere."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Mapping

from app.p20_core.cross_store_recovery import (
    ArtifactRoot, CrossStoreOperationPlan, CrossStoreRecoveryService,
    RecoveryInterventionRequired, RecoveryStatus,
)
from app.p20_core.local_operator import OperatorIdentity
from app.p20_core.manuscript_version import load_manuscript
from app.p20_core.project_repository import ProjectRepository, ProjectStorageError
from app.p20_core.source_promotion import current_source_master, load_source_master
from app.p20_core.translation_contract import (
    BIBLE_DECISION_KINDS, LOCALES, QA_CRITERIA, QA_MODEL_CRITERIA,
    QA_POLICY_HASH, QA_POLICY_VERSION,
    TRANSLATION_POLICY_HASH, TRANSLATION_POLICY_VERSION, SourceBinding,
    TranslationContractError, TranslationQAReport, canonical_bytes, digest,
    reduce_translation_hierarchy, signed, verify_signed,
)


_ARTIFACT_TYPES = {
    "BIBLE": "TRANSLATION_BIBLE_V1", "UNIT": "TRANSLATION_UNIT_V1",
    "VERSION": "TRANSLATION_VERSION_V1", "QA": "TRANSLATION_QA_V1",
    "CANDIDATE": "TRANSLATION_CANDIDATE_V1", "MASTER": "TARGET_MASTER_V1",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _owner(repository: ProjectRepository, locale: str) -> dict[str, Any]:
    if locale not in LOCALES:
        raise TranslationContractError("TARGET_LOCALE_UNSUPPORTED", 422)
    return {"schema_version": 1, "project_id": repository.context.project_id,
            "book_id": repository.context.book_id, "target_locale": locale}


def _id(prefix: str, value: Any) -> str:
    return prefix + "-" + digest(value)


def _source_binding(repository: ProjectRepository, locale: str,
                    source_master_id: str, source_master_hash: str,
                    *, require_current: bool = True) -> SourceBinding:
    try:
        source = load_source_master(repository, source_master_id)
        current = current_source_master(repository)
        if (source.artifact_hash != source_master_hash or
                (require_current and (current is None or
                 (current.source_master_id, current.artifact_hash) !=
                 (source_master_id, source_master_hash)))):
            raise TranslationContractError("SOURCE_MASTER_MISMATCH")
        manuscript = load_manuscript(repository, source.manuscript_id)
    except TranslationContractError:
        raise
    except Exception as exc:
        raise TranslationContractError("SOURCE_MASTER_REQUIRED", 422) from exc
    if (source.project_id, source.book_id, source.manuscript_version) != (
        repository.context.project_id, repository.context.book_id, manuscript.version
    ):
        raise TranslationContractError("SOURCE_MANUSCRIPT_MISMATCH")
    binding = SourceBinding(
        source.project_id, source.book_id, source.source_master_id, source.version,
        source.artifact_hash, manuscript.manuscript_id, manuscript.version,
        manuscript.content_hash, manuscript.source_language, locale,
    )
    binding.validate()
    return binding


def _request_hash(request: Mapping[str, Any], identity: OperatorIdentity | None = None) -> str:
    return digest({"request": dict(request), "actor": None if identity is None else identity.to_dict()})


def _reserve(repository: ProjectRepository, locale: str, kind: str, request_id: str,
             request: Mapping[str, Any], prepared: Mapping[str, Any],
             identity: OperatorIdentity | None = None) -> dict[str, Any]:
    request_hash = _request_hash(request, identity)
    with repository.gap019_transaction() as connection:
        return repository.put_gap019_request(
            locale, kind, request_id, request_hash,
            {"status": "RECORDED", "prepared": dict(prepared), "created_at": _now()},
            connection=connection,
        )


def _replace_receipt(repository: ProjectRepository, receipt: Mapping[str, Any], **changes) -> dict[str, Any]:
    with repository.gap019_transaction() as connection:
        current = repository.get_gap019_request(receipt["target_locale"],
            receipt["operation_kind"], receipt["request_id"], connection=connection)
        if current is None or current["request_hash"] != receipt["request_hash"]:
            raise TranslationContractError("TRANSLATION_REQUEST_INTEGRITY")
        updated = {**current, **changes}
        repository.update_gap019_request(receipt["target_locale"],
            receipt["operation_kind"], receipt["request_id"], current, updated,
            connection=connection)
        return updated


def _artifact_record(repository: ProjectRepository, receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Replay a pinned F-004 plan, then insert immutable domain record and CAS head."""
    prepared = receipt.get("prepared")
    if not isinstance(prepared, dict) or prepared.get("kind") not in _ARTIFACT_TYPES:
        raise TranslationContractError("TRANSLATION_RECOVERY_INTENT_INVALID")
    locale, kind, record_id = (receipt["target_locale"], prepared["kind"],
                               prepared["record_id"])
    existing = repository.get_gap019_record(locale, kind, record_id)
    if receipt.get("status") == "COMMITTED":
        if existing is None or existing["record_hash"] != receipt.get("result_hash"):
            raise TranslationContractError("TRANSLATION_RECEIPT_INTEGRITY")
        return load_translation_record(repository, locale, kind, record_id)
    if receipt.get("status") in {"HEAD_CONFLICT", "NEEDS_INTERVENTION"}:
        raise TranslationContractError(receipt["status"])
    document = prepared["document"]
    artifact_ref = f"translations/v1/{locale}/{kind.lower()}/{record_id}.json"
    raw = canonical_bytes(document)
    artifact_hash = hashlib.sha256(raw).hexdigest()
    record = signed({**document, "artifact_ref": artifact_ref,
                     "artifact_hash": artifact_hash})
    if existing is not None and existing != record:
        raise TranslationContractError("TRANSLATION_RECORD_CONFLICT")
    if kind == "MASTER":
        approval = repository.get_gap019_record(locale, "APPROVAL", document["approval_id"])
        candidate = load_translation_record(repository, locale, "CANDIDATE",
                                            document["candidate_id"])
        approved_version = load_translation_record(repository, locale, "VERSION",
                                                   document["translation_version_id"])
        if (approval is None or approval.get("decision") != "APPROVE"
                or approval.get("record_hash") != document["approval_hash"]
                or approval.get("candidate_hash") != candidate["record_hash"]
                or candidate["record_hash"] != document["candidate_hash"]
                or approved_version["record_hash"] != document["translation_hash"]
                or repository.get_gap019_record(locale, "STATUS", candidate["candidate_id"]) is not None):
            raise TranslationContractError("TARGET_APPROVAL_BINDING_MISMATCH")
    plan = CrossStoreOperationPlan(
        operation_id="gap019-" + kind.lower() + "-" + record_id,
        project_id=repository.context.project_id,
        book_id=repository.context.book_id,
        operation_type=_ARTIFACT_TYPES[kind],
        source_ref=prepared["source_ref"],
        artifact_relative_path=artifact_ref,
        artifact_bytes=raw,
        artifact_root=ArtifactRoot.PROJECT,
        expected_versions={"record_version": document["version"]},
        provenance_refs=tuple(prepared["provenance_refs"]),
    )
    try:
        outcome = CrossStoreRecoveryService(repository).execute(plan)
    except RecoveryInterventionRequired:
        _replace_receipt(repository, receipt, status="NEEDS_INTERVENTION")
        raise TranslationContractError("NEEDS_INTERVENTION") from None
    if outcome.get("status") != RecoveryStatus.COMMITTED.value:
        _replace_receipt(repository, receipt, status="RECOVERY_REQUIRED")
        raise TranslationContractError("RECOVERY_REQUIRED")
    CrossStoreRecoveryService(repository).verify_committed_readonly(plan.operation_id)
    conflict = False
    with repository.gap019_transaction() as connection:
        current = repository.get_gap019_request(locale, receipt["operation_kind"],
                                               receipt["request_id"], connection=connection)
        if current is None or current["request_hash"] != receipt["request_hash"]:
            raise TranslationContractError("TRANSLATION_REQUEST_INTEGRITY")
        if current["status"] == "COMMITTED":
            saved = repository.get_gap019_record(locale, kind, record_id, connection=connection)
            if saved != record:
                raise TranslationContractError("TRANSLATION_RECEIPT_INTEGRITY")
            return saved
        repository.put_gap019_record(locale, kind, record_id, record, connection=connection)
        head_kind = prepared.get("head_kind")
        if head_kind:
            expected = tuple(prepared["expected_head"])
            head = (record_id, record["version"], record["record_hash"])
            binding = document.get("source_binding")
            stale = (isinstance(binding, dict) and
                repository.get_gap018_source_head(connection=connection) != (
                    binding["source_master_id"], binding["source_master_version"],
                    binding["source_master_hash"],
                ))
            if kind != "BIBLE" and document.get("bible_id"):
                bible_head = repository.get_gap019_head(locale, "BIBLE", connection=connection)
                stale = stale or (bible_head[0], bible_head[2]) != (
                    document["bible_id"], document["bible_hash"])
            if kind == "MASTER":
                candidate_head = repository.get_gap019_head(locale, "CANDIDATE", connection=connection)
                version_head = repository.get_gap019_head(locale, "VERSION", connection=connection)
                stale = stale or (candidate_head[0], candidate_head[2]) != (
                    document["candidate_id"], document["candidate_hash"])
                stale = stale or (version_head[0], version_head[2]) != (
                    document["translation_version_id"], document["translation_hash"])
            if stale or not repository.cas_gap019_head(locale, head_kind, expected, head,
                                                       connection=connection):
                updated = {**current, "status": "HEAD_CONFLICT",
                           "result_kind": kind, "result_id": record_id,
                           "result_hash": record["record_hash"]}
                repository.update_gap019_request(locale, receipt["operation_kind"],
                    receipt["request_id"], current, updated, connection=connection)
                conflict = True
        if not conflict:
            updated = {**current, "status": "COMMITTED", "result_kind": kind,
                       "result_id": record_id, "result_hash": record["record_hash"]}
            repository.update_gap019_request(locale, receipt["operation_kind"],
                receipt["request_id"], current, updated, connection=connection)
    if conflict:
        raise TranslationContractError("HEAD_CONFLICT")
    return record


def recover_translation(repository: ProjectRepository, locale: str,
                        operation_kind: str, request_id: str) -> dict[str, Any]:
    receipt = repository.get_gap019_request(locale, operation_kind, request_id)
    if receipt is None:
        raise TranslationContractError("TRANSLATION_REQUEST_NOT_FOUND", 404)
    if receipt.get("prepared", {}).get("kind") in _ARTIFACT_TYPES:
        return _artifact_record(repository, receipt)
    if receipt.get("status") == "COMMITTED":
        return dict(receipt)
    raise TranslationContractError("RECOVERY_REQUIRED")


def recover_translation_invocation(repository: ProjectRepository, locale: str,
                                   operation_id: str, identity: OperatorIdentity) -> dict[str, Any]:
    """Explicitly fence an uncertain transport attempt; never infer QA/approval."""
    _owner(repository, locale)
    raw = repository.get_metadata_readonly("model_invocation.v1:" + operation_id)
    audit = json.loads(raw) if raw else None
    if (not isinstance(audit, dict)
            or audit.get("project_id") != repository.context.project_id
            or audit.get("book_id") != repository.context.book_id
            or audit.get("mode") not in {"TRANSLATE", "TRANSLATION_QA"}
            or "locale:" + locale not in audit.get("artifact_refs", [])
            or audit.get("operation_id") != operation_id):
        raise TranslationContractError("TRANSLATION_INVOCATION_NOT_FOUND", 404)
    from app.p20_core.model_provenance import recover_invocation
    recover_invocation(repository, operation_id, recovered_by=identity.operator_id)
    updated = repository.get_metadata_readonly("model_invocation.v1:" + operation_id)
    result = json.loads(updated) if updated else None
    if not isinstance(result, dict):
        raise TranslationContractError("TRANSLATION_INVOCATION_RECOVERY_FAILED")
    return {"operation_id": operation_id, "status": result.get("status"),
            "validation": result.get("validation")}


def load_translation_record(repository: ProjectRepository, locale: str,
                            kind: str, record_id: str) -> dict[str, Any]:
    value = repository.get_gap019_record(locale, kind, record_id)
    if value is None:
        raise TranslationContractError("TRANSLATION_RECORD_NOT_FOUND", 404)
    verify_signed(value, project_id=repository.context.project_id,
                  book_id=repository.context.book_id, target_locale=locale)
    if kind in _ARTIFACT_TYPES:
        ref = value["artifact_ref"]
        path = (repository.context.project_root / ref).resolve()
        path.relative_to(repository.context.project_root.resolve())
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != value["artifact_hash"]:
            raise TranslationContractError("TRANSLATION_ARTIFACT_HASH_MISMATCH")
        document = json.loads(raw)
        if document != {key: item for key, item in value.items()
                        if key not in {"artifact_ref", "artifact_hash", "record_hash"}}:
            raise TranslationContractError("TRANSLATION_ARTIFACT_RECORD_MISMATCH")
        CrossStoreRecoveryService(repository).verify_committed_readonly(
            "gap019-" + kind.lower() + "-" + record_id)
    return value


def current_translation_record(repository: ProjectRepository, locale: str,
                               kind: str) -> dict[str, Any] | None:
    record_id, version, head_hash = repository.get_gap019_head(locale, kind)
    if record_id is None:
        return None
    record = load_translation_record(repository, locale, kind, record_id)
    if (record["version"], record["record_hash"]) != (version, head_hash):
        raise TranslationContractError("TRANSLATION_HEAD_INTEGRITY")
    return {**record, "effective_status": translation_staleness(repository, record)}


def translation_staleness(repository: ProjectRepository, record: Mapping[str, Any]) -> str:
    binding = record.get("source_binding")
    if not isinstance(binding, dict):
        return "CURRENT"
    current = current_source_master(repository)
    source_head = ([None, None, None] if current is None else
                   [current.source_master_id, current.version, current.artifact_hash])
    bible_head = list(repository.get_gap019_head(record["target_locale"], "BIBLE"))
    if source_head != [binding["source_master_id"], binding["source_master_version"],
                       binding["source_master_hash"]]:
        status = "STALE_AGAINST_SOURCE"
    elif record.get("bible_id") and (bible_head[0], bible_head[2]) != (
            record["bible_id"], record["bible_hash"]):
        status = "STALE_AGAINST_BIBLE"
    else:
        return "CURRENT"
    owner = _owner(repository, record["target_locale"])
    observation = {**owner, "version": 1, "event_type": "STALENESS_OBSERVED",
                   "translation_record_hash": record["record_hash"],
                   "source_head": source_head, "bible_head": bible_head,
                   "effective_status": status}
    observation_id = _id("STALEOBS", observation)
    with repository.gap019_transaction() as connection:
        repository.put_gap019_record(record["target_locale"], "STATUS", observation_id,
                                     signed(observation), connection=connection)
    return status


def create_translation_bible(repository: ProjectRepository, locale: str,
                             request: Mapping[str, Any], identity: OperatorIdentity) -> dict[str, Any]:
    owner = _owner(repository, locale)
    request_id = str(request.get("request_id") or "")
    prior = repository.get_gap019_request(locale, "BIBLE_VERSION", request_id)
    if prior is not None:
        if prior["request_hash"] != _request_hash(request, identity):
            raise TranslationContractError("TRANSLATION_REQUEST_ID_CONFLICT")
        return _artifact_record(repository, prior)
    binding = _source_binding(repository, locale, str(request.get("source_master_id") or ""),
                              str(request.get("source_master_hash") or ""))
    expected = tuple(request.get("expected_head") or ())
    if len(expected) != 3 or repository.get_gap019_head(locale, "BIBLE") != expected:
        raise TranslationContractError("BIBLE_HEAD_CHANGED")
    entries = request.get("decision_entries")
    if not isinstance(entries, list):
        raise TranslationContractError("BIBLE_DECISIONS_INVALID", 422)
    seen: set[str] = set()
    locked: dict[tuple[str, str, str], str] = {}
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("decision_id"), str)
                or not entry["decision_id"] or entry["decision_id"] in seen
                or entry.get("kind") not in BIBLE_DECISION_KINDS
                or entry.get("target_locale") != locale
                or not isinstance(entry.get("scope"), str) or not entry["scope"].strip()
                or not isinstance(entry.get("source_form"), str) or not entry["source_form"].strip()
                or not isinstance(entry.get("target_form"), str)
                or entry.get("status") != "APPROVED"
                or not isinstance(entry.get("provenance"), dict)
                or entry.get("provenance", {}).get("authority") != "USER"
                or entry["provenance"].get("operator_id") != identity.operator_id):
            raise TranslationContractError("BIBLE_DECISIONS_INVALID", 422)
        seen.add(entry["decision_id"])
        if entry["kind"] in {"LOCKED_TERM", "DO_NOT_TRANSLATE", "PROPER_NAME", "TERM"}:
            key = (entry["kind"], entry["scope"], entry["source_form"].casefold())
            target = entry["target_form"]
            if key in locked and locked[key] != target:
                raise TranslationContractError("BIBLE_LOCKED_DECISION_CONFLICT")
            locked[key] = target
    version = 1 if expected[1] is None else expected[1] + 1
    record_id = _id("TB", {"owner": owner, "request_id": request_id})
    document = {**owner, "translation_bible_id": record_id, "version": version,
                "parent_bible_id": expected[0], "source_binding": binding.to_dict(),
                "decision_entries": entries, "created_by": identity.to_dict(),
                "created_at": _now(), "approval_ref": "local-operator:" + request_id,
                "approval_hash": digest({"request": request, "actor": identity.to_dict()})}
    receipt = _reserve(repository, locale, "BIBLE_VERSION", request_id, request,
                       {"kind": "BIBLE", "record_id": record_id, "document": document,
                        "source_ref": binding.source_master_id,
                        "provenance_refs": [binding.source_master_id,
                                            "local-operator:" + request_id],
                        "head_kind": "BIBLE", "expected_head": list(expected)}, identity)
    return _artifact_record(repository, receipt)


def start_translation(repository: ProjectRepository, locale: str,
                      request: Mapping[str, Any], identity: OperatorIdentity) -> dict[str, Any]:
    owner = _owner(repository, locale)
    request_id = str(request.get("request_id") or "")
    prior = repository.get_gap019_request(locale, "START_TRANSLATION", request_id)
    if prior is not None:
        if prior["request_hash"] != _request_hash(request, identity):
            raise TranslationContractError("TRANSLATION_REQUEST_ID_CONFLICT")
        return load_translation_record(repository, locale, "RUN", prior["result_id"])
    binding = _source_binding(repository, locale, str(request.get("source_master_id") or ""),
                              str(request.get("source_master_hash") or ""))
    bible = current_translation_record(repository, locale, "BIBLE")
    if (bible is None or bible["translation_bible_id"] != request.get("bible_id")
            or bible["record_hash"] != request.get("bible_hash")
            or bible["source_binding"] != binding.to_dict()):
        raise TranslationContractError("TRANSLATION_BIBLE_MISMATCH")
    if (request.get("policy_version"), request.get("policy_hash")) != (
        TRANSLATION_POLICY_VERSION, TRANSLATION_POLICY_HASH
    ):
        raise TranslationContractError("TRANSLATION_POLICY_MISMATCH", 422)
    expected = tuple(request.get("expected_head") or ())
    if len(expected) != 3 or repository.get_gap019_head(locale, "VERSION") != expected:
        raise TranslationContractError("TRANSLATION_HEAD_CHANGED")
    run_id = _id("TRUN", {"owner": owner, "request_id": request_id})
    document = signed({**owner, "run_id": run_id, "version": 1,
        "source_binding": binding.to_dict(), "bible_id": bible["translation_bible_id"],
        "bible_version": bible["version"], "bible_hash": bible["record_hash"],
        "policy_version": TRANSLATION_POLICY_VERSION,
        "policy_hash": TRANSLATION_POLICY_HASH, "expected_head": list(expected),
        "request_id": request_id, "requested_by": identity.to_dict(),
        "created_at": _now(), "execution_status": "RECORDED"})
    with repository.gap019_transaction() as connection:
        if repository.get_gap018_source_head(connection=connection) != (
            binding.source_master_id, binding.source_master_version,
            binding.source_master_hash,
        ) or repository.get_gap019_head(locale, "BIBLE", connection=connection) != (
            bible["translation_bible_id"], bible["version"], bible["record_hash"],
        ) or repository.get_gap019_head(locale, "VERSION", connection=connection) != expected:
            raise TranslationContractError("TRANSLATION_INPUT_CHANGED")
        receipt = repository.put_gap019_request(locale, "START_TRANSLATION", request_id,
            _request_hash(request, identity), {"status": "COMMITTED", "result_kind": "RUN",
                "result_id": run_id, "result_hash": document["record_hash"]},
            connection=connection)
        repository.put_gap019_record(locale, "RUN", run_id, document, connection=connection)
    return load_translation_record(repository, locale, "RUN", receipt["result_id"])


def _source_bundle(repository: ProjectRepository, binding: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = load_source_master(repository, binding["source_master_id"])
    manuscript = load_manuscript(repository, binding["manuscript_id"])
    if (source.artifact_hash != binding["source_master_hash"]
            or source.version != binding["source_master_version"]
            or manuscript.version != binding["manuscript_version"]
            or manuscript.content_hash != binding["manuscript_content_hash"]):
        raise TranslationContractError("SOURCE_BINDING_MISMATCH")
    artifact = json.loads((repository.context.project_root / source.artifact_ref).read_text(encoding="utf-8"))
    content = artifact["content"]
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != manuscript.content_hash:
        raise TranslationContractError("SOURCE_CONTENT_HASH_MISMATCH")
    # The sealed manifest pins exact chapter artifact/text hashes. A changed
    # chapter file cannot be used to invent historical chapter boundaries.
    chapters: list[dict[str, Any]] = []
    offset = 0
    for index, selected in enumerate(manuscript.chapter_version_refs):
        path = (repository.context.storage_root / selected.artifact_ref).resolve()
        path.relative_to((repository.context.book_root / "chapters").resolve())
        raw = path.read_bytes()
        document = json.loads(raw)
        text = document["text"]
        if (hashlib.sha256(raw).hexdigest() != selected.artifact_sha256
                or hashlib.sha256(text.encode("utf-8")).hexdigest() != selected.text_sha256
                or document.get("chapter_id") != selected.chapter_id
                or document.get("version") != selected.version):
            raise TranslationContractError("SOURCE_CHAPTER_HASH_MISMATCH")
        raw_text = text.encode("utf-8")
        if content.encode("utf-8")[offset:offset + len(raw_text)] != raw_text:
            raise TranslationContractError("SOURCE_CHAPTER_SPAN_MISMATCH")
        end = offset + len(raw_text)
        if index < len(manuscript.chapter_version_refs) - 1:
            if content.encode("utf-8")[end:end + 2] != b"\n\n":
                raise TranslationContractError("SOURCE_CHAPTER_SEPARATOR_MISMATCH")
            end += 2
        chapters.append({"chapter_id": selected.chapter_id, "chapter_version": selected.version,
                         "chapter_artifact_ref": selected.artifact_ref,
                         "chapter_artifact_hash": selected.artifact_sha256,
                         "chapter_text_hash": selected.text_sha256,
                         "start_byte": offset, "end_byte": end})
        offset = end
    if offset != len(content.encode("utf-8")):
        raise TranslationContractError("SOURCE_CHAPTER_COVERAGE_MISMATCH")
    return artifact, chapters


def record_translation_unit(repository: ProjectRepository, locale: str,
                            request: Mapping[str, Any], *, identity: OperatorIdentity | None = None) -> dict[str, Any]:
    owner = _owner(repository, locale)
    request_id = str(request.get("request_id") or "")
    prior = repository.get_gap019_request(locale, "TRANSLATION_UNIT", request_id)
    if prior is not None:
        if prior["request_hash"] != _request_hash(request, identity):
            raise TranslationContractError("TRANSLATION_REQUEST_ID_CONFLICT")
        return _artifact_record(repository, prior)
    run = load_translation_record(repository, locale, "RUN", str(request.get("run_id") or ""))
    if translation_staleness(repository, run) != "CURRENT":
        raise TranslationContractError("TRANSLATION_INPUT_STALE")
    artifact, chapters = _source_bundle(repository, run["source_binding"])
    start, end = request.get("start_byte"), request.get("end_byte")
    if (type(start) is not int or type(end) is not int or start < 0 or end <= start
            or end > len(artifact["content"].encode("utf-8"))):
        raise TranslationContractError("SOURCE_SPAN_INVALID", 422)
    chapter = next((item for item in chapters if item["start_byte"] <= start
                    and end <= item["end_byte"]), None)
    if chapter is None or chapter["chapter_id"] != request.get("chapter_id"):
        raise TranslationContractError("SOURCE_CHAPTER_SPAN_MISMATCH")
    span = artifact["content"].encode("utf-8")[start:end]
    try:
        source_text = span.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TranslationContractError("SOURCE_SPAN_INVALID", 422) from exc
    target = request.get("target_text")
    if not isinstance(target, str) or not target.strip():
        raise TranslationContractError("TRANSLATION_UNIT_TEXT_REQUIRED", 422)
    origin = request.get("origin")
    invocations = request.get("model_invocation_refs") or []
    if origin == "MODEL":
        if not isinstance(invocations, list) or not invocations:
            raise TranslationContractError("TRANSLATION_MODEL_PROVENANCE_MISSING")
        for operation_id in invocations:
            raw = repository.get_metadata_readonly("model_invocation.v1:" + operation_id)
            audit = json.loads(raw) if raw else None
            if (not isinstance(audit, dict) or audit.get("validation") != "VALID"
                    or audit.get("project_id") != owner["project_id"]
                    or audit.get("book_id") != owner["book_id"]
                    or audit.get("mode") != "TRANSLATE"
                    or audit.get("run_id") != run["run_id"]
                    or audit.get("operation_id") != operation_id
                    or audit.get("output_hash") != hashlib.sha256(target.encode("utf-8")).hexdigest()
                    or request.get("context_ref") != audit.get("context_package_id")
                    or request.get("context_hash") != audit.get("context_hash")
                    or run["source_binding"]["source_master_id"] not in audit.get("artifact_refs", [])
                    or run["bible_id"] not in audit.get("artifact_refs", [])
                    or "locale:" + locale not in audit.get("artifact_refs", [])):
                raise TranslationContractError("TRANSLATION_MODEL_PROVENANCE_INVALID")
    elif origin == "USER_EDIT":
        if identity is None or invocations:
            raise TranslationContractError("TRANSLATION_EDIT_AUTHORITY_INVALID")
    else:
        raise TranslationContractError("TRANSLATION_UNIT_ORIGIN_INVALID", 422)
    source_hash = hashlib.sha256(span).hexdigest()
    if request.get("source_span_hash") != source_hash:
        raise TranslationContractError("SOURCE_SPAN_HASH_MISMATCH")
    parent_unit_id = request.get("parent_unit_id")
    unit_version = 1
    if parent_unit_id is not None:
        parent = load_translation_record(repository, locale, "UNIT", parent_unit_id)
        if (parent["run_id"] != run["run_id"]
                or parent["source_binding"] != run["source_binding"]
                or (parent["start_byte"], parent["end_byte"], parent["source_span_hash"])
                != (start, end, source_hash)):
            raise TranslationContractError("TRANSLATION_REVISION_SOURCE_MISMATCH")
        unit_version = parent["version"] + 1
    record_id = _id("TU", {"owner": owner, "request_id": request_id})
    document = {**owner, "unit_id": record_id, "version": unit_version,
                "run_id": run["run_id"], "source_binding": run["source_binding"],
                "bible_id": run["bible_id"], "bible_version": run["bible_version"],
                "bible_hash": run["bible_hash"], "policy_version": run["policy_version"],
                "policy_hash": run["policy_hash"], **chapter,
                "start_byte": start, "end_byte": end, "source_span_hash": source_hash,
                "source_text": source_text, "target_text": target,
                "target_text_hash": hashlib.sha256(target.encode("utf-8")).hexdigest(),
                "origin": origin, "parent_unit_id": parent_unit_id,
                "context_ref": request.get("context_ref"),
                "context_hash": request.get("context_hash"),
                "model_invocation_refs": invocations,
                "edited_by": None if identity is None else identity.to_dict(),
                "created_at": _now()}
    receipt = _reserve(repository, locale, "TRANSLATION_UNIT", request_id, request,
                       {"kind": "UNIT", "record_id": record_id, "document": document,
                        "source_ref": run["source_binding"]["source_master_id"],
                        "provenance_refs": [run["source_binding"]["source_master_id"],
                            run["bible_id"], *invocations]}, identity)
    return _artifact_record(repository, receipt)


def seal_translation_version(repository: ProjectRepository, locale: str,
                             request: Mapping[str, Any]) -> dict[str, Any]:
    owner = _owner(repository, locale)
    request_id = str(request.get("request_id") or "")
    prior = repository.get_gap019_request(locale, "SEAL_TRANSLATION", request_id)
    if prior is not None:
        if prior["request_hash"] != _request_hash(request):
            raise TranslationContractError("TRANSLATION_REQUEST_ID_CONFLICT")
        return _artifact_record(repository, prior)
    run = load_translation_record(repository, locale, "RUN", str(request.get("run_id") or ""))
    if translation_staleness(repository, run) != "CURRENT":
        raise TranslationContractError("TRANSLATION_INPUT_STALE")
    unit_ids = request.get("unit_ids")
    if not isinstance(unit_ids, list) or not unit_ids or len(set(unit_ids)) != len(unit_ids):
        raise TranslationContractError("TRANSLATION_UNIT_LIST_INVALID", 422)
    units = [load_translation_record(repository, locale, "UNIT", item) for item in unit_ids]
    source_artifact, chapters = _source_bundle(repository, run["source_binding"])
    source_length = len(source_artifact["content"].encode("utf-8"))
    cursor = 0
    for unit in units:
        if unit["run_id"] != run["run_id"]:
            raise TranslationContractError("TRANSLATION_UNIT_RUN_MISMATCH")
        if (unit["source_binding"] != run["source_binding"]
                or unit["bible_hash"] != run["bible_hash"]
                or unit["policy_hash"] != run["policy_hash"]
                or unit["start_byte"] != cursor
                or unit["end_byte"] <= cursor):
            raise TranslationContractError("TRANSLATION_UNIT_COVERAGE_INVALID")
        cursor = unit["end_byte"]
    if cursor != source_length:
        raise TranslationContractError("TRANSLATION_UNIT_COVERAGE_INCOMPLETE")
    expected = tuple(request.get("expected_head") or ())
    if len(expected) != 3 or repository.get_gap019_head(locale, "VERSION") != expected:
        raise TranslationContractError("TRANSLATION_HEAD_CHANGED")
    version = 1 if expected[1] is None else expected[1] + 1
    parent = request.get("parent_version_id")
    if parent != expected[0]:
        raise TranslationContractError("TRANSLATION_PARENT_MISMATCH")
    record_id = _id("TV", {"owner": owner, "request_id": request_id})
    target_text = "".join(unit["target_text"] for unit in units)
    document = {**owner, "translation_version_id": record_id, "version": version,
                "parent_version_id": parent, "run_id": run["run_id"],
                "source_binding": run["source_binding"], "bible_id": run["bible_id"],
                "bible_version": run["bible_version"], "bible_hash": run["bible_hash"],
                "policy_version": run["policy_version"], "policy_hash": run["policy_hash"],
                "unit_ids": unit_ids,
                "unit_hashes": [unit["record_hash"] for unit in units],
                "source_chapters": chapters,
                "target_text": target_text,
                "target_text_hash": hashlib.sha256(target_text.encode("utf-8")).hexdigest(),
                "status": "SEALED", "created_at": _now()}
    receipt = _reserve(repository, locale, "SEAL_TRANSLATION", request_id, request,
                       {"kind": "VERSION", "record_id": record_id, "document": document,
                        "source_ref": run["source_binding"]["source_master_id"],
                        "provenance_refs": [run["source_binding"]["source_master_id"],
                                            run["bible_id"], *unit_ids],
                        "head_kind": "VERSION", "expected_head": list(expected)})
    return _artifact_record(repository, receipt)


def create_translation_qa_report(repository: ProjectRepository, locale: str,
                                 request: Mapping[str, Any]) -> TranslationQAReport:
    """Persist the Phase 2 QA result only after exact coverage and provenance checks."""
    owner = _owner(repository, locale)
    request_id = str(request.get("request_id") or "")
    prior = repository.get_gap019_request(locale, "TRANSLATION_QA", request_id)
    if prior is not None:
        if prior["request_hash"] != _request_hash(request):
            raise TranslationContractError("TRANSLATION_REQUEST_ID_CONFLICT")
        return TranslationQAReport.from_dict(_artifact_record(repository, prior))
    version = load_translation_record(repository, locale, "VERSION",
                                      str(request.get("translation_version_id") or ""))
    if request.get("translation_hash") != version["record_hash"]:
        raise TranslationContractError("TRANSLATION_VERSION_HASH_MISMATCH")
    coverage, findings = request.get("coverage"), request.get("findings")
    if not isinstance(coverage, list) or not isinstance(findings, list):
        raise TranslationContractError("TRANSLATION_QA_INPUT_INVALID", 422)
    units = {unit_id: load_translation_record(repository, locale, "UNIT", unit_id)
             for unit_id in version["unit_ids"]}
    valid = True
    invocation_refs: set[str] = set()
    for item in coverage:
        unit = units.get(item.get("unit_id")) if isinstance(item, dict) else None
        if unit is None or (item.get("source_hash"), item.get("target_hash")) != (
            unit["source_span_hash"], unit["target_text_hash"]
        ):
            valid = False
            break
        invocation_ref = item.get("invocation_ref")
        if invocation_ref:
            raw = repository.get_metadata_readonly("model_invocation.v1:" + invocation_ref)
            audit = json.loads(raw) if raw else None
            if (not isinstance(audit, dict) or audit.get("validation") != "VALID"
                    or audit.get("project_id") != owner["project_id"]
                    or audit.get("book_id") != owner["book_id"]
                    or audit.get("operation_id") != invocation_ref
                    or audit.get("context_package_id") != item.get("context_ref")
                    or audit.get("context_hash") != item.get("context_hash")
                    or audit.get("mode") != "TRANSLATION_QA"
                    or version["translation_version_id"] not in audit.get("artifact_refs", [])
                    or item["unit_id"] not in audit.get("artifact_refs", [])
                    or version["bible_id"] not in audit.get("artifact_refs", [])
                    or version["source_binding"]["source_master_id"] not in audit.get("artifact_refs", [])
                    or "locale:" + locale not in audit.get("artifact_refs", [])):
                valid = False
                break
            invocation_refs.add(invocation_ref)
        elif (item.get("criterion") in QA_MODEL_CRITERIA
              or item.get("evaluator_kind") != "LOCAL_DETERMINISTIC"
              or not item.get("algorithm_version")):
            valid = False
            break
    if valid:
        coverage_by_key = {(item["unit_id"], item["criterion"]): item for item in coverage}
        for finding in findings:
            if not isinstance(finding, dict):
                valid = False
                break
            unit = units.get(finding.get("unit_id"))
            covered = coverage_by_key.get((finding.get("unit_id"), finding.get("criterion")))
            if (unit is None or covered is None
                    or (finding.get("source_hash"), finding.get("target_hash")) != (
                        unit["source_span_hash"], unit["target_text_hash"])
                    or (finding.get("source_start_byte"), finding.get("source_end_byte")) != (
                        unit["start_byte"], unit["end_byte"])
                    or any(finding.get(field) != covered.get(field) for field in (
                        "context_ref", "context_hash", "invocation_ref",
                        "evaluator_kind", "algorithm_version"))):
                valid = False
                break
    chapter_units: dict[str, tuple[str, ...]] = {}
    for chapter in version["source_chapters"]:
        chapter_id = chapter["chapter_id"]
        chapter_units[chapter_id] = tuple(
            unit_id for unit_id in version["unit_ids"]
            if units[unit_id]["chapter_id"] == chapter_id)
    if set(chapter_units) != {unit["chapter_id"] for unit in units.values()}:
        raise TranslationContractError("TRANSLATION_QA_CHAPTER_COVERAGE_INVALID")
    kept_coverage = coverage if valid else []
    kept_findings = findings if valid else []
    chapter_results, book_result = reduce_translation_hierarchy(
        chapter_units, tuple(kept_coverage), tuple(kept_findings))
    execution = book_result["execution_status"]
    validation = book_result["validation_status"]
    decision = book_result["quality_decision"]
    reevaluation_of = request.get("reevaluation_of")
    report_version = 1
    if reevaluation_of:
        old = TranslationQAReport.from_dict(load_translation_record(
            repository, locale, "QA", reevaluation_of))
        if old.translation_version_id != version["translation_version_id"]:
            raise TranslationContractError("TRANSLATION_QA_REEVALUATION_MISMATCH")
        report_version = old.version + 1
    record_id = _id("TQA", {"owner": owner, "request_id": request_id})
    document = {**owner, "qa_id": record_id, "version": report_version,
                "translation_version_id": version["translation_version_id"],
                "translation_hash": version["record_hash"],
                "source_binding": version["source_binding"],
                "bible_id": version["bible_id"], "bible_hash": version["bible_hash"],
                "policy_version": QA_POLICY_VERSION, "policy_hash": QA_POLICY_HASH,
                "criteria": list(QA_CRITERIA), "coverage": kept_coverage,
                "findings": kept_findings,
                "chapter_results": chapter_results, "book_result": book_result,
                "model_invocation_refs": sorted(invocation_refs),
                "execution_status": execution, "validation_status": validation,
                "quality_decision": decision, "reevaluation_of": reevaluation_of,
                "created_at": _now()}
    receipt = _reserve(repository, locale, "TRANSLATION_QA", request_id, request,
                       {"kind": "QA", "record_id": record_id, "document": document,
                        "source_ref": version["translation_version_id"],
                        "provenance_refs": [version["translation_version_id"],
                                            version["bible_id"], *sorted(invocation_refs)]})
    return TranslationQAReport.from_dict(_artifact_record(repository, receipt))


def create_translation_candidate(repository: ProjectRepository, locale: str,
                                 request: Mapping[str, Any]) -> dict[str, Any]:
    owner = _owner(repository, locale)
    request_id = str(request.get("request_id") or "")
    prior = repository.get_gap019_request(locale, "TRANSLATION_CANDIDATE", request_id)
    if prior is not None:
        if prior["request_hash"] != _request_hash(request):
            raise TranslationContractError("TRANSLATION_REQUEST_ID_CONFLICT")
        return _artifact_record(repository, prior)
    report = TranslationQAReport.from_dict(load_translation_record(
        repository, locale, "QA", str(request.get("qa_id") or "")))
    if ((report.execution_status, report.validation_status, report.quality_decision)
            != ("COMPLETED", "VALID", "ACCEPT")
            or report.record_hash != request.get("qa_hash")):
        raise TranslationContractError("TRANSLATION_QA_NOT_ACCEPTED")
    version = load_translation_record(repository, locale, "VERSION",
                                      report.translation_version_id)
    if (version["record_hash"] != report.translation_hash
            or translation_staleness(repository, version) != "CURRENT"
            or repository.get_gap019_head(locale, "VERSION") != (
                version["translation_version_id"], version["version"],
                version["record_hash"],
            )):
        raise TranslationContractError("TRANSLATION_INPUT_STALE")
    expected = tuple(request.get("expected_head") or ())
    if len(expected) != 3 or repository.get_gap019_head(locale, "CANDIDATE") != expected:
        raise TranslationContractError("TRANSLATION_CANDIDATE_HEAD_CHANGED")
    candidate_version = 1 if expected[1] is None else expected[1] + 1
    record_id = _id("TC", {"owner": owner, "request_id": request_id})
    document = {**owner, "candidate_id": record_id, "version": candidate_version,
                "parent_candidate_id": expected[0],
                "translation_version_id": version["translation_version_id"],
                "translation_hash": version["record_hash"],
                "qa_id": report.qa_id, "qa_hash": report.record_hash,
                "source_binding": version["source_binding"],
                "bible_id": version["bible_id"], "bible_hash": version["bible_hash"],
                "target_text_hash": version["target_text_hash"],
                "status": "CANDIDATE_READY", "created_at": _now()}
    receipt = _reserve(repository, locale, "TRANSLATION_CANDIDATE", request_id, request,
                       {"kind": "CANDIDATE", "record_id": record_id,
                        "document": document, "source_ref": version["translation_version_id"],
                        "provenance_refs": [version["translation_version_id"], report.qa_id],
                        "head_kind": "CANDIDATE", "expected_head": list(expected)})
    return _artifact_record(repository, receipt)


def revise_translation_version(repository: ProjectRepository, locale: str,
                               request: Mapping[str, Any], identity: OperatorIdentity) -> dict[str, Any]:
    """Target only QA findings; unchanged units retain exact immutable IDs/hashes."""
    _owner(repository, locale)
    parent = load_translation_record(repository, locale, "VERSION",
                                     str(request.get("parent_version_id") or ""))
    report = TranslationQAReport.from_dict(load_translation_record(
        repository, locale, "QA", str(request.get("qa_id") or "")))
    if (report.quality_decision != "REVISE"
            or report.translation_version_id != parent["translation_version_id"]
            or report.record_hash != request.get("qa_hash")
            or translation_staleness(repository, parent) != "CURRENT"):
        raise TranslationContractError("TRANSLATION_REVISION_QA_MISMATCH")
    expected = tuple(request.get("expected_head") or ())
    if expected != (parent["translation_version_id"], parent["version"], parent["record_hash"]):
        raise TranslationContractError("TRANSLATION_REVISION_HEAD_MISMATCH")
    replacements = request.get("replacements")
    if not isinstance(replacements, list) or not replacements:
        raise TranslationContractError("TRANSLATION_REVISION_TARGETS_REQUIRED", 422)
    affected = {item["unit_id"] for item in report.findings if item.get("repairable")}
    replacement_ids = [item.get("unit_id") for item in replacements if isinstance(item, dict)]
    if (not replacement_ids or len(set(replacement_ids)) != len(replacement_ids)
            or not set(replacement_ids) <= affected):
        raise TranslationContractError("TRANSLATION_REVISION_TARGET_MISMATCH")
    revised: dict[str, str] = {}
    for index, item in enumerate(replacements):
        old = load_translation_record(repository, locale, "UNIT", item["unit_id"])
        new = record_translation_unit(repository, locale, {
            "request_id": str(request["request_id"]) + f"-unit-{index}",
            "run_id": parent["run_id"], "chapter_id": old["chapter_id"],
            "start_byte": old["start_byte"], "end_byte": old["end_byte"],
            "source_span_hash": old["source_span_hash"],
            "target_text": item["target_text"], "origin": "USER_EDIT",
            "parent_unit_id": old["unit_id"],
        }, identity=identity)
        revised[old["unit_id"]] = new["unit_id"]
    return seal_translation_version(repository, locale, {
        "request_id": str(request["request_id"]) + "-seal",
        "run_id": parent["run_id"],
        "unit_ids": [revised.get(unit_id, unit_id) for unit_id in parent["unit_ids"]],
        "parent_version_id": parent["translation_version_id"],
        "expected_head": list(expected),
    })


def issue_target_review(repository: ProjectRepository, locale: str,
                        candidate_id: str, identity: OperatorIdentity,
                        *, ttl_seconds: int) -> dict[str, Any]:
    owner = _owner(repository, locale)
    if not 1 <= ttl_seconds <= 900:
        raise TranslationContractError("INVALID_CHALLENGE_TTL", 422)
    candidate = load_translation_record(repository, locale, "CANDIDATE", candidate_id)
    translation_version = load_translation_record(repository, locale, "VERSION",
                                                  candidate["translation_version_id"])
    if (translation_staleness(repository, candidate) != "CURRENT"
            or repository.get_gap019_head(locale, "CANDIDATE") != (
                candidate_id, candidate["version"], candidate["record_hash"])
            or repository.get_gap019_head(locale, "VERSION") != (
                translation_version["translation_version_id"], translation_version["version"],
                translation_version["record_hash"])
            or repository.get_gap019_record(locale, "STATUS", candidate_id) is not None):
        raise TranslationContractError("TRANSLATION_CANDIDATE_STALE_OR_REJECTED")
    expected = repository.get_gap019_head(locale, "MASTER")
    challenge_id = "TREV-" + secrets.token_hex(32)
    review = signed({**owner, "challenge_id": challenge_id, "version": 1,
                     "candidate_id": candidate_id,
                     "candidate_hash": candidate["record_hash"],
                     "source_binding": candidate["source_binding"],
                     "bible_id": candidate["bible_id"],
                     "bible_hash": candidate["bible_hash"],
                     "expected_head": list(expected),
                     "operator": identity.to_dict(),
                     "expires_at": time.time() + ttl_seconds,
                     "created_at": _now()})
    with repository.gap019_transaction() as connection:
        if (repository.get_gap019_head(locale, "MASTER", connection=connection) != expected
                or repository.get_gap019_record(locale, "STATUS", candidate_id,
                                                 connection=connection) is not None):
            raise TranslationContractError("TARGET_MASTER_HEAD_CHANGED")
        repository.put_gap019_record(locale, "REVIEW", challenge_id, review,
                                     connection=connection)
    return review


def decide_target_candidate(repository: ProjectRepository, locale: str,
                            candidate_id: str, request: Mapping[str, Any],
                            identity: OperatorIdentity) -> dict[str, Any]:
    owner = _owner(repository, locale)
    request_id = str(request.get("request_id") or "")
    if request.get("decision") not in {"APPROVE", "REJECT"}:
        raise TranslationContractError("TARGET_DECISION_INVALID", 422)
    prior = repository.get_gap019_request(locale, "TARGET_DECISION", request_id)
    if prior is not None:
        if prior["request_hash"] != _request_hash(request, identity):
            raise TranslationContractError("TRANSLATION_REQUEST_ID_CONFLICT")
        if prior.get("result_kind") == "APPROVAL":
            return load_translation_record(repository, locale, "APPROVAL", prior["result_id"])
        return _artifact_record(repository, prior)
    candidate = load_translation_record(repository, locale, "CANDIDATE", candidate_id)
    translation_version = load_translation_record(repository, locale, "VERSION",
                                                  candidate["translation_version_id"])
    if (translation_staleness(repository, candidate) != "CURRENT"
            or repository.get_gap019_head(locale, "CANDIDATE") != (
                candidate_id, candidate["version"], candidate["record_hash"])
            or repository.get_gap019_head(locale, "VERSION") != (
                translation_version["translation_version_id"], translation_version["version"],
                translation_version["record_hash"])
            or repository.get_gap019_record(locale, "STATUS", candidate_id) is not None):
        raise TranslationContractError("TRANSLATION_CANDIDATE_STALE_OR_REJECTED")
    challenge_id = str(request.get("challenge_id") or "")
    review = load_translation_record(repository, locale, "REVIEW", challenge_id)
    if (review["candidate_id"] != candidate_id
            or review["candidate_hash"] != request.get("candidate_hash")
            or review["candidate_hash"] != candidate["record_hash"]
            or review["operator"] != identity.to_dict()
            or review["expected_head"] != request.get("expected_head")
            or review["expires_at"] <= time.time()):
        raise TranslationContractError("TARGET_REVIEW_BINDING_MISMATCH")
    approval_id = _id("TAP", {"owner": owner, "request_id": request_id})
    approval = signed({**owner, "approval_id": approval_id, "version": 1,
        "candidate_id": candidate_id, "candidate_hash": candidate["record_hash"],
        "translation_version_id": candidate["translation_version_id"],
        "translation_hash": candidate["translation_hash"],
        "qa_id": candidate["qa_id"], "qa_hash": candidate["qa_hash"],
        "source_binding": candidate["source_binding"],
        "bible_id": candidate["bible_id"], "bible_hash": candidate["bible_hash"],
        "decision": request["decision"], "operator": identity.to_dict(),
        "challenge_id": challenge_id, "request_id": request_id,
        "expected_head": review["expected_head"], "decided_at": _now()})
    master_id = _id("TM", {"owner": owner, "approval_hash": approval["record_hash"]})
    expected = tuple(review["expected_head"])
    master_document = {**owner, "target_master_id": master_id,
        "version": 1 if expected[1] is None else expected[1] + 1,
        "parent_master_id": expected[0], "candidate_id": candidate_id,
        "candidate_hash": candidate["record_hash"],
        "translation_version_id": candidate["translation_version_id"],
        "translation_hash": candidate["translation_hash"],
        "qa_id": candidate["qa_id"], "qa_hash": candidate["qa_hash"],
        "source_binding": candidate["source_binding"],
        "bible_id": candidate["bible_id"], "bible_hash": candidate["bible_hash"],
        "approval_id": approval_id, "approval_hash": approval["record_hash"],
        "approved_by_user": True, "approved_at": approval["decided_at"],
        "status": "TARGET_COMMITTED", "created_at": _now()}
    prepared = {"kind": "MASTER", "record_id": master_id,
        "document": master_document, "source_ref": candidate_id,
        "provenance_refs": [candidate_id, approval_id, candidate["qa_id"]],
        "head_kind": "MASTER", "expected_head": list(expected)}
    with repository.gap019_transaction() as connection:
        if (repository.get_gap019_record(locale, "REVIEW_USE", challenge_id,
                                          connection=connection) is not None
                or repository.get_gap019_record(locale, "STATUS", candidate_id,
                                                 connection=connection) is not None
                or repository.get_gap019_head(locale, "MASTER", connection=connection) != expected):
            raise TranslationContractError("TARGET_REVIEW_USED_OR_STALE")
        repository.put_gap019_record(locale, "APPROVAL", approval_id, approval,
                                     connection=connection)
        repository.put_gap019_record(locale, "REVIEW_USE", challenge_id,
            signed({**owner, "version": 1, "challenge_id": challenge_id,
                    "approval_id": approval_id, "used_at": approval["decided_at"]}),
            connection=connection)
        if request["decision"] == "REJECT":
            repository.put_gap019_record(locale, "STATUS", candidate_id,
                signed({**owner, "version": 1, "candidate_id": candidate_id,
                        "status": "REJECTED_BY_USER", "approval_id": approval_id}),
                connection=connection)
            repository.put_gap019_request(locale, "TARGET_DECISION", request_id,
                _request_hash(request, identity), {"status": "COMMITTED",
                    "result_kind": "APPROVAL", "result_id": approval_id,
                    "result_hash": approval["record_hash"]}, connection=connection)
            return approval
        receipt = repository.put_gap019_request(locale, "TARGET_DECISION", request_id,
            _request_hash(request, identity), {"status": "RECORDED",
                "prepared": prepared, "created_at": approval["decided_at"]},
            connection=connection)
    return _artifact_record(repository, receipt)

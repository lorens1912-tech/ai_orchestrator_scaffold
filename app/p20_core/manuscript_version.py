"""GAP-018 project-owned, immutable manuscript input and sealing."""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.p20_core.book_bible_contract import (
    BOOK_BIBLE_CONTRACT_VERSION,
    BookBibleContractError,
    validate_book_bible_payload_or_raise,
)
from app.p20_core.canon_service import canonical_proposal_hash
from app.p20_core.cross_store_recovery import (
    ArtifactRoot,
    CrossStoreOperationPlan,
    CrossStoreRecoveryService,
    RecoveryStatus,
)
from app.p20_core.evaluation import EvaluationError, find_evaluation
from app.p20_core.project_repository import ProjectRepository, ProjectStorageError


SCHEMA_VERSION = 1
COMPOSITION_POLICY_VERSION = "GAP018_UTF8_DOUBLE_LF_V1"
_OPERATION_TYPE = "MANUSCRIPT_VERSION_V1"
_SEPARATOR = "\n\n"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_json(value: Any) -> str:
    return _sha(_json(value).encode("utf-8"))


def _hash_ok(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


@dataclass(frozen=True)
class Blocker:
    code: str
    reason: str


@dataclass(frozen=True)
class ValidationResult:
    status: str
    blockers: tuple[Blocker, ...] = ()


class ManuscriptBlocked(ValueError):
    def __init__(self, blockers: tuple[Blocker, ...]):
        self.blockers = blockers
        super().__init__(", ".join(item.code for item in blockers))


class ManuscriptIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class ChapterSelection:
    chapter_id: str
    version: int
    artifact_ref: str
    artifact_sha256: str
    text_sha256: str
    evaluation_id: str
    evaluation_record_hash: str
    chapter_commit_operation_id: str
    canonical_commit_operation_id: str


@dataclass(frozen=True)
class CompositionDeclaration:
    version: str
    project_id: str
    book_id: str
    ordered_chapter_ids: tuple[str, ...]


@dataclass(frozen=True)
class SealRequest:
    project_id: str
    book_id: str
    version: int
    chapter_versions: tuple[ChapterSelection, ...]
    composition: CompositionDeclaration
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
    composition_policy_version: str = COMPOSITION_POLICY_VERSION


@dataclass(frozen=True)
class ManuscriptVersion:
    schema_version: int
    manuscript_id: str
    project_id: str
    book_id: str
    series_id: str | None
    version: int
    parent_manuscript_id: str | None
    chapter_version_refs: tuple[ChapterSelection, ...]
    composition_declaration_version: str
    composition_declaration_hash: str
    book_bible_version: str
    book_bible_hash: str
    book_bible_source_ref: str
    book_bible_snapshot_ref: str
    canon_version_or_revision: str
    canon_snapshot_hash: str
    canon_source_ref: str
    canon_snapshot_ref: str
    composition_policy_version: str
    source_language: str
    content_hash: str
    manifest_hash: str
    artifact_ref: str
    status: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["chapter_version_refs"] = [asdict(item) for item in self.chapter_version_refs]
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ManuscriptVersion:
        try:
            data = dict(value)
            data["chapter_version_refs"] = tuple(
                ChapterSelection(**item) for item in data["chapter_version_refs"]
            )
            record = cls(**data)
        except (KeyError, TypeError, ValueError) as exc:
            raise ManuscriptIntegrityError("manuscript record is incomplete") from exc
        if record.schema_version != SCHEMA_VERSION or record.status != "SEALED":
            raise ManuscriptIntegrityError("manuscript schema or status is invalid")
        if record.manifest_hash != _hash_json(_manifest_payload(record)):
            raise ManuscriptIntegrityError("manuscript manifest hash mismatch")
        if record.manuscript_id != "MV-" + record.manifest_hash:
            raise ManuscriptIntegrityError("manuscript identity mismatch")
        if record.artifact_ref != _artifact_ref(record.manuscript_id):
            raise ManuscriptIntegrityError("manuscript artifact reference mismatch")
        return record


@dataclass(frozen=True)
class _Prepared:
    record: ManuscriptVersion
    content: str
    book_bible_bytes: bytes
    canon_bytes: bytes


def _artifact_ref(manuscript_id: str) -> str:
    return "manuscripts/v1/" + manuscript_id + ".json"


def _manifest_payload(record: ManuscriptVersion) -> dict[str, Any]:
    return {
        "schema_version": record.schema_version,
        "project_id": record.project_id,
        "book_id": record.book_id,
        "series_id": record.series_id,
        "version": record.version,
        "parent_manuscript_id": record.parent_manuscript_id,
        "chapter_version_refs": [asdict(item) for item in record.chapter_version_refs],
        "composition_declaration_version": record.composition_declaration_version,
        "composition_declaration_hash": record.composition_declaration_hash,
        "book_bible_version": record.book_bible_version,
        "book_bible_hash": record.book_bible_hash,
        "book_bible_source_ref": record.book_bible_source_ref,
        "canon_version_or_revision": record.canon_version_or_revision,
        "canon_snapshot_hash": record.canon_snapshot_hash,
        "canon_source_ref": record.canon_source_ref,
        "composition_policy_version": record.composition_policy_version,
        "source_language": record.source_language,
        "content_hash": record.content_hash,
    }


def _source_path(repository: ProjectRepository, ref: str, root: Path) -> Path:
    candidate = Path(ref)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("source reference must be storage-relative")
    resolved = (repository.context.storage_root / candidate).resolve()
    resolved.relative_to(root.resolve())
    if not resolved.is_file():
        raise FileNotFoundError(ref)
    return resolved


def _snapshot(
    repository: ProjectRepository, ref: str, expected_hash: str, root: Path,
) -> tuple[bytes, dict[str, Any]]:
    if not _hash_ok(expected_hash):
        raise ValueError("snapshot hash is invalid")
    raw = _source_path(repository, ref, root).read_bytes()
    if _sha(raw) != expected_hash:
        raise ValueError("snapshot hash mismatch")
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("snapshot must be a JSON object")
    return raw, document


def _canonical_commit_matches(
    repository: ProjectRepository, operation_id: str, *, run_id: str, text_hash: str,
) -> bool:
    raw = repository.get_metadata_readonly("canonical_commit.v1:" + operation_id)
    if raw is None:
        return False
    receipt = json.loads(raw)
    if (
        not isinstance(receipt, dict)
        or receipt.get("operation_id") != operation_id
        or receipt.get("project_id") != repository.context.project_id
        or receipt.get("scope_type") != "PROJECT"
        or receipt.get("scope_id") != repository.context.project_id
        or receipt.get("run_id") != run_id
        or receipt.get("status") != "COMMITTED"
        or receipt.get("canonical_commit") is not True
    ):
        return False
    proposal_raw = repository.get_metadata_readonly(
        "canonical_proposal.v1:" + str(receipt.get("proposal_id") or "")
    )
    if proposal_raw is None:
        return False
    proposal_document = json.loads(proposal_raw)
    records = proposal_document.get("versions") if isinstance(proposal_document, dict) else None
    if not isinstance(records, dict):
        return False
    for entry in records.values():
        if not isinstance(entry, dict) or entry.get("receipt") != receipt:
            continue
        proposal = entry.get("proposal")
        if not isinstance(proposal, dict):
            continue
        if (
            proposal.get("status") == "COMMITTED"
            and proposal.get("project_id") == repository.context.project_id
            and proposal.get("book_id") == repository.context.book_id
            and proposal.get("run_id") == run_id
            and proposal.get("scope_type") == "PROJECT"
            and proposal.get("source_artifact_hash") == text_hash
            and proposal.get("proposal_hash") == receipt.get("proposal_hash")
            and canonical_proposal_hash(proposal) == receipt.get("proposal_hash")
        ):
            return True
    return False


def _chapter(
    repository: ProjectRepository, selected: ChapterSelection,
) -> tuple[str | None, Blocker | None]:
    try:
        path = _source_path(
            repository, selected.artifact_ref, repository.context.book_root / "chapters"
        )
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None, Blocker("CHAPTER_MISSING_OR_INVALID", selected.chapter_id)
    if not isinstance(document, dict):
        return None, Blocker("CHAPTER_INVALID", selected.chapter_id)
    text = document.get("text")
    versions = document.get("versions")
    if (
        document.get("project_id") != repository.context.project_id
        or document.get("domain_book_id") != repository.context.book_id
    ):
        return None, Blocker("CHAPTER_SCOPE_MISMATCH", selected.chapter_id)
    if (
        document.get("chapter_id") != selected.chapter_id
        or document.get("version") != selected.version
        or document.get("status") != "ACCEPTED"
        or not isinstance(versions, list)
        or not versions
        or not isinstance(versions[-1], dict)
        or versions[-1].get("version") != selected.version
        or versions[-1].get("status") != "ACCEPTED"
    ):
        return None, Blocker("CHAPTER_LINEAGE_MISMATCH", selected.chapter_id)
    if (
        not isinstance(text, str)
        or not text.strip()
        or not _hash_ok(selected.artifact_sha256)
        or _sha(raw) != selected.artifact_sha256
        or not _hash_ok(selected.text_sha256)
        or _sha(text.encode("utf-8")) != selected.text_sha256
        or document.get("sha256") != selected.text_sha256
        or versions[-1].get("artifact_hash") != selected.text_sha256
        or versions[-1].get("text") != text
    ):
        return None, Blocker("CHAPTER_TEXT_HASH_MISMATCH", selected.chapter_id)
    try:
        version_numbers = tuple(item["version"] for item in versions)
        relative = str(path.relative_to(repository.context.storage_root / "books")).replace("\\", "/")
        operation = repository.get_cross_store_operation(selected.chapter_commit_operation_id)
        if (
            operation is None
            or operation.get("operation_type") != "CHAPTER_ARTIFACT_LINEAGE_V2"
            or operation.get("status") != "COMMITTED"
            or operation.get("payload", {}).get("artifact_relative_path") != relative
            or not repository.is_finalized_chapter_artifact(
                relative_path=relative,
                artifact_sha256=selected.artifact_sha256,
                chapter_id=selected.chapter_id,
                versions=version_numbers,
            )
        ):
            return None, Blocker("CHAPTER_COMMIT_INCOMPLETE", selected.chapter_id)
    except (KeyError, TypeError, ValueError, ProjectStorageError):
        return None, Blocker("CHAPTER_COMMIT_INCOMPLETE", selected.chapter_id)
    quality = document.get("quality_evaluation")
    if document.get("quality_decision") == "NOT_EVALUATED" or not isinstance(quality, dict):
        return None, Blocker("QUALITY_NOT_EVALUATED", selected.chapter_id)
    if (
        quality.get("evaluation_id") != selected.evaluation_id
        or quality.get("evaluation_record_hash") != selected.evaluation_record_hash
        or quality.get("evaluated_artifact_hash") != selected.text_sha256
        or quality.get("evaluation_execution_status") != "COMPLETED"
        or quality.get("evaluation_validation_status") != "VALID"
        or quality.get("decision") != "ACCEPT"
        or document.get("quality_decision") != "ACCEPT"
    ):
        return None, Blocker("QUALITY_REFERENCE_MISMATCH", selected.chapter_id)
    try:
        binding, record, state = find_evaluation(repository, evaluation_id=selected.evaluation_id)
    except (EvaluationError, ProjectStorageError, ValueError):
        return None, Blocker("QUALITY_RECORD_MISSING_OR_INVALID", selected.chapter_id)
    if (
        record is None
        or state.get("execution_status") != "COMPLETED"
        or state.get("validation_status") != "VALID"
        or binding.project_id != repository.context.project_id
        or binding.book_id != repository.context.book_id
        or binding.evaluation_kind != "QUALITY"
        or binding.artifact_hash != selected.text_sha256
        or record.artifact_hash != selected.text_sha256
        or record.record_hash != selected.evaluation_record_hash
        or record.decision != "ACCEPT"
        or record.run_id != document.get("run_id")
        or quality.get("evaluated_artifact_id") != record.artifact_id
        or quality.get("evaluation_operation_id") != record.operation_id
    ):
        return None, Blocker("QUALITY_RECORD_MISMATCH", selected.chapter_id)
    try:
        canonical = _canonical_commit_matches(
            repository, selected.canonical_commit_operation_id,
            run_id=str(document.get("run_id") or ""), text_hash=selected.text_sha256,
        )
    except (ValueError, TypeError, json.JSONDecodeError, ProjectStorageError):
        canonical = False
    if not canonical:
        return None, Blocker("CANONICAL_COMMIT_INCOMPLETE", selected.chapter_id)
    return text, None


def _prepare(
    repository: ProjectRepository, request: SealRequest,
) -> tuple[_Prepared | None, tuple[Blocker, ...]]:
    blockers: list[Blocker] = []
    if request.project_id != repository.context.project_id or request.book_id != repository.context.book_id:
        return None, (Blocker("PROJECT_BOOK_SCOPE_MISMATCH", "request owner differs from repository"),)
    if type(request.version) is not int or request.version < 1:
        blockers.append(Blocker("MANUSCRIPT_VERSION_INVALID", "version must be positive"))
    if not request.source_language.strip():
        blockers.append(Blocker("SOURCE_LANGUAGE_MISSING", "source language is required"))
    if request.composition_policy_version != COMPOSITION_POLICY_VERSION:
        blockers.append(Blocker("COMPOSITION_POLICY_UNKNOWN", "unsupported composition policy"))
    declaration = request.composition
    if (
        declaration.project_id != request.project_id
        or declaration.book_id != request.book_id
        or not declaration.version.strip()
        or not declaration.ordered_chapter_ids
    ):
        blockers.append(Blocker("COMPOSITION_DECLARATION_INVALID", "versioned owner/order is required"))
    ids = tuple(item.chapter_id for item in request.chapter_versions)
    if len(set(ids)) != len(ids) or len(set(declaration.ordered_chapter_ids)) != len(declaration.ordered_chapter_ids):
        blockers.append(Blocker("DUPLICATE_CHAPTER", "chapter identity is repeated"))
    if ids != declaration.ordered_chapter_ids:
        blockers.append(Blocker("COMPOSITION_ORDER_MISMATCH", "selected chapters differ from declared order"))
    if not request.chapter_versions:
        blockers.append(Blocker("CHAPTERS_MISSING", "at least one chapter is required"))
    if blockers:
        return None, tuple(blockers)
    try:
        bible_bytes, bible = _snapshot(
            repository, request.book_bible_source_ref, request.book_bible_hash,
            repository.context.book_root,
        )
        bible_path = _source_path(
            repository, request.book_bible_source_ref, repository.context.book_root,
        )
        if bible_path != (repository.context.book_root / "book_bible.json").resolve():
            raise ValueError("Book Bible must use the bound book path")
        if request.book_bible_version != BOOK_BIBLE_CONTRACT_VERSION:
            raise ValueError("Book Bible contract version mismatch")
        validate_book_bible_payload_or_raise(bible, expected_book_id=request.book_id)
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, BookBibleContractError):
        blockers.append(Blocker("BOOK_BIBLE_SNAPSHOT_INVALID", "Book Bible snapshot is unavailable or inconsistent"))
        bible_bytes = b""
    try:
        canon_bytes, canon = _snapshot(
            repository, request.canon_source_ref, request.canon_snapshot_hash,
            repository.context.book_root / "artifacts" / "canon",
        )
        if (
            canon.get("_meta", {}).get("book_id") != request.book_id
            or request.canon_revision != "sha256:" + request.canon_snapshot_hash
        ):
            raise ValueError("Canon revision or book binding mismatch")
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        blockers.append(Blocker("CANON_SNAPSHOT_INVALID", "Canon snapshot is unavailable or inconsistent"))
        canon_bytes = b""
    texts: list[str] = []
    for selected in request.chapter_versions:
        text, blocker = _chapter(repository, selected)
        if blocker is not None:
            blockers.append(blocker)
        else:
            texts.append(str(text))
    if blockers:
        return None, tuple(blockers)
    content = _SEPARATOR.join(texts)
    declaration_hash = _hash_json(asdict(declaration))
    provisional = ManuscriptVersion(
        schema_version=SCHEMA_VERSION,
        manuscript_id="",
        project_id=request.project_id,
        book_id=request.book_id,
        series_id=request.series_id,
        version=request.version,
        parent_manuscript_id=request.parent_manuscript_id,
        chapter_version_refs=request.chapter_versions,
        composition_declaration_version=declaration.version,
        composition_declaration_hash=declaration_hash,
        book_bible_version=request.book_bible_version,
        book_bible_hash=request.book_bible_hash,
        book_bible_source_ref=request.book_bible_source_ref,
        book_bible_snapshot_ref="",
        canon_version_or_revision=request.canon_revision,
        canon_snapshot_hash=request.canon_snapshot_hash,
        canon_source_ref=request.canon_source_ref,
        canon_snapshot_ref="",
        composition_policy_version=request.composition_policy_version,
        source_language=request.source_language,
        content_hash=_sha(content.encode("utf-8")),
        manifest_hash="",
        artifact_ref="",
        status="SEALED",
        created_at="",
    )
    manifest_hash = _hash_json(_manifest_payload(provisional))
    manuscript_id = "MV-" + manifest_hash
    artifact_ref = _artifact_ref(manuscript_id)
    record = ManuscriptVersion(
        **{
            **provisional.__dict__,
            "manuscript_id": manuscript_id,
            "manifest_hash": manifest_hash,
            "artifact_ref": artifact_ref,
            "book_bible_snapshot_ref": artifact_ref + "#/snapshots/book_bible_b64",
            "canon_snapshot_ref": artifact_ref + "#/snapshots/canon_b64",
        }
    )
    return _Prepared(record, content, bible_bytes, canon_bytes), ()


def validate_manuscript_input(repository: ProjectRepository, request: SealRequest) -> ValidationResult:
    """Mechanical, read-only prerequisite validation; no LLM or filesystem ordering."""
    _prepared, blockers = _prepare(repository, request)
    if not blockers:
        try:
            expected = _expected_manuscript_head(repository, request)
            if repository.get_gap018_manuscript_head() != expected:
                blockers = (Blocker("MANUSCRIPT_HEAD_CHANGED", "current manuscript differs"),)
        except ManuscriptBlocked as exc:
            blockers = exc.blockers
    return ValidationResult("BLOCKED" if blockers else "VALID", blockers)


def _expected_manuscript_head(
    repository: ProjectRepository, request: SealRequest,
) -> tuple[str | None, int | None, str | None]:
    if request.parent_manuscript_id is None:
        if request.version != 1:
            raise ManuscriptBlocked((Blocker("MANUSCRIPT_PARENT_MISSING", "version requires parent"),))
        return (None, None, None)
    try:
        parent = load_manuscript(repository, request.parent_manuscript_id)
    except (ManuscriptIntegrityError, OSError, ValueError) as exc:
        raise ManuscriptBlocked((Blocker("MANUSCRIPT_PARENT_INVALID", str(exc)),)) from exc
    if request.version != parent.version + 1:
        raise ManuscriptBlocked((Blocker("MANUSCRIPT_VERSION_CONFLICT", "version does not follow parent"),))
    return (parent.manuscript_id, parent.version, parent.manifest_hash)


def validate_current_manuscript_inputs(
    repository: ProjectRepository, record: ManuscriptVersion,
) -> ValidationResult:
    """Fail closed before downstream promotion if any pinned source changed."""
    if (record.project_id, record.book_id) != (repository.context.project_id, repository.context.book_id):
        return ValidationResult("BLOCKED", (Blocker("PROJECT_BOOK_SCOPE_MISMATCH", "manuscript owner differs"),))
    blockers: list[Blocker] = []
    try:
        _snapshot(repository, record.book_bible_source_ref, record.book_bible_hash,
                  repository.context.book_root)
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        blockers.append(Blocker("BOOK_BIBLE_STALE", "pinned Book Bible source is no longer current"))
    try:
        _snapshot(repository, record.canon_source_ref, record.canon_snapshot_hash,
                  repository.context.book_root / "artifacts" / "canon")
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        blockers.append(Blocker("CANON_STALE", "pinned Canon source changed or is missing"))
    for selected in record.chapter_version_refs:
        _, blocker = _chapter(repository, selected)
        if blocker is not None:
            blockers.append(blocker)
    return ValidationResult("BLOCKED" if blockers else "VALID", tuple(blockers))


def seal_manuscript(repository: ProjectRepository, request: SealRequest) -> ManuscriptVersion:
    if (request.project_id, request.book_id) != (repository.context.project_id,
                                                  repository.context.book_id):
        raise ManuscriptBlocked((Blocker("PROJECT_BOOK_SCOPE_MISMATCH", "request owner differs"),))
    from app.p20_core.gap018_receipts import reserve_request
    reserve_request(repository, "MANUSCRIPT", request.request_id, asdict(request))
    result_id = "manuscript-result-" + _sha(request.request_id.encode("utf-8"))
    prior_result = repository.get_gap018_record("MANUSCRIPT_REQUEST_RESULT", result_id)
    if prior_result is not None:
        return load_manuscript(repository, prior_result["manuscript_id"])
    prepared, blockers = _prepare(repository, request)
    if blockers:
        raise ManuscriptBlocked(blockers)
    assert prepared is not None
    record = prepared.record
    operation_id = "gap018-manuscript-" + record.manifest_hash
    committed = repository.get_gap018_record("MANUSCRIPT", record.manuscript_id)
    if committed is not None:
        if (committed.get("manifest_hash") != record.manifest_hash
                or committed.get("content_hash") != record.content_hash):
            raise ManuscriptIntegrityError("existing manuscript record conflicts")
        with repository.gap018_transaction() as connection:
            repository.put_gap018_record("MANUSCRIPT_REQUEST_RESULT", result_id,
                                         {"manuscript_id": record.manuscript_id,
                                          "manifest_hash": record.manifest_hash},
                                         connection=connection)
        return load_manuscript(repository, record.manuscript_id)
    expected_head = _expected_manuscript_head(repository, request)
    if repository.get_gap018_manuscript_head() != expected_head:
        raise ManuscriptBlocked((Blocker("MANUSCRIPT_HEAD_CHANGED", "current manuscript differs"),))
    existing = repository.get_cross_store_operation(operation_id)
    service = CrossStoreRecoveryService(repository)
    if existing is not None:
        service.recover(operation_id)
    else:
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        record = ManuscriptVersion(**{**record.__dict__, "created_at": created_at})
        artifact = {
            "record": record.to_dict(),
            "content": prepared.content,
            "snapshots": {
                "book_bible_b64": base64.b64encode(prepared.book_bible_bytes).decode("ascii"),
                "canon_b64": base64.b64encode(prepared.canon_bytes).decode("ascii"),
            },
        }
        plan = CrossStoreOperationPlan(
            operation_id=operation_id,
            project_id=request.project_id,
            book_id=request.book_id,
            operation_type=_OPERATION_TYPE,
            source_ref="composition-declaration:" + record.composition_declaration_hash,
            artifact_relative_path=record.artifact_ref,
            artifact_root=ArtifactRoot.PROJECT,
            artifact_bytes=_json(artifact).encode("utf-8"),
            expected_versions={"manuscript_version": record.version},
            provenance_refs=tuple(item.artifact_ref for item in record.chapter_version_refs)
            + (request.book_bible_source_ref, request.canon_source_ref),
        )
        result = service.execute(plan)
        if result.get("status") != RecoveryStatus.COMMITTED.value:
            raise ManuscriptIntegrityError("manuscript artifact did not commit")
    # F-004 and the project-owned current index are distinct durable steps.
    # Reopen/retry finalizes the latter using the original expected parent.
    raw_record = json.loads((repository.context.project_root / record.artifact_ref).read_text(encoding="utf-8"))["record"]
    committed_record = ManuscriptVersion.from_dict(raw_record)
    with repository.gap018_transaction() as connection:
        if repository.get_gap018_manuscript_head(connection=connection) != expected_head:
            raise ManuscriptBlocked((Blocker("MANUSCRIPT_HEAD_CHANGED", "current manuscript differs"),))
        repository.put_gap018_record("MANUSCRIPT", committed_record.manuscript_id,
                                     committed_record.to_dict(), connection=connection)
        if not repository.cas_gap018_manuscript_head(
            expected_head, (committed_record.manuscript_id, committed_record.version,
                            committed_record.manifest_hash), connection=connection,
        ):
            raise ManuscriptIntegrityError("manuscript CAS changed inside transaction")
        repository.put_gap018_record("MANUSCRIPT_REQUEST_RESULT", result_id,
                                     {"manuscript_id": committed_record.manuscript_id,
                                      "manifest_hash": committed_record.manifest_hash},
                                     connection=connection)
    return load_manuscript(repository, record.manuscript_id)


def load_manuscript(repository: ProjectRepository, manuscript_id: str) -> ManuscriptVersion:
    """Verify the F-004 receipt, Ledger, artifact and immutable snapshot hashes."""
    if not manuscript_id.startswith("MV-") or not _hash_ok(manuscript_id[3:]):
        raise ManuscriptIntegrityError("manuscript id is invalid")
    operation_id = "gap018-manuscript-" + manuscript_id[3:]
    operation = repository.get_cross_store_operation_readonly(operation_id)
    if operation is None or operation.get("operation_type") != _OPERATION_TYPE:
        raise ManuscriptIntegrityError("manuscript operation is missing")
    if operation.get("status") != RecoveryStatus.COMMITTED.value:
        raise ManuscriptIntegrityError("manuscript operation is not committed")
    CrossStoreRecoveryService(repository).verify_committed_readonly(operation_id)
    artifact_ref = _artifact_ref(manuscript_id)
    if operation.get("payload", {}).get("artifact_relative_path") != artifact_ref:
        raise ManuscriptIntegrityError("manuscript artifact path mismatch")
    path = (repository.context.project_root / artifact_ref).resolve()
    path.relative_to(repository.context.project_root.resolve())
    bundle = json.loads(path.read_text(encoding="utf-8"))
    record = ManuscriptVersion.from_dict(bundle["record"])
    if record.manuscript_id != manuscript_id or record.project_id != repository.context.project_id or record.book_id != repository.context.book_id:
        raise ManuscriptIntegrityError("manuscript owner mismatch")
    durable = repository.get_gap018_record("MANUSCRIPT", manuscript_id)
    if durable is None or durable != record.to_dict():
        raise ManuscriptIntegrityError("manuscript domain commit is missing")
    if _sha(bundle["content"].encode("utf-8")) != record.content_hash:
        raise ManuscriptIntegrityError("manuscript content hash mismatch")
    snapshots = bundle["snapshots"]
    if (
        _sha(base64.b64decode(snapshots["book_bible_b64"], validate=True)) != record.book_bible_hash
        or _sha(base64.b64decode(snapshots["canon_b64"], validate=True)) != record.canon_snapshot_hash
    ):
        raise ManuscriptIntegrityError("manuscript input snapshot hash mismatch")
    return record


__all__ = [
    "Blocker", "ChapterSelection", "CompositionDeclaration", "COMPOSITION_POLICY_VERSION",
    "ManuscriptBlocked", "ManuscriptIntegrityError", "ManuscriptVersion", "SealRequest",
    "ValidationResult", "load_manuscript", "seal_manuscript", "validate_manuscript_input",
    "validate_current_manuscript_inputs",
]

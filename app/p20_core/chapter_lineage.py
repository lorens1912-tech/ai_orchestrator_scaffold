from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

from app.p20_core.cross_store_recovery import (
    ArtifactRoot,
    CrossStoreOperationPlan,
    CrossStoreRecoveryService,
    RecoveryStatus,
)
from app.p20_core.project_repository import ProjectRepository
from app.p20_core.storage_paths import get_storage_root

if TYPE_CHECKING:
    from app.p20_core.context_runtime import ProjectExecutionContext


CHAPTER_ARTIFACT_SCHEMA_VERSION = 2
_TEXT_VERSION_MODES = {"WRITE", "REWRITE", "EDIT"}
_EVALUATION_MODES = {
    "CRITIC",
    "QUALITY",
    "CONTINUITY",
    "CANON_CHECK",
    "FACTCHECK",
    "STYLE",
}


class ChapterLineageError(ValueError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _artifact_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _required_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ChapterLineageError(f"{field_name} is required")
    return normalized


def _storage_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return get_storage_root() / path


def _public_storage_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(get_storage_root().resolve())).replace(
            "\\", "/"
        )
    except ValueError as exc:
        raise ChapterLineageError("chapter artifact escapes storage root") from exc


def _read_step(path_value: str) -> tuple[dict[str, Any], str]:
    path = _storage_path(path_value)
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChapterLineageError(f"invalid step artifact: {path_value}") from exc
    if not isinstance(document, dict):
        raise ChapterLineageError(f"step artifact must be an object: {path_value}")
    return document, _sha256_bytes(raw)


def _operation_id(execution_context: ProjectExecutionContext) -> str:
    identity = {
        "project_id": execution_context.project_id,
        "book_id": execution_context.book_id,
        "series_id": execution_context.series_id,
        "run_id": execution_context.run_id,
        "step_id": execution_context.step_id,
        "operation_type": "CHAPTER_ARTIFACT_LINEAGE_V2",
    }
    digest = _sha256_text(_canonical_json(identity))
    return f"chapter-lineage-{digest}"


def _record_artifact_path(
    repository: ProjectRepository,
    record: Mapping[str, Any],
) -> Path:
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        raise ChapterLineageError("chapter recovery record has no durable payload")
    relative = _required_text(
        payload.get("artifact_relative_path"),
        "artifact_relative_path",
    )
    artifact_root = str(payload.get("artifact_root") or ArtifactRoot.PROJECT.value)
    root = (
        repository.context.storage_root / "books"
        if artifact_root == ArtifactRoot.BOOKS.value
        else repository.context.project_root
    )
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ChapterLineageError("chapter recovery path escapes its root") from exc
    return path


def _next_chapter_path(storage_book_id: str) -> Path:
    book_component = Path(_required_text(storage_book_id, "storage_book_id"))
    if book_component.is_absolute() or len(book_component.parts) != 1:
        raise ChapterLineageError("storage_book_id contains unsafe path characters")
    storage_root = get_storage_root().resolve()
    chapters = (storage_root / "books" / book_component / "chapters").resolve()
    try:
        chapters.relative_to(storage_root)
    except ValueError as exc:
        raise ChapterLineageError("chapter directory escapes storage root") from exc
    chapters.mkdir(parents=True, exist_ok=True)
    maximum = 0
    for path in chapters.glob("chapter_*.json"):
        try:
            maximum = max(maximum, int(path.stem.rsplit("_", 1)[-1]))
        except ValueError:
            continue
    return chapters / f"chapter_{maximum + 1:03d}.json"


def _context_versions(step: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    tool_input = step.get("input")
    package = tool_input.get("_context_package") if isinstance(tool_input, Mapping) else None
    if not isinstance(package, Mapping):
        raise ChapterLineageError("text version has no persisted ContextPackage")
    if (
        package.get("context_package_id") != step.get("context_package_id")
        or package.get("context_hash") != step.get("context_hash")
    ):
        raise ChapterLineageError("step and ContextPackage identity do not match")
    return (
        package.get("canon_version"),
        package.get("book_bible_version"),
        package.get("style_version"),
    )


def _result_payload(step: Mapping[str, Any]) -> Mapping[str, Any]:
    result = step.get("result")
    payload = result.get("payload") if isinstance(result, Mapping) else None
    return payload if isinstance(payload, Mapping) else {}


def _evaluation_ref(
    *,
    step: Mapping[str, Any],
    artifact_path: str,
    artifact_hash: str,
) -> dict[str, Any]:
    payload = _result_payload(step)
    decision = payload.get("DECISION") or payload.get("decision")
    return {
        "mode": str(step.get("mode") or "").upper(),
        "run_id": step.get("run_id"),
        "step_id": step.get("step_id"),
        "artifact_path": artifact_path,
        "artifact_hash": artifact_hash,
        "context_package_id": step.get("context_package_id"),
        "context_hash": step.get("context_hash"),
        "requested_model": step.get("requested_model"),
        "effective_model": step.get("effective_model"),
        "decision": None if decision is None else str(decision).upper(),
    }


def _load_operation_steps(
    *,
    artifact_paths: list[str],
    execution_context: ProjectExecutionContext,
) -> list[tuple[dict[str, Any], str, str]]:
    loaded: list[tuple[dict[str, Any], str, str]] = []
    seen_step_ids: set[str] = set()
    for artifact_path in artifact_paths:
        step, file_hash = _read_step(artifact_path)
        if step.get("project_id") != execution_context.project_id:
            raise ChapterLineageError("step artifact belongs to another project")
        if step.get("book_id") != execution_context.book_id:
            raise ChapterLineageError("step artifact belongs to another book")
        step_id = _required_text(step.get("step_id"), "step_id")
        if step_id in seen_step_ids:
            continue
        seen_step_ids.add(step_id)
        loaded.append((step, artifact_path, file_hash))
    return loaded


def _build_chapter_document(
    *,
    chapter_path: Path,
    storage_book_id: str,
    execution_context: ProjectExecutionContext,
    steps: list[tuple[dict[str, Any], str, str]],
    fallback_text: str,
    quality_decision: str | None,
    canon_snapshot_path: str,
    pre_report: Mapping[str, Any],
    post_report: Mapping[str, Any],
    master_canon: Mapping[str, Any],
    project_truth: Mapping[str, Any],
    book_bible_binding: Mapping[str, Any],
    engine: str,
) -> dict[str, Any]:
    evaluations: list[dict[str, Any]] = []
    versions: list[dict[str, Any]] = []
    latest_evaluation: dict[str, Any] | None = None

    for step, artifact_path, step_artifact_hash in steps:
        mode = str(step.get("mode") or "").upper()
        if mode in _EVALUATION_MODES:
            latest_evaluation = _evaluation_ref(
                step=step,
                artifact_path=artifact_path,
                artifact_hash=step_artifact_hash,
            )
            evaluations.append(latest_evaluation)
        if mode not in _TEXT_VERSION_MODES:
            continue

        text = str(_result_payload(step).get("text") or "")
        if not text.strip():
            raise ChapterLineageError(f"{mode} step produced no versioned text")
        canon_version, bible_version, style_version = _context_versions(step)
        version = len(versions) + 1
        versions.append(
            {
                "chapter_id": chapter_path.stem,
                "project_id": execution_context.project_id,
                "book_id": storage_book_id,
                "domain_book_id": execution_context.book_id,
                "version": version,
                "parent_version": None if version == 1 else version - 1,
                "status": "DRAFT",
                "text": text,
                "text_ref": artifact_path,
                "reason": mode,
                "source_operation": {
                    "operation_type": mode,
                    "run_id": execution_context.run_id,
                    "step_id": step.get("step_id"),
                    "artifact_path": artifact_path,
                    "artifact_hash": step_artifact_hash,
                    "input_ref": f"{artifact_path}#/input",
                    "input_hash": _sha256_text(
                        _canonical_json(step.get("input") or {})
                    ),
                },
                "source_evaluation": (
                    None if latest_evaluation is None else dict(latest_evaluation)
                ),
                "run_id": execution_context.run_id,
                "step_id": step.get("step_id"),
                "context_package_id": step.get("context_package_id"),
                "context_hash": step.get("context_hash"),
                "created_by_role": step.get("role"),
                "requested_model": step.get("requested_model"),
                "effective_model": step.get("effective_model"),
                "canon_version": canon_version,
                "book_bible_version": bible_version,
                "style_version": style_version,
                "quality_decision": None,
                "artifact_hash": _sha256_text(text),
                "created_at": step.get("created_at"),
            }
        )

    if not versions:
        fallback = _required_text(fallback_text, "chapter text")
        raise ChapterLineageError(
            "accepted chapter has no WRITE/REWRITE/EDIT lineage for text: "
            + _sha256_text(fallback)
        )

    resolved_quality = str(quality_decision or "NOT_EVALUATED").upper()
    for version in versions[:-1]:
        version["status"] = "SUPERSEDED"
    versions[-1]["status"] = "ACCEPTED"
    versions[-1]["quality_decision"] = resolved_quality
    latest = versions[-1]
    quality_evaluation = next(
        (
            dict(evaluation)
            for evaluation in reversed(evaluations)
            if evaluation["mode"] == "QUALITY"
        ),
        None,
    )
    bible = dict(book_bible_binding)
    return {
        "schema_version": CHAPTER_ARTIFACT_SCHEMA_VERSION,
        "lineage_version": CHAPTER_ARTIFACT_SCHEMA_VERSION,
        "chapter_id": chapter_path.stem,
        "project_id": execution_context.project_id,
        "book_id": storage_book_id,
        "domain_book_id": execution_context.book_id,
        "series_id": execution_context.series_id,
        "version": latest["version"],
        "parent_version": latest["parent_version"],
        "status": "ACCEPTED",
        "text": latest["text"],
        "content": latest["text"],
        "text_ref": latest["text_ref"],
        "canon_version": latest["canon_version"],
        "book_bible_version": latest["book_bible_version"],
        "style_version": latest["style_version"],
        "context_package_id": latest["context_package_id"],
        "context_hash": latest["context_hash"],
        "created_by_role": latest["created_by_role"],
        "requested_model": latest["requested_model"],
        "effective_model": latest["effective_model"],
        "quality_decision": resolved_quality,
        "artifact_hash": latest["artifact_hash"],
        "sha256": latest["artifact_hash"],
        "created_at": versions[0]["created_at"],
        "updated_at": latest["created_at"],
        "run_id": execution_context.run_id,
        "step_id": latest["step_id"],
        "source_artifact": latest["text_ref"],
        "source_operation": latest["source_operation"],
        "source_evaluation": latest["source_evaluation"],
        "quality_evaluation": quality_evaluation,
        "versions": versions,
        "evaluations": evaluations,
        "canon_snapshot_path": canon_snapshot_path,
        "pre_canon_check": dict(pre_report),
        "post_canon_check": dict(post_report),
        "master_canon": dict(master_canon),
        "project_truth": dict(project_truth),
        "book_bible": bible,
        "book_bible_path": bible.get("path"),
        "book_bible_sha256": bible.get("sha256"),
        "book_bible_contract_version": bible.get("contract_version"),
        "canon_validation": {
            "source": "book_bible.json",
            "path": bible.get("path"),
            "sha256": bible.get("sha256"),
            "contract_version": bible.get("contract_version"),
        },
        "audit_refs": {
            "run_state": f"runs/{execution_context.run_id}/run_state.json",
            "run_audit": f"runs/{execution_context.run_id}/audit.json",
            "step_artifacts": [path for _step, path, _hash in steps],
        },
        "engine": engine,
    }


@dataclass(frozen=True)
class ChapterArtifactCommit:
    chapter_path: str
    operation_id: str
    artifact_hash: str
    version: int
    recovery_status: str
    document: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": CHAPTER_ARTIFACT_SCHEMA_VERSION,
            "chapter_id": self.document["chapter_id"],
            "chapter_path": self.chapter_path,
            "operation_id": self.operation_id,
            "version": self.version,
            "artifact_hash": self.artifact_hash,
            "context_package_id": self.document["context_package_id"],
            "context_hash": self.document["context_hash"],
            "recovery_status": self.recovery_status,
        }


def persist_chapter_lineage(
    *,
    repository: ProjectRepository,
    execution_context: ProjectExecutionContext,
    storage_book_id: str,
    artifact_paths: list[str],
    text: str,
    quality_decision: str | None,
    canon_snapshot_path: str,
    pre_report: Mapping[str, Any],
    post_report: Mapping[str, Any],
    master_canon: Mapping[str, Any],
    project_truth: Mapping[str, Any],
    book_bible_binding: Mapping[str, Any],
    engine: str,
) -> ChapterArtifactCommit:
    if repository.context.project_id != execution_context.project_id:
        raise ChapterLineageError("chapter owner repository belongs to another project")
    if repository.context.book_id != execution_context.book_id:
        raise ChapterLineageError("chapter owner repository belongs to another book")

    operation_id = _operation_id(execution_context)
    service = CrossStoreRecoveryService(repository)
    existing = repository.get_cross_store_operation(operation_id)
    if existing is not None and execution_context.technical_retry:
        recovered = service.recover(operation_id)
        chapter_path = _record_artifact_path(repository, recovered)
        document = json.loads(chapter_path.read_text(encoding="utf-8"))
        if document.get("project_id") != execution_context.project_id:
            raise ChapterLineageError("recovered chapter belongs to another project")
        return ChapterArtifactCommit(
            chapter_path=_public_storage_path(chapter_path),
            operation_id=operation_id,
            artifact_hash=str(document["artifact_hash"]),
            version=int(document["version"]),
            recovery_status=str(recovered["status"]),
            document=document,
        )

    chapter_path = (
        _record_artifact_path(repository, existing)
        if existing is not None
        else _next_chapter_path(storage_book_id)
    )
    steps = _load_operation_steps(
        artifact_paths=artifact_paths,
        execution_context=execution_context,
    )
    document = _build_chapter_document(
        chapter_path=chapter_path,
        storage_book_id=storage_book_id,
        execution_context=execution_context,
        steps=steps,
        fallback_text=text,
        quality_decision=quality_decision,
        canon_snapshot_path=canon_snapshot_path,
        pre_report=pre_report,
        post_report=post_report,
        master_canon=master_canon,
        project_truth=project_truth,
        book_bible_binding=book_bible_binding,
        engine=engine,
    )
    artifact_bytes = _artifact_json(document)
    chapter_public_path = _public_storage_path(chapter_path)
    books_root = (get_storage_root() / "books").resolve()
    try:
        relative_path = str(chapter_path.resolve().relative_to(books_root)).replace(
            "\\", "/"
        )
    except ValueError as exc:
        raise ChapterLineageError("chapter artifact escapes books root") from exc
    latest = document["versions"][-1]
    provenance = tuple(
        dict.fromkeys(
            [
                str(version["text_ref"])
                for version in document["versions"]
            ]
            + [
                str(evaluation["artifact_path"])
                for evaluation in document["evaluations"]
            ]
        )
    )
    record = service.execute(
        CrossStoreOperationPlan(
            operation_id=operation_id,
            project_id=execution_context.project_id,
            book_id=execution_context.book_id,
            series_id=None,
            run_id=execution_context.run_id,
            step_id=execution_context.step_id,
            operation_type="CHAPTER_ARTIFACT_LINEAGE_V2",
            source_ref=str(latest["text_ref"]),
            artifact_relative_path=relative_path,
            artifact_root=ArtifactRoot.BOOKS,
            artifact_bytes=artifact_bytes,
            expected_versions={
                "chapter_version": document["version"],
                "canon_version": document["canon_version"],
                "book_bible_version": document["book_bible_version"],
                "style_version": document["style_version"],
            },
            provenance_refs=provenance,
        )
    )
    if record["status"] != RecoveryStatus.COMMITTED.value:
        raise ChapterLineageError("chapter recovery operation did not commit")
    persisted = json.loads(chapter_path.read_text(encoding="utf-8"))
    if persisted != document:
        raise ChapterLineageError("persisted chapter does not match lineage document")
    return ChapterArtifactCommit(
        chapter_path=chapter_public_path,
        operation_id=operation_id,
        artifact_hash=str(document["artifact_hash"]),
        version=int(document["version"]),
        recovery_status=str(record["status"]),
        document=document,
    )


__all__ = [
    "CHAPTER_ARTIFACT_SCHEMA_VERSION",
    "ChapterArtifactCommit",
    "ChapterLineageError",
    "persist_chapter_lineage",
]

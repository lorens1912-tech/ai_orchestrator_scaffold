from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from app.p20_core.project_repository import (
    ProjectRepository,
    SeriesAccessContext,
    SeriesRepository,
    StorageResolver,
)
from app.p20_core.series_memory import (
    SeriesStateKind,
    VolumeClosingSnapshot,
    VolumeTransferTarget,
    series_state_from_snapshot_item,
)


RECOVERY_RECORD_SCHEMA_VERSION = 1
_OPERATION_KEY_PREFIX = "cross_store_operation.v1:"
_PROJECT_WRITE_KEY_PREFIX = "cross_store_project_write.v1:"
_AUDIT_KEY_PREFIX = "cross_store_audit.v1:"
_LEDGER_CHAPTER_OPERATION = "CHAPTER_ARTIFACT_LINEAGE_V2"
_LEDGER_MANUSCRIPT_OPERATION = "MANUSCRIPT_VERSION_V1"
_LEDGER_GAP018_OPERATIONS = frozenset({
    _LEDGER_MANUSCRIPT_OPERATION,
    "BOOK_QA_REPORT_V1",
    "CANDIDATE_MASTER_V1",
    "AUTHOR_APPROVAL_V1",
    "SOURCE_MASTER_V1",
})


class CrossStoreRecoveryError(RuntimeError):
    pass


class RecoveryInterventionRequired(CrossStoreRecoveryError):
    pass


class InjectedRecoveryCrash(BaseException):
    """Test-only abrupt-stop signal; production error handling must not consume it."""


class FaultPoint(str, Enum):
    AFTER_INTENT = "A_AFTER_INTENT"
    AFTER_PROJECT_WRITE = "B_AFTER_PROJECT_WRITE"
    AFTER_ARTIFACT_PREPARE = "C_AFTER_ARTIFACT_PREPARE"
    AFTER_ARTIFACT_COMMIT = "D_AFTER_ARTIFACT_COMMIT"
    AFTER_SERIES_WRITE = "E_AFTER_SERIES_WRITE"
    AFTER_LOGICAL_COMMIT = "F_AFTER_LOGICAL_COMMIT"
    DURING_RETRY = "G_DURING_RETRY"


class RecoveryStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    AUDIT_PENDING = "AUDIT_PENDING"
    COMMITTED = "COMMITTED"
    NEEDS_INTERVENTION = "NEEDS_INTERVENTION"


class ArtifactRoot(str, Enum):
    PROJECT = "PROJECT"
    BOOKS = "BOOKS"
    RUNS = "RUNS"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _required_text(value: str | None, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise CrossStoreRecoveryError(f"{field_name} is required")
    return normalized


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _json_object(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CrossStoreRecoveryError(f"{field_name} must be an object")
    normalized = json.loads(_canonical_json(dict(value)))
    if not isinstance(normalized, dict):
        raise CrossStoreRecoveryError(f"{field_name} must be an object")
    return normalized


@dataclass(frozen=True)
class CrossStoreOperationPlan:
    operation_id: str
    project_id: str
    book_id: str
    operation_type: str
    source_ref: str
    artifact_relative_path: str
    artifact_bytes: bytes
    expected_versions: Mapping[str, Any]
    provenance_refs: tuple[str, ...]
    artifact_root: ArtifactRoot | str = ArtifactRoot.PROJECT
    series_id: str | None = None
    series_snapshot: VolumeClosingSnapshot | None = None
    run_id: str | None = None
    step_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "operation_id",
            "project_id",
            "book_id",
            "operation_type",
            "source_ref",
            "artifact_relative_path",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        if not isinstance(self.artifact_bytes, bytes):
            raise CrossStoreRecoveryError("artifact_bytes must be bytes")
        if not self.artifact_bytes:
            raise CrossStoreRecoveryError("artifact_bytes must not be empty")
        object.__setattr__(
            self,
            "expected_versions",
            _json_object(self.expected_versions, "expected_versions"),
        )
        if isinstance(self.provenance_refs, (str, bytes)):
            raise CrossStoreRecoveryError("provenance_refs must be a list")
        refs = tuple(_required_text(value, "provenance_refs") for value in self.provenance_refs)
        if not refs:
            raise CrossStoreRecoveryError("provenance_refs is required")
        object.__setattr__(self, "provenance_refs", refs)
        object.__setattr__(self, "run_id", _optional_text(self.run_id, "run_id"))
        object.__setattr__(self, "step_id", _optional_text(self.step_id, "step_id"))
        object.__setattr__(self, "series_id", _optional_text(self.series_id, "series_id"))
        try:
            artifact_root = (
                self.artifact_root
                if isinstance(self.artifact_root, ArtifactRoot)
                else ArtifactRoot(str(self.artifact_root))
            )
        except ValueError as exc:
            raise CrossStoreRecoveryError(
                "artifact_root must be PROJECT, BOOKS or RUNS"
            ) from exc
        object.__setattr__(self, "artifact_root", artifact_root)
        if (self.series_id is None) != (self.series_snapshot is None):
            raise CrossStoreRecoveryError(
                "series_id and series_snapshot must either both be present or both be absent"
            )
        if self.series_snapshot is not None:
            if not isinstance(self.series_snapshot, VolumeClosingSnapshot):
                raise CrossStoreRecoveryError(
                    "series_snapshot must be a VolumeClosingSnapshot"
                )
            if str(self.series_snapshot.operation_id) != self.operation_id:
                raise CrossStoreRecoveryError(
                    "series snapshot must use the cross-store operation_id"
                )
            if str(self.series_snapshot.project_id) != self.project_id:
                raise CrossStoreRecoveryError("series snapshot project_id does not match")
            if str(self.series_snapshot.book_id) != self.book_id:
                raise CrossStoreRecoveryError("series snapshot book_id does not match")
            if str(self.series_snapshot.series_id) != self.series_id:
                raise CrossStoreRecoveryError("series snapshot series_id does not match")

    @property
    def artifact_sha256(self) -> str:
        return _sha256_bytes(self.artifact_bytes)

    def identity_payload(self) -> dict[str, Any]:
        payload = {
            "project_id": self.project_id,
            "book_id": self.book_id,
            "series_id": self.series_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "operation_type": self.operation_type,
            "source_ref": self.source_ref,
            "artifact_relative_path": self.artifact_relative_path,
            "artifact_sha256": self.artifact_sha256,
            "expected_versions": dict(self.expected_versions),
            "provenance_refs": list(self.provenance_refs),
            "series_snapshot_semantic_hash": (
                None
                if self.series_snapshot is None
                else self.series_snapshot.semantic_identity
            ),
        }
        # PROJECT is the v1 default and is intentionally omitted so existing
        # durable records retain their original input hash.
        if self.artifact_root != ArtifactRoot.PROJECT:
            payload["artifact_root"] = self.artifact_root.value
        return payload

    @property
    def input_hash(self) -> str:
        return _sha256_json(self.identity_payload())

    def durable_payload(self) -> dict[str, Any]:
        return {
            **self.identity_payload(),
            "artifact_base64": base64.b64encode(self.artifact_bytes).decode("ascii"),
            "series_snapshot": (
                None if self.series_snapshot is None else self.series_snapshot.to_dict()
            ),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> CrossStoreOperationPlan:
        payload = dict(record.get("payload") or {})
        try:
            artifact_bytes = base64.b64decode(
                str(payload["artifact_base64"]),
                validate=True,
            )
        except (KeyError, ValueError) as exc:
            raise CrossStoreRecoveryError("durable artifact payload is invalid") from exc
        snapshot_payload = payload.get("series_snapshot")
        snapshot = (
            None
            if snapshot_payload is None
            else VolumeClosingSnapshot(**dict(snapshot_payload))
        )
        return cls(
            operation_id=str(record.get("operation_id") or ""),
            project_id=str(payload.get("project_id") or ""),
            book_id=str(payload.get("book_id") or ""),
            series_id=payload.get("series_id"),
            run_id=payload.get("run_id"),
            step_id=payload.get("step_id"),
            operation_type=str(payload.get("operation_type") or ""),
            source_ref=str(payload.get("source_ref") or ""),
            artifact_relative_path=str(payload.get("artifact_relative_path") or ""),
            artifact_bytes=artifact_bytes,
            expected_versions=dict(payload.get("expected_versions") or {}),
            provenance_refs=tuple(payload.get("provenance_refs") or ()),
            artifact_root=payload.get("artifact_root", ArtifactRoot.PROJECT.value),
            series_snapshot=snapshot,
        )


class CrossStoreRecoveryService:
    """Project-owned outbox and recovery for project/file/optional-series writes."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        *,
        clock: Callable[[], str] = _utc_now,
        fault_hook: Callable[[FaultPoint], None] | None = None,
    ) -> None:
        if not isinstance(project_repository, ProjectRepository):
            raise CrossStoreRecoveryError(
                "project_repository must be a ProjectRepository"
            )
        self._project_repository = project_repository
        self._clock = clock
        self._fault_hook = fault_hook

    def execute(self, plan: CrossStoreOperationPlan) -> dict[str, Any]:
        self._validate_plan_scope(plan)
        created, record = self._create_or_validate_intent(plan)
        if created:
            self._inject(FaultPoint.AFTER_INTENT)
        else:
            if record["status"] == RecoveryStatus.COMMITTED.value:
                try:
                    self._verify_committed(plan, record)
                except RecoveryInterventionRequired as exc:
                    self._record_error(
                        plan.operation_id,
                        exc,
                        intervention=True,
                        allow_committed=True,
                    )
                    raise
                return record
            self._require_automatic_recovery(record)
            self._record_attempt(plan.operation_id, "TECHNICAL_RETRY", retry=True)
            self._inject(FaultPoint.DURING_RETRY)
        return self._run(plan)

    def verify_committed_readonly(self, operation_id: str) -> dict[str, Any]:
        """Verify a committed project artifact without recovery or error writes."""
        record = self._project_repository.get_cross_store_operation_readonly(operation_id)
        if record is None or record.get("status") != RecoveryStatus.COMMITTED.value:
            raise CrossStoreRecoveryError("cross-store operation is not committed")
        plan = CrossStoreOperationPlan.from_record(record)
        self._validate_plan_scope(plan)
        self._verify_committed(plan, record, read_only=True)
        return record

    def recover(self, operation_id: str) -> dict[str, Any]:
        record = self._project_repository.get_cross_store_operation(operation_id)
        if record is None:
            raise CrossStoreRecoveryError("cross-store operation does not exist")
        plan = CrossStoreOperationPlan.from_record(record)
        self._validate_plan_scope(plan)
        if record["status"] == RecoveryStatus.COMMITTED.value:
            try:
                if plan.operation_type == "GAP017_STEP_PROJECTION_V1":
                    path = self._artifact_path(plan.artifact_relative_path, plan.artifact_root)
                    if not path.exists():
                        self._write_artifact(plan)
                self._verify_committed(plan, record)
            except RecoveryInterventionRequired as exc:
                self._record_error(
                    plan.operation_id,
                    exc,
                    intervention=True,
                    allow_committed=True,
                )
                raise
            return record
        self._require_automatic_recovery(record)
        self._record_attempt(plan.operation_id, "RECOVERY", retry=True)
        self._inject(FaultPoint.DURING_RETRY)
        return self._run(plan)

    def recover_pending(self) -> tuple[dict[str, Any], ...]:
        recovered: list[dict[str, Any]] = []
        for record in self._project_repository.list_cross_store_operations():
            if record.get("status") in {
                RecoveryStatus.COMMITTED.value,
                RecoveryStatus.NEEDS_INTERVENTION.value,
            }:
                continue
            recovered.append(self.recover(str(record["operation_id"])))
        return tuple(recovered)

    def _validate_plan_scope(self, plan: CrossStoreOperationPlan) -> None:
        context = self._project_repository.context
        if plan.project_id != context.project_id or plan.book_id != context.book_id:
            raise CrossStoreRecoveryError(
                "operation identity does not match the owner project repository"
            )
        self._artifact_path(plan.artifact_relative_path, plan.artifact_root)

    def _create_or_validate_intent(
        self,
        plan: CrossStoreOperationPlan,
    ) -> tuple[bool, dict[str, Any]]:
        created = False
        with self._project_repository.cross_store_operation_transaction(
            plan.operation_id
        ) as (state, conn):
            if state:
                self._validate_record_identity(state, plan)
            else:
                created = True
                now = self._clock()
                planned = ["PROJECT_CHECKPOINT", "FILE_ARTIFACT"]
                if plan.series_snapshot is not None:
                    planned.append("SERIES_VOLUME_CLOSE")
                planned.extend(("LOGICAL_COMMIT", "FINAL_AUDIT"))
                state.update(
                    {
                        "schema_version": RECOVERY_RECORD_SCHEMA_VERSION,
                        "operation_id": plan.operation_id,
                        "scope": {
                            "type": "PROJECT",
                            "project_id": plan.project_id,
                            "book_id": plan.book_id,
                            "series_id": plan.series_id,
                        },
                        "owner_store": "project.db",
                        "operation_type": plan.operation_type,
                        "input_hash": plan.input_hash,
                        "expected_versions": dict(plan.expected_versions),
                        "planned_writes": planned,
                        "completed_writes": [],
                        "status": RecoveryStatus.PENDING.value,
                        "retry_count": 0,
                        "last_error_class": None,
                        "created_at": now,
                        "updated_at": now,
                        "logical_committed_at": None,
                        "committed_at": None,
                        "provenance_refs": list(plan.provenance_refs),
                        "audit_refs": [
                            _OPERATION_KEY_PREFIX + plan.operation_id,
                            _AUDIT_KEY_PREFIX + plan.operation_id,
                        ],
                        "lineage": [
                            {
                                "sequence": 1,
                                "kind": "INITIAL",
                                "at": now,
                                "resume_from": [],
                            }
                        ],
                        "payload": plan.durable_payload(),
                    }
                )
                if self._ledger_covered(plan):
                    self._project_repository.record_memory_event(
                        conn,
                        operation_namespace="CROSS_STORE_OPERATION",
                        operation_id=plan.operation_id,
                        event_slot=("intent",), event_type="ARTIFACT_WRITE_INTENDED",
                        actor={"kind": "SYSTEM", "id": "F004_CROSS_STORE_RECOVERY",
                               "evidence_ref": None},
                        run_id=plan.run_id, step_id=plan.step_id,
                        structured_payload={
                            "operation_id": plan.operation_id,
                            "input_hash": plan.input_hash,
                            "artifact_type": plan.operation_type,
                            "confined_path": plan.artifact_relative_path,
                            "expected_hash": plan.artifact_sha256,
                            "status": "INTENDED", "outcome": "PENDING",
                        },
                    )
            result = dict(state)
        return created, result

    def _validate_record_identity(
        self,
        record: Mapping[str, Any],
        plan: CrossStoreOperationPlan,
    ) -> None:
        if int(record.get("schema_version", 0)) != RECOVERY_RECORD_SCHEMA_VERSION:
            raise RecoveryInterventionRequired(
                "unsupported cross-store recovery record version"
            )
        if (
            record.get("operation_id") != plan.operation_id
            or record.get("input_hash") != plan.input_hash
            or record.get("owner_store") != "project.db"
        ):
            raise CrossStoreRecoveryError(
                "operation_id was already used for different cross-store input"
            )

    @staticmethod
    def _require_automatic_recovery(record: Mapping[str, Any]) -> None:
        if record.get("status") == RecoveryStatus.NEEDS_INTERVENTION.value:
            raise RecoveryInterventionRequired(
                "operation requires manual intervention"
            )

    def _record_attempt(self, operation_id: str, kind: str, *, retry: bool) -> None:
        with self._project_repository.cross_store_operation_transaction(
            operation_id
        ) as (state, _conn):
            self._require_automatic_recovery(state)
            if state.get("status") == RecoveryStatus.COMMITTED.value:
                return
            if retry:
                state["retry_count"] = int(state.get("retry_count", 0)) + 1
            self._append_lineage(state, kind)
            state["updated_at"] = self._clock()

    def _run(self, plan: CrossStoreOperationPlan) -> dict[str, Any]:
        try:
            self._write_project_checkpoint(plan)
            self._inject(FaultPoint.AFTER_PROJECT_WRITE)
            self._write_artifact(plan)
            if plan.series_snapshot is not None:
                self._write_series(plan)
            self._mark_logically_committed(plan)
            self._inject(FaultPoint.AFTER_LOGICAL_COMMIT)
            return self._write_final_audit(plan)
        except RecoveryInterventionRequired as exc:
            self._record_error(plan.operation_id, exc, intervention=True)
            raise
        except Exception as exc:
            self._record_error(plan.operation_id, exc, intervention=False)
            raise

    def _write_project_checkpoint(self, plan: CrossStoreOperationPlan) -> None:
        key = _PROJECT_WRITE_KEY_PREFIX + plan.operation_id
        with self._project_repository.cross_store_operation_transaction(
            plan.operation_id
        ) as (state, conn):
            self._validate_record_identity(state, plan)
            expected = {
                "operation_id": plan.operation_id,
                "input_hash": plan.input_hash,
                "source_ref": plan.source_ref,
                "expected_versions": dict(plan.expected_versions),
            }
            row = conn.execute(
                "SELECT value FROM project_metadata WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO project_metadata (key, value) VALUES (?, ?)",
                    (key, _canonical_json(expected)),
                )
            elif json.loads(str(row["value"])) != expected:
                raise RecoveryInterventionRequired(
                    "project checkpoint does not match durable intent"
                )
            self._complete(state, "PROJECT_CHECKPOINT")

    def _write_artifact(self, plan: CrossStoreOperationPlan) -> None:
        final_path = self._artifact_path(plan.artifact_relative_path, plan.artifact_root)
        temporary_path = final_path.with_name(
            final_path.name + "." + plan.operation_id + ".tmp"
        )
        expected_hash = plan.artifact_sha256
        with self._project_repository.cross_store_operation_transaction(
            plan.operation_id
        ) as (state, _conn):
            self._validate_record_identity(state, plan)
            if final_path.exists():
                if self._file_hash(final_path) != expected_hash:
                    raise RecoveryInterventionRequired(
                        "final artifact exists with an unexpected hash"
                    )
                if temporary_path.exists():
                    temporary_path.unlink()
                self._complete(state, "FILE_ARTIFACT")
                return

            final_path.parent.mkdir(parents=True, exist_ok=True)
            if temporary_path.exists():
                if self._file_hash(temporary_path) != expected_hash:
                    raise RecoveryInterventionRequired(
                        "prepared artifact exists with an unexpected hash"
                    )
            else:
                with temporary_path.open("xb") as handle:
                    handle.write(plan.artifact_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                if self._file_hash(temporary_path) != expected_hash:
                    raise CrossStoreRecoveryError(
                        "prepared artifact failed hash verification"
                    )

            self._inject(FaultPoint.AFTER_ARTIFACT_PREPARE)
            os.replace(temporary_path, final_path)
            self._fsync_directory(final_path.parent)
            if self._file_hash(final_path) != expected_hash:
                raise RecoveryInterventionRequired(
                    "committed artifact failed hash verification"
                )
            self._inject(FaultPoint.AFTER_ARTIFACT_COMMIT)
            self._complete(state, "FILE_ARTIFACT")

    def _write_series(self, plan: CrossStoreOperationPlan) -> None:
        snapshot = plan.series_snapshot
        if snapshot is None or plan.series_id is None:
            return
        resolver = StorageResolver(self._project_repository.context.storage_root)
        series_repository = SeriesRepository(resolver.resolve_series(plan.series_id))
        access = SeriesAccessContext.bind(plan.project_id, plan.series_id)
        with self._project_repository.cross_store_operation_transaction(
            plan.operation_id
        ) as (state, _conn):
            self._validate_record_identity(state, plan)
            series_repository.require_registered_member(access, book_id=plan.book_id)
            persisted = series_repository.close_volume(access, snapshot)
            if (
                persisted.operation_id != snapshot.operation_id
                or persisted.semantic_identity != snapshot.semantic_identity
            ):
                raise RecoveryInterventionRequired(
                    "series repository returned a different durable result"
                )
            self._inject(FaultPoint.AFTER_SERIES_WRITE)
            self._complete(state, "SERIES_VOLUME_CLOSE")

    def _mark_logically_committed(self, plan: CrossStoreOperationPlan) -> None:
        with self._project_repository.cross_store_operation_transaction(
            plan.operation_id
        ) as (state, _conn):
            self._validate_record_identity(state, plan)
            missing = [
                step
                for step in state["planned_writes"]
                if step not in {"LOGICAL_COMMIT", "FINAL_AUDIT"}
                and step not in state["completed_writes"]
            ]
            if missing:
                raise CrossStoreRecoveryError(
                    "logical commit attempted before required writes: "
                    + ", ".join(missing)
                )
            self._complete(state, "LOGICAL_COMMIT")
            state["status"] = RecoveryStatus.AUDIT_PENDING.value
            if state.get("logical_committed_at") is None:
                state["logical_committed_at"] = self._clock()

    def _write_final_audit(self, plan: CrossStoreOperationPlan) -> dict[str, Any]:
        key = _AUDIT_KEY_PREFIX + plan.operation_id
        with self._project_repository.cross_store_operation_transaction(
            plan.operation_id
        ) as (state, conn):
            self._validate_record_identity(state, plan)
            self._complete(state, "FINAL_AUDIT")
            state["status"] = RecoveryStatus.COMMITTED.value
            state["last_error_class"] = None
            if state.get("committed_at") is None:
                state["committed_at"] = self._clock()
            self._append_lineage(state, "FINAL_AUDIT")
            audit = self._audit_document(state)
            row = conn.execute(
                "SELECT value FROM project_metadata WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO project_metadata (key, value) VALUES (?, ?)",
                    (key, _canonical_json(audit)),
                )
            else:
                existing = json.loads(str(row["value"]))
                if existing.get("input_hash") != plan.input_hash:
                    raise RecoveryInterventionRequired(
                        "final audit does not match durable intent"
                    )
            if self._ledger_covered(plan):
                final_path = self._artifact_path(
                    plan.artifact_relative_path, plan.artifact_root,
                )
                if not final_path.is_file() or self._file_hash(final_path) != plan.artifact_sha256:
                    raise RecoveryInterventionRequired(
                        "artifact confirmation requires the expected durable file"
                    )
                self._project_repository.record_memory_event(
                    conn,
                    operation_namespace="CROSS_STORE_OPERATION",
                    operation_id=plan.operation_id,
                    event_slot=("confirmed",), event_type="ARTIFACT_WRITE_CONFIRMED",
                    actor={"kind": "SYSTEM", "id": "F004_CROSS_STORE_RECOVERY",
                           "evidence_ref": None},
                    run_id=plan.run_id, step_id=plan.step_id,
                    structured_payload={
                        "operation_id": plan.operation_id,
                        "input_hash": plan.input_hash,
                        "artifact_type": plan.operation_type,
                        "confined_path": plan.artifact_relative_path,
                        "expected_hash": plan.artifact_sha256,
                        "observed_hash": plan.artifact_sha256,
                        "status": "CONFIRMED", "outcome": "COMMITTED",
                    },
                )
                if plan.operation_type == _LEDGER_CHAPTER_OPERATION:
                    self._record_chapter_events(conn, plan)
            result = dict(state)
        return result

    @staticmethod
    def _ledger_covered(plan: CrossStoreOperationPlan) -> bool:
        return (
            plan.operation_type == _LEDGER_CHAPTER_OPERATION
            or plan.operation_type in _LEDGER_GAP018_OPERATIONS
            or plan.series_snapshot is not None
        )

    def _record_chapter_events(
        self, connection, plan: CrossStoreOperationPlan,
    ) -> None:
        try:
            document = json.loads(plan.artifact_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RecoveryInterventionRequired(
                "chapter lineage artifact is not valid JSON"
            ) from exc
        chapter_id = str(document.get("chapter_id") or "")
        versions = document.get("versions")
        if not chapter_id or not isinstance(versions, list) or not versions:
            raise RecoveryInterventionRequired(
                "chapter lineage artifact has no version history"
            )
        for version in versions:
            number = version.get("version")
            if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                raise RecoveryInterventionRequired("chapter version is invalid")
            evaluation = version.get("source_evaluation")
            quality_ref = None
            if isinstance(evaluation, dict):
                quality_ref = evaluation.get("artifact_path")
            if quality_ref is None and number == document.get("version"):
                final_evaluation = document.get("quality_evaluation")
                if isinstance(final_evaluation, dict):
                    quality_ref = final_evaluation.get("artifact_path")
            status = version.get("status")
            if status not in {"SUPERSEDED", "ACCEPTED"}:
                raise RecoveryInterventionRequired("chapter version status is invalid")
            self._project_repository.record_memory_event(
                connection,
                operation_namespace="CROSS_STORE_OPERATION",
                operation_id=plan.operation_id,
                event_slot=("chapter-version", chapter_id, str(number)),
                event_type="CHAPTER_VERSION_RECORDED",
                actor={"kind": "SYSTEM", "id": "F005_CHAPTER_LINEAGE",
                       "evidence_ref": None},
                run_id=plan.run_id, step_id=version.get("step_id") or plan.step_id,
                structured_payload={
                    "operation_id": plan.operation_id,
                    "input_hash": plan.input_hash,
                    "artifact_type": plan.operation_type,
                    "confined_path": plan.artifact_relative_path,
                    "observed_hash": plan.artifact_sha256,
                    "chapter_id": chapter_id, "version": number,
                    "status": status,
                    "text_hash": str(version.get("artifact_hash") or ""),
                    "quality_evaluation_ref": quality_ref,
                    "outcome": "RECORDED",
                },
            )

    def _verify_committed(
        self,
        plan: CrossStoreOperationPlan,
        record: Mapping[str, Any],
        *,
        read_only: bool = False,
    ) -> None:
        self._validate_record_identity(record, plan)
        if set(record.get("planned_writes") or ()) != set(
            record.get("completed_writes") or ()
        ):
            raise RecoveryInterventionRequired(
                "committed operation has incomplete durable steps"
            )
        final_path = self._artifact_path(plan.artifact_relative_path, plan.artifact_root)
        if not final_path.is_file() or self._file_hash(final_path) != plan.artifact_sha256:
            raise RecoveryInterventionRequired(
                "committed operation artifact is missing or has an unexpected hash"
            )
        read_metadata = (self._project_repository.get_metadata_readonly if read_only
                         else self._project_repository.get_metadata)
        audit_value = read_metadata(_AUDIT_KEY_PREFIX + plan.operation_id)
        if audit_value is None or json.loads(audit_value).get("input_hash") != plan.input_hash:
            raise RecoveryInterventionRequired(
                "committed operation audit is missing or inconsistent"
            )
        if self._ledger_covered(plan):
            required_slots: list[tuple[str, ...]] = [("intent",), ("confirmed",)]
            if plan.operation_type == _LEDGER_CHAPTER_OPERATION:
                try:
                    chapter_document = json.loads(plan.artifact_bytes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RecoveryInterventionRequired(
                        "chapter lineage artifact is not valid JSON"
                    ) from exc
                chapter_id = str(chapter_document.get("chapter_id") or "")
                versions = chapter_document.get("versions")
                if not chapter_id or not isinstance(versions, list):
                    raise RecoveryInterventionRequired(
                        "chapter lineage artifact is incomplete"
                    )
                required_slots.extend(
                    ("chapter-version", chapter_id, str(version.get("version")))
                    for version in versions
                )
            for event_slot in required_slots:
                classification = self._project_repository.classify_required_memory_event(
                    operation_namespace="CROSS_STORE_OPERATION",
                    operation_id=plan.operation_id,
                    event_slot=event_slot,
                    baseline_locator="metadata:" + _OPERATION_KEY_PREFIX + plan.operation_id,
                )
                if classification == "MEMORY_LEDGER_NEEDS_INTERVENTION":
                    raise RecoveryInterventionRequired(
                        "committed operation is missing a required Memory Ledger event"
                    )
        if plan.series_snapshot is not None and plan.series_id is not None:
            resolver = StorageResolver(self._project_repository.context.storage_root)
            series_repository = SeriesRepository(resolver.resolve_series(plan.series_id))
            access = SeriesAccessContext.bind(plan.project_id, plan.series_id)
            series_repository.require_registered_member(access, book_id=plan.book_id)
            snapshots = series_repository.list_volume_snapshots(access)
            matching = tuple(
                snapshot
                for snapshot in snapshots
                if snapshot.operation_id == plan.series_snapshot.operation_id
            )
            if matching != (plan.series_snapshot,):
                raise RecoveryInterventionRequired(
                    "committed series snapshot is missing or inconsistent"
                )
            for item in plan.series_snapshot.items:
                if item.transfer_target == VolumeTransferTarget.SNAPSHOT_ONLY:
                    continue
                expected = series_state_from_snapshot_item(plan.series_snapshot, item)
                records = (
                    series_repository.list_series_canon(access)
                    if expected.state_kind == SeriesStateKind.CANON
                    else series_repository.list_series_memory(access)
                )
                if expected not in records:
                    raise RecoveryInterventionRequired(
                        "committed series transfer is missing or inconsistent"
                    )

    def _record_error(
        self,
        operation_id: str,
        error: Exception,
        *,
        intervention: bool,
        allow_committed: bool = False,
    ) -> None:
        with self._project_repository.cross_store_operation_transaction(
            operation_id
        ) as (state, _conn):
            if (
                state.get("status") == RecoveryStatus.COMMITTED.value
                and not allow_committed
            ):
                return
            error_class = type(error).__name__
            state["last_error_class"] = error_class
            if intervention:
                state["status"] = RecoveryStatus.NEEDS_INTERVENTION.value
                kind = "MANUAL_ESCALATION_REQUIRED"
            else:
                state["status"] = RecoveryStatus.IN_PROGRESS.value
                kind = "ERROR"
            self._append_lineage(state, kind, error_class=error_class)
            state["updated_at"] = self._clock()

    def _complete(self, state: dict[str, Any], step: str) -> None:
        completed = state.setdefault("completed_writes", [])
        if step not in completed:
            completed.append(step)
        if state.get("status") == RecoveryStatus.PENDING.value:
            state["status"] = RecoveryStatus.IN_PROGRESS.value
        state["updated_at"] = self._clock()

    def _append_lineage(
        self,
        state: dict[str, Any],
        kind: str,
        *,
        error_class: str | None = None,
    ) -> None:
        lineage = state.setdefault("lineage", [])
        event: dict[str, Any] = {
            "sequence": len(lineage) + 1,
            "kind": kind,
            "at": self._clock(),
            "resume_from": list(state.get("completed_writes") or ()),
        }
        if error_class is not None:
            event["error_class"] = error_class
        lineage.append(event)

    @staticmethod
    def _audit_document(state: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": RECOVERY_RECORD_SCHEMA_VERSION,
            "operation_id": state["operation_id"],
            "operation_type": state["operation_type"],
            "scope": state["scope"],
            "owner_store": state["owner_store"],
            "input_hash": state["input_hash"],
            "expected_versions": state["expected_versions"],
            "planned_writes": state["planned_writes"],
            "completed_writes": state["completed_writes"],
            "status": state["status"],
            "retry_count": state["retry_count"],
            "created_at": state["created_at"],
            "logical_committed_at": state["logical_committed_at"],
            "committed_at": state["committed_at"],
            "provenance_refs": state["provenance_refs"],
            "audit_refs": state["audit_refs"],
            "lineage": state["lineage"],
        }

    def _artifact_path(
        self,
        relative_path: str,
        artifact_root: ArtifactRoot,
    ) -> Path:
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise CrossStoreRecoveryError(
                "artifact_relative_path must stay inside its configured root"
            )
        root = (
            self._project_repository.context.project_root
            if artifact_root == ArtifactRoot.PROJECT
            else self._project_repository.context.storage_root / (
                "runs" if artifact_root == ArtifactRoot.RUNS else "books"
            )
        ).resolve()
        resolved = (root / path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise CrossStoreRecoveryError(
                "artifact_relative_path escapes its configured root"
            ) from exc
        return resolved

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _inject(self, point: FaultPoint) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)


__all__ = [
    "RECOVERY_RECORD_SCHEMA_VERSION",
    "ArtifactRoot",
    "CrossStoreOperationPlan",
    "CrossStoreRecoveryError",
    "CrossStoreRecoveryService",
    "FaultPoint",
    "InjectedRecoveryCrash",
    "RecoveryInterventionRequired",
    "RecoveryStatus",
]

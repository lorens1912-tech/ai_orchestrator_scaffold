from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields, replace
from enum import Enum
from typing import Any, Iterable

from app.p20_core.domain_records import (
    DomainContractError,
    DomainId,
    DomainNamespace,
    require_domain_id,
)
from app.p20_core.project_repository import SeriesAccessContext, StorageScope


SERIES_MEMORY_SCHEMA_VERSION = 1
VOLUME_CLOSING_SNAPSHOT_SCHEMA_VERSION = 1
_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,191}$")


class SeriesMemoryContractError(ValueError):
    pass


class SeriesStateKind(str, Enum):
    CANON = "SERIES_CANON"
    MEMORY = "SERIES_MEMORY"


class VolumeTransferTarget(str, Enum):
    SNAPSHOT_ONLY = "SNAPSHOT_ONLY"
    SERIES_MEMORY = "SERIES_MEMORY"
    SERIES_CANON = "SERIES_CANON"


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SeriesMemoryContractError(f"{field_name} is required")
    if value != value.strip():
        raise SeriesMemoryContractError(
            f"{field_name} must not contain leading or trailing whitespace"
        )
    return value


def _safe_operation_id(value: Any, field_name: str) -> str:
    normalized = _required_text(value, field_name)
    if not _SAFE_OPERATION_ID.fullmatch(normalized):
        raise SeriesMemoryContractError(f"{field_name} contains unsafe characters")
    return normalized


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SeriesMemoryContractError(f"{field_name} must be a positive integer")
    return value


def _bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise SeriesMemoryContractError(f"{field_name} must be a boolean")
    return value


def _coerce_enum(enum_type: type[Enum], value: Any, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise SeriesMemoryContractError(f"unsupported {field_name}: {value!r}") from exc


def _json_value(value: Any, field_name: str) -> Any:
    try:
        return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))
    except (TypeError, ValueError) as exc:
        raise SeriesMemoryContractError(f"{field_name} must be JSON-serializable") from exc


def _serialize(value: Any) -> Any:
    if isinstance(value, DomainId):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _serialize(to_dict())
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(value[key]) for key in sorted(value)}
    return value


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _semantic_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class _Serializable:
    def to_dict(self) -> dict[str, Any]:
        return {
            field.name: _serialize(getattr(self, field.name))
            for field in fields(self)
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    def with_updates(self, **changes: Any):
        return replace(self, **changes)


@dataclass(frozen=True)
class SeriesMembershipRecord(_Serializable):
    series_id: DomainId | str
    project_id: DomainId | str
    book_id: DomainId | str
    source_ref: str
    version: int
    created_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", require_domain_id(
            self.series_id, DomainNamespace.SERIES, "series_id"
        ))
        object.__setattr__(self, "project_id", require_domain_id(
            self.project_id, DomainNamespace.PROJECT, "project_id"
        ))
        object.__setattr__(self, "book_id", require_domain_id(
            self.book_id, DomainNamespace.BOOK, "book_id"
        ))
        object.__setattr__(self, "source_ref", _required_text(self.source_ref, "source_ref"))
        object.__setattr__(self, "version", _positive_int(self.version, "version"))
        object.__setattr__(self, "created_at", _required_text(self.created_at, "created_at"))

    @property
    def scope(self) -> StorageScope:
        return StorageScope.series(str(self.series_id))

    def require_access(self, access: SeriesAccessContext) -> None:
        if not isinstance(access, SeriesAccessContext):
            raise SeriesMemoryContractError("access must be a SeriesAccessContext")
        access.require_series(str(self.series_id))
        access.require_project(str(self.project_id))


@dataclass(frozen=True)
class SeriesStateRecord(_Serializable):
    series_id: DomainId | str
    state_kind: SeriesStateKind | str
    source_project_id: DomainId | str
    source_book_id: DomainId | str
    record_type: str
    record_id: DomainId | str
    source_version: int
    source_ref: str
    provenance_refs: Iterable[str]
    transfer_reason: str
    state: dict[str, Any]
    operation_id: str
    version: int
    frozen: bool
    author_locked: bool
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", require_domain_id(
            self.series_id, DomainNamespace.SERIES, "series_id"
        ))
        object.__setattr__(self, "state_kind", _coerce_enum(
            SeriesStateKind, self.state_kind, "state_kind"
        ))
        object.__setattr__(self, "source_project_id", require_domain_id(
            self.source_project_id, DomainNamespace.PROJECT, "source_project_id"
        ))
        object.__setattr__(self, "source_book_id", require_domain_id(
            self.source_book_id, DomainNamespace.BOOK, "source_book_id"
        ))
        object.__setattr__(self, "record_type", _required_text(self.record_type, "record_type"))
        try:
            record_id = self.record_id if isinstance(self.record_id, DomainId) else DomainId.parse(self.record_id)
        except DomainContractError as exc:
            raise SeriesMemoryContractError("record_id must be a stable domain id") from exc
        object.__setattr__(self, "record_id", record_id)
        object.__setattr__(self, "source_version", _positive_int(
            self.source_version, "source_version"
        ))
        object.__setattr__(self, "source_ref", _required_text(self.source_ref, "source_ref"))
        if isinstance(self.provenance_refs, (str, bytes)):
            raise SeriesMemoryContractError("provenance_refs must be a list")
        refs = tuple(_required_text(value, "provenance_refs") for value in self.provenance_refs)
        if not refs:
            raise SeriesMemoryContractError("provenance_refs is required")
        object.__setattr__(self, "provenance_refs", refs)
        object.__setattr__(self, "transfer_reason", _required_text(
            self.transfer_reason, "transfer_reason"
        ))
        state = _json_value(self.state, "state")
        if not isinstance(state, dict):
            raise SeriesMemoryContractError("state must be an object")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "operation_id", _safe_operation_id(
            self.operation_id, "operation_id"
        ))
        object.__setattr__(self, "version", _positive_int(self.version, "version"))
        object.__setattr__(self, "frozen", _bool(self.frozen, "frozen"))
        object.__setattr__(self, "author_locked", _bool(
            self.author_locked, "author_locked"
        ))
        for field_name in ("created_at", "updated_at"):
            object.__setattr__(self, field_name, _required_text(
                getattr(self, field_name), field_name
            ))

    @property
    def scope(self) -> StorageScope:
        return StorageScope.series(str(self.series_id))

    @property
    def semantic_identity(self) -> str:
        return _semantic_hash(self.semantic_payload())

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "series_id": str(self.series_id),
            "state_kind": self.state_kind.value,
            "source_project_id": str(self.source_project_id),
            "source_book_id": str(self.source_book_id),
            "record_type": self.record_type,
            "record_id": str(self.record_id),
            "source_version": self.source_version,
            "source_ref": self.source_ref,
            "provenance_refs": list(self.provenance_refs),
            "transfer_reason": self.transfer_reason,
            "state": self.state,
            "version": self.version,
            "frozen": self.frozen,
            "author_locked": self.author_locked,
        }

    def require_access(self, access: SeriesAccessContext) -> None:
        if not isinstance(access, SeriesAccessContext):
            raise SeriesMemoryContractError("access must be a SeriesAccessContext")
        access.require_series(str(self.series_id))
        access.require_project(str(self.source_project_id))

    @classmethod
    def from_project_record(
        cls,
        *,
        access: SeriesAccessContext,
        source_book_id: DomainId | str,
        record: Any,
        state_kind: SeriesStateKind | str,
        source_ref: str,
        provenance_refs: Iterable[str],
        transfer_reason: str,
        operation_id: str,
        created_at: str,
        updated_at: str,
    ) -> SeriesStateRecord:
        record_project_id = str(getattr(record, "project_id", ""))
        access.require_project(record_project_id)
        to_dict = getattr(record, "to_dict", None)
        if not callable(to_dict):
            raise SeriesMemoryContractError("project record must provide to_dict")
        return cls(
            series_id=access.series_id,
            state_kind=state_kind,
            source_project_id=record_project_id,
            source_book_id=source_book_id,
            record_type=getattr(record, "memory_record_type", ""),
            record_id=getattr(record, "record_id", ""),
            source_version=getattr(record, "version", 0),
            source_ref=source_ref,
            provenance_refs=provenance_refs,
            transfer_reason=transfer_reason,
            state=to_dict(),
            operation_id=operation_id,
            version=getattr(record, "version", 0),
            frozen=bool(getattr(record, "frozen", False)),
            author_locked=bool(getattr(record, "author_locked", False)),
            created_at=created_at,
            updated_at=updated_at,
        )


@dataclass(frozen=True)
class VolumeSnapshotItem(_Serializable):
    record_type: str
    record_id: DomainId | str
    source_version: int
    source_ref: str
    provenance_refs: Iterable[str]
    state: dict[str, Any]
    transfer_target: VolumeTransferTarget | str = VolumeTransferTarget.SNAPSHOT_ONLY
    transfer_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_type", _required_text(self.record_type, "record_type"))
        try:
            record_id = self.record_id if isinstance(self.record_id, DomainId) else DomainId.parse(self.record_id)
        except DomainContractError as exc:
            raise SeriesMemoryContractError("record_id must be a stable domain id") from exc
        object.__setattr__(self, "record_id", record_id)
        object.__setattr__(self, "source_version", _positive_int(
            self.source_version, "source_version"
        ))
        object.__setattr__(self, "source_ref", _required_text(self.source_ref, "source_ref"))
        if isinstance(self.provenance_refs, (str, bytes)):
            raise SeriesMemoryContractError("provenance_refs must be a list")
        refs = tuple(_required_text(value, "provenance_refs") for value in self.provenance_refs)
        if not refs:
            raise SeriesMemoryContractError("provenance_refs is required")
        object.__setattr__(self, "provenance_refs", refs)
        state = _json_value(self.state, "state")
        if not isinstance(state, dict):
            raise SeriesMemoryContractError("state must be an object")
        object.__setattr__(self, "state", state)
        target = _coerce_enum(VolumeTransferTarget, self.transfer_target, "transfer_target")
        object.__setattr__(self, "transfer_target", target)
        reason = self.transfer_reason
        if reason is not None:
            reason = _required_text(reason, "transfer_reason")
        if target != VolumeTransferTarget.SNAPSHOT_ONLY and reason is None:
            raise SeriesMemoryContractError("transfer_reason is required for series transfer")
        if (
            self.record_type == "THREAD"
            and str(self.state.get("status", "")).upper() == "CLOSED"
            and target != VolumeTransferTarget.SNAPSHOT_ONLY
        ):
            raise SeriesMemoryContractError("closed thread cannot be transferred to series state")
        object.__setattr__(self, "transfer_reason", reason)

    @classmethod
    def from_project_record(
        cls,
        record: Any,
        *,
        source_ref: str,
        provenance_refs: Iterable[str],
        transfer_target: VolumeTransferTarget | str = VolumeTransferTarget.SNAPSHOT_ONLY,
        transfer_reason: str | None = None,
    ) -> VolumeSnapshotItem:
        to_dict = getattr(record, "to_dict", None)
        if not callable(to_dict):
            raise SeriesMemoryContractError("project record must provide to_dict")
        return cls(
            record_type=getattr(record, "memory_record_type", ""),
            record_id=getattr(record, "record_id", ""),
            source_version=getattr(record, "version", 0),
            source_ref=source_ref,
            provenance_refs=provenance_refs,
            state=to_dict(),
            transfer_target=transfer_target,
            transfer_reason=transfer_reason,
        )


@dataclass(frozen=True)
class VolumeClosingSnapshot(_Serializable):
    snapshot_id: str
    operation_id: str
    series_id: DomainId | str
    project_id: DomainId | str
    book_id: DomainId | str
    source_state_version: int
    items: Iterable[VolumeSnapshotItem]
    created_at: str
    schema_version: int = VOLUME_CLOSING_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _safe_operation_id(
            self.snapshot_id, "snapshot_id"
        ))
        object.__setattr__(self, "operation_id", _safe_operation_id(
            self.operation_id, "operation_id"
        ))
        object.__setattr__(self, "series_id", require_domain_id(
            self.series_id, DomainNamespace.SERIES, "series_id"
        ))
        object.__setattr__(self, "project_id", require_domain_id(
            self.project_id, DomainNamespace.PROJECT, "project_id"
        ))
        object.__setattr__(self, "book_id", require_domain_id(
            self.book_id, DomainNamespace.BOOK, "book_id"
        ))
        object.__setattr__(self, "source_state_version", _positive_int(
            self.source_state_version, "source_state_version"
        ))
        if isinstance(self.items, (str, bytes)):
            raise SeriesMemoryContractError("items must be a list")
        items = tuple(
            item if isinstance(item, VolumeSnapshotItem) else VolumeSnapshotItem(**item)
            for item in self.items
        )
        identities = [(item.record_type, str(item.record_id)) for item in items]
        if len(identities) != len(set(identities)):
            raise SeriesMemoryContractError("snapshot item identities must be unique")
        object.__setattr__(
            self,
            "items",
            tuple(sorted(items, key=lambda item: (item.record_type, str(item.record_id)))),
        )
        object.__setattr__(self, "created_at", _required_text(self.created_at, "created_at"))
        object.__setattr__(self, "schema_version", _positive_int(
            self.schema_version, "schema_version"
        ))

    @property
    def scope(self) -> StorageScope:
        return StorageScope.series(str(self.series_id))

    @property
    def semantic_identity(self) -> str:
        return _semantic_hash(self.semantic_payload())

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "series_id": str(self.series_id),
            "project_id": str(self.project_id),
            "book_id": str(self.book_id),
            "source_state_version": self.source_state_version,
            "items": [item.to_dict() for item in self.items],
            "schema_version": self.schema_version,
        }

    def require_access(self, access: SeriesAccessContext) -> None:
        if not isinstance(access, SeriesAccessContext):
            raise SeriesMemoryContractError("access must be a SeriesAccessContext")
        access.require_series(str(self.series_id))
        access.require_project(str(self.project_id))


@dataclass(frozen=True)
class SeriesOpeningState(_Serializable):
    series_id: DomainId | str
    project_id: DomainId | str
    series_canon: Iterable[SeriesStateRecord]
    series_memory: Iterable[SeriesStateRecord]
    latest_snapshot: VolumeClosingSnapshot | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", require_domain_id(
            self.series_id, DomainNamespace.SERIES, "series_id"
        ))
        object.__setattr__(self, "project_id", require_domain_id(
            self.project_id, DomainNamespace.PROJECT, "project_id"
        ))
        object.__setattr__(self, "series_canon", tuple(self.series_canon))
        object.__setattr__(self, "series_memory", tuple(self.series_memory))


def series_state_from_snapshot_item(
    snapshot: VolumeClosingSnapshot,
    item: VolumeSnapshotItem,
) -> SeriesStateRecord:
    if item.transfer_target == VolumeTransferTarget.SNAPSHOT_ONLY:
        raise SeriesMemoryContractError("snapshot-only item cannot become series state")
    state_kind = (
        SeriesStateKind.CANON
        if item.transfer_target == VolumeTransferTarget.SERIES_CANON
        else SeriesStateKind.MEMORY
    )
    operation_digest = hashlib.sha256(
        f"{snapshot.operation_id}|{state_kind.value}|{item.record_type}|{item.record_id}".encode(
            "utf-8"
        )
    ).hexdigest()
    return SeriesStateRecord(
        series_id=snapshot.series_id,
        state_kind=state_kind,
        source_project_id=snapshot.project_id,
        source_book_id=snapshot.book_id,
        record_type=item.record_type,
        record_id=item.record_id,
        source_version=item.source_version,
        source_ref=item.source_ref,
        provenance_refs=item.provenance_refs,
        transfer_reason=item.transfer_reason or "explicit series transfer",
        state=item.state,
        operation_id=f"series-transfer-{operation_digest}",
        version=item.source_version,
        frozen=bool(item.state.get("frozen", False)),
        author_locked=bool(item.state.get("author_locked", False)),
        created_at=snapshot.created_at,
        updated_at=snapshot.created_at,
    )


__all__ = [
    "SERIES_MEMORY_SCHEMA_VERSION",
    "VOLUME_CLOSING_SNAPSHOT_SCHEMA_VERSION",
    "SeriesMembershipRecord",
    "SeriesMemoryContractError",
    "SeriesOpeningState",
    "SeriesStateKind",
    "SeriesStateRecord",
    "VolumeClosingSnapshot",
    "VolumeSnapshotItem",
    "VolumeTransferTarget",
    "series_state_from_snapshot_item",
]

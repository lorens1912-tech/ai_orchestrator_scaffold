from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields, replace
from enum import Enum
from typing import Any, Iterable

from app.p20_core.project_repository import StorageScope


DOMAIN_RECORD_SCHEMA_VERSION = 1

_SAFE_ID_BODY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class DomainContractError(ValueError):
    pass


class DomainNamespace(str, Enum):
    PROJECT = "PROJ"
    SERIES = "SERIES"
    BOOK = "BOOK"
    ACT = "ACT"
    SEQUENCE = "SEQ"
    CHAPTER = "CHAPTER"
    SCENE = "SCENE"
    CHARACTER = "CHAR"
    VOICE = "VOICE"
    ARC = "ARC"
    RELATIONSHIP = "REL"
    PLACE = "PLACE"
    ROUTE = "ROUTE"
    ORGANIZATION = "ORG"
    OBJECT = "OBJECT"
    FACT = "FACT"
    EVENT = "EVENT"
    THREAD = "THREAD"
    SETUP = "SETUP"
    SOURCE = "SOURCE"
    RESEARCH = "RESEARCH"
    CLAIM = "CLAIM"
    TERM = "TERM"
    DECISION = "DECISION"
    CONFLICT = "CONFLICT"
    EVALUATION = "EVALUATION"
    CONTEXT = "CONTEXT"


_PREFIX_TO_NAMESPACE = {namespace.value: namespace for namespace in DomainNamespace}


def _coerce_namespace(namespace: DomainNamespace | str) -> DomainNamespace:
    if isinstance(namespace, DomainNamespace):
        return namespace
    try:
        return DomainNamespace(str(namespace))
    except ValueError as exc:
        raise DomainContractError(f"unsupported domain namespace: {namespace!r}") from exc


def _ensure_json_compatible(value: Any, field_name: str) -> Any:
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise DomainContractError(f"{field_name} must be JSON-serializable") from exc
    return value


def _validate_id_value(namespace: DomainNamespace, value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise DomainContractError(f"{field_name} must be a string")
    if value != value.strip():
        raise DomainContractError(f"{field_name} must not contain leading or trailing whitespace")
    if not value:
        raise DomainContractError(f"{field_name} is required")
    if "/" in value or "\\" in value:
        raise DomainContractError(f"{field_name} contains unsafe path characters")
    if value in {".", ".."}:
        raise DomainContractError(f"{field_name} must not be a relative path marker")

    prefix = namespace.value
    expected = f"{prefix}-"
    if not value.startswith(expected):
        raise DomainContractError(f"{field_name} must use {prefix}- namespace")

    body = value[len(expected):]
    if not body:
        raise DomainContractError(f"{field_name} body is required")
    if body in {".", ".."}:
        raise DomainContractError(f"{field_name} body must not be a relative path marker")
    if not _SAFE_ID_BODY.fullmatch(body):
        raise DomainContractError(f"{field_name} contains unsafe characters")
    return value


@dataclass(frozen=True)
class DomainId:
    namespace: DomainNamespace | str
    value: str

    def __post_init__(self) -> None:
        namespace = _coerce_namespace(self.namespace)
        value = _validate_id_value(namespace, self.value, "domain_id")
        object.__setattr__(self, "namespace", namespace)
        object.__setattr__(self, "value", value)

    @classmethod
    def parse(cls, value: str) -> DomainId:
        if not isinstance(value, str) or "-" not in value:
            raise DomainContractError("domain_id must include an explicit namespace prefix")
        prefix = value.split("-", 1)[0]
        namespace = _PREFIX_TO_NAMESPACE.get(prefix)
        if namespace is None:
            raise DomainContractError(f"unsupported domain namespace prefix: {prefix!r}")
        return cls(namespace=namespace, value=value)

    def require_namespace(self, namespace: DomainNamespace | str, field_name: str = "domain_id") -> DomainId:
        expected = _coerce_namespace(namespace)
        if self.namespace != expected:
            raise DomainContractError(f"{field_name} must use {expected.value}- namespace")
        return self

    def to_dict(self) -> dict[str, str]:
        return {
            "namespace": self.namespace.value,
            "value": self.value,
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    def __str__(self) -> str:
        return self.value


def build_domain_id(namespace: DomainNamespace | str, local_id: str) -> DomainId:
    resolved = _coerce_namespace(namespace)
    if not isinstance(local_id, str):
        raise DomainContractError("local_id must be a string")
    if local_id != local_id.strip() or not local_id:
        raise DomainContractError("local_id is required")
    if "/" in local_id or "\\" in local_id:
        raise DomainContractError("local_id contains unsafe path characters")
    if local_id in {".", ".."} or not _SAFE_ID_BODY.fullmatch(local_id):
        raise DomainContractError("local_id contains unsafe characters")
    return DomainId(resolved, f"{resolved.value}-{local_id}")


def parse_domain_id(value: str) -> DomainId:
    return DomainId.parse(value)


def require_domain_id(
    value: DomainId | str,
    namespace: DomainNamespace | str,
    field_name: str,
) -> DomainId:
    resolved = value if isinstance(value, DomainId) else DomainId.parse(value)
    return resolved.require_namespace(namespace, field_name)


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise DomainContractError(f"{field_name} must be a string")
    if value != value.strip() or not value:
        raise DomainContractError(f"{field_name} is required")
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _optional_id(
    value: DomainId | str | None,
    namespace: DomainNamespace,
    field_name: str,
) -> DomainId | None:
    if value is None:
        return None
    return require_domain_id(value, namespace, field_name)


def _positive_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DomainContractError(f"{field_name} must be a positive integer")
    return value


def _non_negative_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainContractError(f"{field_name} must be a non-negative integer")
    return value


def _number(value: Any, field_name: str) -> int | float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DomainContractError(f"{field_name} must be a number")
    return value


def _id_list(
    values: Iterable[DomainId | str],
    namespace: DomainNamespace,
    field_name: str,
) -> tuple[DomainId, ...]:
    if isinstance(values, (str, bytes)) or values is None:
        raise DomainContractError(f"{field_name} must be a list")
    return tuple(require_domain_id(value, namespace, field_name) for value in values)


def _serialize_value(value: Any) -> Any:
    if isinstance(value, DomainId):
        return value.value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_value(value[key]) for key in sorted(value)}
    return value


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class SerializableRecord:
    def to_dict(self) -> dict[str, Any]:
        return {
            item.name: _serialize_value(getattr(self, item.name))
            for item in fields(self)
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    def with_updates(self, **changes: Any):
        return replace(self, **changes)


@dataclass(frozen=True)
class ProjectRecord(SerializableRecord):
    project_id: DomainId | str
    book_id: DomainId | str
    series_id: DomainId | str | None
    title: str
    project_type: str
    source_language: str
    target_market: str
    status: str
    canon_version: int
    book_bible_version: int
    style_profile_version: int
    active_run_id: str | None
    storage_namespace: str
    schema_version: int
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"))
        object.__setattr__(self, "book_id", require_domain_id(self.book_id, DomainNamespace.BOOK, "book_id"))
        object.__setattr__(self, "series_id", _optional_id(self.series_id, DomainNamespace.SERIES, "series_id"))
        for name in ("title", "project_type", "source_language", "target_market", "status", "storage_namespace", "created_at", "updated_at"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        for name in ("canon_version", "book_bible_version", "style_profile_version", "schema_version"):
            object.__setattr__(self, name, _positive_int(getattr(self, name), name))
        if self.active_run_id is not None:
            object.__setattr__(self, "active_run_id", _required_text(self.active_run_id, "active_run_id"))

    @property
    def scope(self) -> StorageScope:
        return StorageScope.project(str(self.project_id))

    def require_scope(self, scope: StorageScope) -> None:
        if scope != self.scope:
            raise DomainContractError("project record scope does not match")


@dataclass(frozen=True)
class SeriesRecord(SerializableRecord):
    series_id: DomainId | str
    name: str
    description: str
    series_canon_version: int
    status: str
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", require_domain_id(self.series_id, DomainNamespace.SERIES, "series_id"))
        for name in ("name", "description", "status", "created_at", "updated_at"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "series_canon_version", _positive_int(self.series_canon_version, "series_canon_version"))

    @property
    def scope(self) -> StorageScope:
        return StorageScope.series(str(self.series_id))

    def require_scope(self, scope: StorageScope) -> None:
        if scope != self.scope:
            raise DomainContractError("series record scope does not match")


@dataclass(frozen=True)
class BookRecord(SerializableRecord):
    book_id: DomainId | str
    project_id: DomainId | str
    series_id: DomainId | str | None
    volume_number: int | None
    title: str
    source_language: str
    target_market: str
    opening_state_ref: DomainId | str | None
    closing_state_ref: DomainId | str | None
    canon_version: int
    style_profile_version: int
    status: str
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "book_id", require_domain_id(self.book_id, DomainNamespace.BOOK, "book_id"))
        object.__setattr__(self, "project_id", require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"))
        object.__setattr__(self, "series_id", _optional_id(self.series_id, DomainNamespace.SERIES, "series_id"))
        object.__setattr__(self, "opening_state_ref", _optional_id(self.opening_state_ref, DomainNamespace.CONTEXT, "opening_state_ref"))
        object.__setattr__(self, "closing_state_ref", _optional_id(self.closing_state_ref, DomainNamespace.CONTEXT, "closing_state_ref"))
        if self.volume_number is not None:
            object.__setattr__(self, "volume_number", _positive_int(self.volume_number, "volume_number"))
        for name in ("title", "source_language", "target_market", "status", "created_at", "updated_at"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        for name in ("canon_version", "style_profile_version"):
            object.__setattr__(self, name, _positive_int(getattr(self, name), name))

    @property
    def project_scope(self) -> StorageScope:
        return StorageScope.project(str(self.project_id))

    def require_project_scope(self, scope: StorageScope) -> None:
        if scope != self.project_scope:
            raise DomainContractError("book record project scope does not match")


@dataclass(frozen=True)
class ActRecord(SerializableRecord):
    act_id: DomainId | str
    book_id: DomainId | str
    order: int
    name: str | None
    purpose: str
    opening_state: Any
    closing_state: Any
    main_conflict: str
    turning_point: str
    target_tension_start: int | float
    target_tension_end: int | float
    status: str
    version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "act_id", require_domain_id(self.act_id, DomainNamespace.ACT, "act_id"))
        object.__setattr__(self, "book_id", require_domain_id(self.book_id, DomainNamespace.BOOK, "book_id"))
        object.__setattr__(self, "order", _non_negative_int(self.order, "order"))
        object.__setattr__(self, "name", _optional_text(self.name, "name"))
        for name in ("purpose", "main_conflict", "turning_point", "status"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        for name in ("opening_state", "closing_state"):
            object.__setattr__(self, name, _ensure_json_compatible(getattr(self, name), name))
        object.__setattr__(self, "target_tension_start", _number(self.target_tension_start, "target_tension_start"))
        object.__setattr__(self, "target_tension_end", _number(self.target_tension_end, "target_tension_end"))
        object.__setattr__(self, "version", _positive_int(self.version, "version"))


@dataclass(frozen=True)
class SequenceRecord(SerializableRecord):
    sequence_id: DomainId | str
    act_id: DomainId | str
    order: int
    purpose: str
    main_goal: str
    main_conflict: str
    entry_state: Any
    exit_state: Any
    turning_point: str
    target_tension: int | float
    target_pace: int | float
    status: str
    version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "sequence_id", require_domain_id(self.sequence_id, DomainNamespace.SEQUENCE, "sequence_id"))
        object.__setattr__(self, "act_id", require_domain_id(self.act_id, DomainNamespace.ACT, "act_id"))
        object.__setattr__(self, "order", _non_negative_int(self.order, "order"))
        for name in ("purpose", "main_goal", "main_conflict", "turning_point", "status"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        for name in ("entry_state", "exit_state"):
            object.__setattr__(self, name, _ensure_json_compatible(getattr(self, name), name))
        object.__setattr__(self, "target_tension", _number(self.target_tension, "target_tension"))
        object.__setattr__(self, "target_pace", _number(self.target_pace, "target_pace"))
        object.__setattr__(self, "version", _positive_int(self.version, "version"))


@dataclass(frozen=True)
class ChapterRecord(SerializableRecord):
    chapter_id: DomainId | str
    project_id: DomainId | str
    book_id: DomainId | str
    sequence_id: DomainId | str | None
    order: int
    title: str | None
    purpose: str
    entry_state: Any
    exit_state: Any
    scene_ids: Iterable[DomainId | str]
    status: str
    current_version: int
    canon_status: str
    style_status: str
    quality_status: str
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "chapter_id", require_domain_id(self.chapter_id, DomainNamespace.CHAPTER, "chapter_id"))
        object.__setattr__(self, "project_id", require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"))
        object.__setattr__(self, "book_id", require_domain_id(self.book_id, DomainNamespace.BOOK, "book_id"))
        object.__setattr__(self, "sequence_id", _optional_id(self.sequence_id, DomainNamespace.SEQUENCE, "sequence_id"))
        object.__setattr__(self, "order", _non_negative_int(self.order, "order"))
        object.__setattr__(self, "title", _optional_text(self.title, "title"))
        for name in ("purpose", "status", "canon_status", "style_status", "quality_status", "created_at", "updated_at"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        for name in ("entry_state", "exit_state"):
            object.__setattr__(self, name, _ensure_json_compatible(getattr(self, name), name))
        object.__setattr__(self, "scene_ids", _id_list(self.scene_ids, DomainNamespace.SCENE, "scene_ids"))
        object.__setattr__(self, "current_version", _positive_int(self.current_version, "current_version"))

    @property
    def project_scope(self) -> StorageScope:
        return StorageScope.project(str(self.project_id))

    def require_project_scope(self, scope: StorageScope) -> None:
        if scope != self.project_scope:
            raise DomainContractError("chapter record project scope does not match")


@dataclass(frozen=True)
class SceneContract(SerializableRecord):
    scene_id: DomainId | str
    project_id: DomainId | str
    chapter_id: DomainId | str
    order: int
    narrative_order: int
    pov_character_id: DomainId | str
    participant_character_ids: Iterable[DomainId | str]
    location_id: DomainId | str | None
    route_id: DomainId | str | None
    time_start: str
    time_end: str
    purpose: str
    goal: str
    obstacle: str
    conflict: str
    stakes: str
    outcome: str
    state_change: str
    facts_required: Iterable[DomainId | str]
    facts_created: Iterable[DomainId | str]
    facts_revealed: Iterable[DomainId | str]
    must_include_facts: Iterable[DomainId | str]
    must_include_events: Iterable[DomainId | str]
    must_include_threads: Iterable[DomainId | str]
    must_include_character_states: Iterable[DomainId | str]
    threads_opened: Iterable[DomainId | str]
    threads_progressed: Iterable[DomainId | str]
    threads_closed: Iterable[DomainId | str]
    setups_created: Iterable[DomainId | str]
    payoffs_completed: Iterable[DomainId | str]
    reader_knowledge_added: Iterable[DomainId | str]
    target_tension: int | float
    target_pace: int | float
    status: str
    version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "scene_id", require_domain_id(self.scene_id, DomainNamespace.SCENE, "scene_id"))
        object.__setattr__(self, "project_id", require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"))
        object.__setattr__(self, "chapter_id", require_domain_id(self.chapter_id, DomainNamespace.CHAPTER, "chapter_id"))
        object.__setattr__(self, "order", _non_negative_int(self.order, "order"))
        object.__setattr__(self, "narrative_order", _non_negative_int(self.narrative_order, "narrative_order"))
        object.__setattr__(self, "pov_character_id", require_domain_id(self.pov_character_id, DomainNamespace.CHARACTER, "pov_character_id"))
        object.__setattr__(self, "participant_character_ids", _id_list(self.participant_character_ids, DomainNamespace.CHARACTER, "participant_character_ids"))
        object.__setattr__(self, "location_id", _optional_id(self.location_id, DomainNamespace.PLACE, "location_id"))
        object.__setattr__(self, "route_id", _optional_id(self.route_id, DomainNamespace.ROUTE, "route_id"))
        for name in (
            "time_start",
            "time_end",
            "purpose",
            "goal",
            "obstacle",
            "conflict",
            "stakes",
            "outcome",
            "state_change",
            "status",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        for name in ("facts_required", "facts_created", "facts_revealed", "must_include_facts"):
            object.__setattr__(self, name, _id_list(getattr(self, name), DomainNamespace.FACT, name))
        object.__setattr__(self, "must_include_events", _id_list(self.must_include_events, DomainNamespace.EVENT, "must_include_events"))
        object.__setattr__(self, "must_include_threads", _id_list(self.must_include_threads, DomainNamespace.THREAD, "must_include_threads"))
        object.__setattr__(self, "must_include_character_states", _id_list(self.must_include_character_states, DomainNamespace.CONTEXT, "must_include_character_states"))
        for name in ("threads_opened", "threads_progressed", "threads_closed"):
            object.__setattr__(self, name, _id_list(getattr(self, name), DomainNamespace.THREAD, name))
        object.__setattr__(self, "setups_created", _id_list(self.setups_created, DomainNamespace.SETUP, "setups_created"))
        object.__setattr__(self, "payoffs_completed", _id_list(self.payoffs_completed, DomainNamespace.SETUP, "payoffs_completed"))
        object.__setattr__(self, "reader_knowledge_added", _id_list(self.reader_knowledge_added, DomainNamespace.CONTEXT, "reader_knowledge_added"))
        object.__setattr__(self, "target_tension", _number(self.target_tension, "target_tension"))
        object.__setattr__(self, "target_pace", _number(self.target_pace, "target_pace"))
        object.__setattr__(self, "version", _positive_int(self.version, "version"))

    @property
    def project_scope(self) -> StorageScope:
        return StorageScope.project(str(self.project_id))

    def require_project_scope(self, scope: StorageScope) -> None:
        if scope != self.project_scope:
            raise DomainContractError("scene contract project scope does not match")


__all__ = [
    "DOMAIN_RECORD_SCHEMA_VERSION",
    "ActRecord",
    "BookRecord",
    "ChapterRecord",
    "DomainContractError",
    "DomainId",
    "DomainNamespace",
    "ProjectRecord",
    "SceneContract",
    "SequenceRecord",
    "SeriesRecord",
    "build_domain_id",
    "parse_domain_id",
    "require_domain_id",
]

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
    PAYOFF = "PAYOFF"
    KNOWLEDGE = "KNOWLEDGE"
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


def _any_domain_id(value: DomainId | str, field_name: str) -> DomainId:
    try:
        return value if isinstance(value, DomainId) else DomainId.parse(value)
    except DomainContractError as exc:
        raise DomainContractError(f"{field_name} must be a valid domain id") from exc


def _optional_any_domain_id(value: DomainId | str | None, field_name: str) -> DomainId | None:
    if value is None:
        return None
    return _any_domain_id(value, field_name)


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


def _confidence(value: Any, field_name: str = "confidence") -> int | float:
    score = _number(value, field_name)
    if score < 0 or score > 1:
        raise DomainContractError(f"{field_name} must be between 0 and 1")
    return score


def _bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise DomainContractError(f"{field_name} must be a boolean")
    return value


def _optional_time(value: Any, field_name: str) -> str | None:
    return _optional_text(value, field_name)


def _validate_temporal_range(valid_from: str | None, valid_to: str | None) -> None:
    if valid_from is not None and valid_to is not None and valid_to < valid_from:
        raise DomainContractError("valid_to must not be earlier than valid_from")


def _text_tuple(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or values is None:
        raise DomainContractError(f"{field_name} must be a list")
    return tuple(_required_text(value, field_name) for value in values)


def _id_list(
    values: Iterable[DomainId | str],
    namespace: DomainNamespace,
    field_name: str,
) -> tuple[DomainId, ...]:
    if isinstance(values, (str, bytes)) or values is None:
        raise DomainContractError(f"{field_name} must be a list")
    return tuple(require_domain_id(value, namespace, field_name) for value in values)


def _any_id_list(values: Iterable[DomainId | str], field_name: str) -> tuple[DomainId, ...]:
    if isinstance(values, (str, bytes)) or values is None:
        raise DomainContractError(f"{field_name} must be a list")
    return tuple(_any_domain_id(value, field_name) for value in values)


def _require_provenance(record_name: str, *sources: Any) -> None:
    for source in sources:
        if isinstance(source, tuple) and source:
            return
        if isinstance(source, DomainId):
            return
        if isinstance(source, str) and source:
            return
    raise DomainContractError(f"{record_name} provenance is required")


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
            if item.name not in {"frozen", "author_locked"} or getattr(self, item.name) is not None
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    def with_updates(self, **changes: Any):
        return replace(self, **changes)


def validate_optional_protection(record: Any) -> None:
    """None preserves legacy absence; it never authorizes canonical mutation."""
    for name in ("frozen", "author_locked"):
        value = getattr(record, name)
        if value is not None:
            _bool(value, name)


class ProjectScopedRecord(SerializableRecord):
    @property
    def record_id(self) -> DomainId:
        raise NotImplementedError

    @property
    def memory_record_type(self) -> str:
        raise NotImplementedError

    @property
    def scope(self) -> StorageScope:
        return StorageScope.project(str(self.project_id))

    def require_project_scope(self, scope: StorageScope) -> None:
        if scope != self.scope:
            raise DomainContractError("structured memory record project scope does not match")


class EdgeRelationType(str, Enum):
    KNOWS = "KNOWS"
    BELIEVES = "BELIEVES"
    RELATED_TO = "RELATED_TO"
    LOCATED_AT = "LOCATED_AT"
    PRESENT_IN = "PRESENT_IN"
    REVEALS = "REVEALS"
    REQUIRES = "REQUIRES"
    USES = "USES"
    OPENS = "OPENS"
    PROGRESSES = "PROGRESSES"
    CLOSES = "CLOSES"
    SETS_UP = "SETS_UP"
    PAYS_OFF = "PAYS_OFF"
    CAUSES = "CAUSES"
    MOTIVATES = "MOTIVATES"
    CONTRADICTS = "CONTRADICTS"
    DEPENDS_ON = "DEPENDS_ON"
    PART_OF = "PART_OF"


@dataclass(frozen=True)
class EdgeRecord(SerializableRecord):
    edge_id: str
    scope_type: str
    scope_id: str
    source_type: str
    source_id: DomainId | str
    relation_type: EdgeRelationType | str
    target_type: str
    target_id: DomainId | str
    valid_from: str | None
    valid_to: str | None
    confidence: int | float
    source_ref: str | None
    version: int

    def __post_init__(self) -> None:
        edge_id = _required_text(self.edge_id, "edge_id")
        if not _SAFE_ID_BODY.fullmatch(edge_id):
            raise DomainContractError("edge_id contains unsafe characters")
        scope = StorageScope(self.scope_type, self.scope_id)
        object.__setattr__(self, "scope_type", scope.scope_type.value)
        object.__setattr__(self, "scope_id", scope.scope_id)
        for side in ("source", "target"):
            node_id = _any_domain_id(getattr(self, f"{side}_id"), f"{side}_id")
            node_type = getattr(self, f"{side}_type")
            if node_type not in {node_id.namespace.name, node_id.namespace.value}:
                raise DomainContractError(f"{side}_type does not match domain id namespace")
            if (scope.scope_type.value == "PROJECT"
                    and node_id.namespace == DomainNamespace.PROJECT
                    and str(node_id) != scope.scope_id):
                raise DomainContractError("cross-project edge endpoint is forbidden")
            if (scope.scope_type.value == "SERIES"
                    and node_id.namespace == DomainNamespace.SERIES
                    and str(node_id) != scope.scope_id):
                raise DomainContractError("cross-series edge endpoint is forbidden")
            object.__setattr__(self, f"{side}_id", node_id)
            object.__setattr__(self, f"{side}_type", node_id.namespace.name)
        try:
            relation = EdgeRelationType(self.relation_type)
        except ValueError as exc:
            raise DomainContractError("unsupported edge relation_type") from exc
        object.__setattr__(self, "relation_type", relation)
        for name in ("valid_from", "valid_to"):
            object.__setattr__(self, name, _optional_time(getattr(self, name), name))
        _validate_temporal_range(self.valid_from, self.valid_to)
        confidence = _confidence(self.confidence)
        if confidence != confidence:
            raise DomainContractError("confidence must be finite")
        object.__setattr__(self, "source_ref", _optional_text(self.source_ref, "source_ref"))
        object.__setattr__(self, "version", _positive_int(self.version, "version"))

    @property
    def scope(self) -> StorageScope:
        # Both endpoints are local to this scope; IDs never trigger global lookup.
        return StorageScope(self.scope_type, self.scope_id)


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
        object.__setattr__(self, "payoffs_completed", _id_list(self.payoffs_completed, DomainNamespace.PAYOFF, "payoffs_completed"))
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


@dataclass(frozen=True)
class FactRecord(ProjectScopedRecord):
    fact_id: DomainId | str
    project_id: DomainId | str
    subject_id: DomainId | str
    predicate: str
    object_type: str
    object_id: DomainId | str | None
    object_value: Any
    reality_status: str
    verification_status: str
    confidence: int | float
    frozen: bool
    author_locked: bool
    valid_from: str | None
    valid_to: str | None
    established_event_id: DomainId | str | None
    established_scene_id: DomainId | str | None
    source_artifact_ref: str | None
    source_refs: Iterable[str]
    canon_version: int
    version: int
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "fact_id", require_domain_id(self.fact_id, DomainNamespace.FACT, "fact_id"))
        object.__setattr__(self, "project_id", require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"))
        object.__setattr__(self, "subject_id", _any_domain_id(self.subject_id, "subject_id"))
        object.__setattr__(self, "predicate", _required_text(self.predicate, "predicate"))
        object.__setattr__(self, "object_type", _required_text(self.object_type, "object_type"))
        object.__setattr__(self, "object_id", _optional_any_domain_id(self.object_id, "object_id"))
        if self.object_id is None and self.object_value is None:
            raise DomainContractError("object_id or object_value is required")
        object.__setattr__(self, "object_value", _ensure_json_compatible(self.object_value, "object_value"))
        for name in ("reality_status", "verification_status", "created_at", "updated_at"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(self, "frozen", _bool(self.frozen, "frozen"))
        object.__setattr__(self, "author_locked", _bool(self.author_locked, "author_locked"))
        object.__setattr__(self, "valid_from", _optional_time(self.valid_from, "valid_from"))
        object.__setattr__(self, "valid_to", _optional_time(self.valid_to, "valid_to"))
        _validate_temporal_range(self.valid_from, self.valid_to)
        object.__setattr__(self, "established_event_id", _optional_id(self.established_event_id, DomainNamespace.EVENT, "established_event_id"))
        object.__setattr__(self, "established_scene_id", _optional_id(self.established_scene_id, DomainNamespace.SCENE, "established_scene_id"))
        object.__setattr__(self, "source_artifact_ref", _optional_text(self.source_artifact_ref, "source_artifact_ref"))
        object.__setattr__(self, "source_refs", _text_tuple(self.source_refs, "source_refs"))
        _require_provenance("fact", self.established_scene_id, self.source_artifact_ref, self.source_refs)
        object.__setattr__(self, "canon_version", _positive_int(self.canon_version, "canon_version"))
        object.__setattr__(self, "version", _positive_int(self.version, "version"))

    @property
    def record_id(self) -> DomainId:
        return self.fact_id

    @property
    def memory_record_type(self) -> str:
        return "FACT"


@dataclass(frozen=True)
class CharacterState(ProjectScopedRecord):
    state_id: DomainId | str
    project_id: DomainId | str
    character_id: DomainId | str
    source_scene_id: DomainId | str | None
    source_event_id: DomainId | str | None
    source_artifact_ref: str | None
    state_payload: Any
    valid_from: str | None
    valid_to: str | None
    version: int
    created_at: str
    updated_at: str
    frozen: bool | None = None
    author_locked: bool | None = None

    def __post_init__(self) -> None:
        validate_optional_protection(self)
        object.__setattr__(self, "state_id", require_domain_id(self.state_id, DomainNamespace.CONTEXT, "state_id"))
        object.__setattr__(self, "project_id", require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"))
        object.__setattr__(self, "character_id", require_domain_id(self.character_id, DomainNamespace.CHARACTER, "character_id"))
        object.__setattr__(self, "source_scene_id", _optional_id(self.source_scene_id, DomainNamespace.SCENE, "source_scene_id"))
        object.__setattr__(self, "source_event_id", _optional_id(self.source_event_id, DomainNamespace.EVENT, "source_event_id"))
        object.__setattr__(self, "source_artifact_ref", _optional_text(self.source_artifact_ref, "source_artifact_ref"))
        _require_provenance("character state", self.source_scene_id, self.source_event_id, self.source_artifact_ref)
        object.__setattr__(self, "state_payload", _ensure_json_compatible(self.state_payload, "state_payload"))
        object.__setattr__(self, "valid_from", _optional_time(self.valid_from, "valid_from"))
        object.__setattr__(self, "valid_to", _optional_time(self.valid_to, "valid_to"))
        _validate_temporal_range(self.valid_from, self.valid_to)
        object.__setattr__(self, "version", _positive_int(self.version, "version"))
        for name in ("created_at", "updated_at"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))

    @property
    def record_id(self) -> DomainId:
        return self.state_id

    @property
    def memory_record_type(self) -> str:
        return "CHARACTER_STATE"


@dataclass(frozen=True)
class EventRecord(ProjectScopedRecord):
    event_id: DomainId | str
    project_id: DomainId | str
    event_type: str
    time_start: str
    time_end: str
    narrative_order: int
    location_id: DomainId | str | None
    participant_ids: Iterable[DomainId | str]
    description: str
    cause_refs: Iterable[DomainId | str]
    effect_refs: Iterable[DomainId | str]
    source_scene_id: DomainId | str | None
    source_artifact_ref: str | None
    canon_status: str
    version: int
    created_at: str
    updated_at: str
    frozen: bool | None = None
    author_locked: bool | None = None

    def __post_init__(self) -> None:
        validate_optional_protection(self)
        object.__setattr__(self, "event_id", require_domain_id(self.event_id, DomainNamespace.EVENT, "event_id"))
        object.__setattr__(self, "project_id", require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"))
        for name in ("event_type", "time_start", "time_end", "description", "canon_status", "created_at", "updated_at"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        if self.time_end < self.time_start:
            raise DomainContractError("time_end must not be earlier than time_start")
        object.__setattr__(self, "narrative_order", _non_negative_int(self.narrative_order, "narrative_order"))
        object.__setattr__(self, "location_id", _optional_id(self.location_id, DomainNamespace.PLACE, "location_id"))
        object.__setattr__(self, "participant_ids", _any_id_list(self.participant_ids, "participant_ids"))
        object.__setattr__(self, "cause_refs", _id_list(self.cause_refs, DomainNamespace.EVENT, "cause_refs"))
        object.__setattr__(self, "effect_refs", _id_list(self.effect_refs, DomainNamespace.EVENT, "effect_refs"))
        object.__setattr__(self, "source_scene_id", _optional_id(self.source_scene_id, DomainNamespace.SCENE, "source_scene_id"))
        object.__setattr__(self, "source_artifact_ref", _optional_text(self.source_artifact_ref, "source_artifact_ref"))
        _require_provenance("event", self.source_scene_id, self.source_artifact_ref)
        object.__setattr__(self, "version", _positive_int(self.version, "version"))

    @property
    def record_id(self) -> DomainId:
        return self.event_id

    @property
    def memory_record_type(self) -> str:
        return "EVENT"


@dataclass(frozen=True)
class KnowledgeEvent(ProjectScopedRecord):
    knowledge_event_id: DomainId | str
    project_id: DomainId | str
    character_id: DomainId | str
    fact_id: DomainId | str | None
    event_id: DomainId | str | None
    knowledge_status: str
    learned_at_scene_id: DomainId | str
    learned_at_event_id: DomainId | str | None
    learned_from_character_id: DomainId | str | None
    source_ref: str | None
    valid_from: str | None
    valid_to: str | None
    confidence: int | float
    version: int
    created_at: str
    updated_at: str
    frozen: bool | None = None
    author_locked: bool | None = None

    def __post_init__(self) -> None:
        validate_optional_protection(self)
        object.__setattr__(self, "knowledge_event_id", require_domain_id(self.knowledge_event_id, DomainNamespace.KNOWLEDGE, "knowledge_event_id"))
        object.__setattr__(self, "project_id", require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"))
        object.__setattr__(self, "character_id", require_domain_id(self.character_id, DomainNamespace.CHARACTER, "character_id"))
        object.__setattr__(self, "fact_id", _optional_id(self.fact_id, DomainNamespace.FACT, "fact_id"))
        object.__setattr__(self, "event_id", _optional_id(self.event_id, DomainNamespace.EVENT, "event_id"))
        if self.fact_id is None and self.event_id is None:
            raise DomainContractError("fact_id or event_id is required")
        object.__setattr__(self, "knowledge_status", _required_text(self.knowledge_status, "knowledge_status"))
        object.__setattr__(self, "learned_at_scene_id", require_domain_id(self.learned_at_scene_id, DomainNamespace.SCENE, "learned_at_scene_id"))
        object.__setattr__(self, "learned_at_event_id", _optional_id(self.learned_at_event_id, DomainNamespace.EVENT, "learned_at_event_id"))
        object.__setattr__(self, "learned_from_character_id", _optional_id(self.learned_from_character_id, DomainNamespace.CHARACTER, "learned_from_character_id"))
        object.__setattr__(self, "source_ref", _optional_text(self.source_ref, "source_ref"))
        _require_provenance("knowledge event", self.learned_at_scene_id, self.source_ref)
        object.__setattr__(self, "valid_from", _optional_time(self.valid_from, "valid_from"))
        object.__setattr__(self, "valid_to", _optional_time(self.valid_to, "valid_to"))
        _validate_temporal_range(self.valid_from, self.valid_to)
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(self, "version", _positive_int(self.version, "version"))
        for name in ("created_at", "updated_at"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))

    @property
    def record_id(self) -> DomainId:
        return self.knowledge_event_id

    @property
    def memory_record_type(self) -> str:
        return "KNOWLEDGE_EVENT"


@dataclass(frozen=True)
class ThreadRecord(ProjectScopedRecord):
    thread_id: DomainId | str
    project_id: DomainId | str
    name: str
    description: str
    importance: int | float
    opened_scene_id: DomainId | str
    closed_scene_id: DomainId | str | None
    status: str
    payoff_required: bool
    target_payoff: str | None
    actual_payoff_ref: DomainId | str | None
    deliberately_left_reason: str | None
    version: int
    created_at: str
    updated_at: str
    frozen: bool | None = None
    author_locked: bool | None = None

    def __post_init__(self) -> None:
        validate_optional_protection(self)
        object.__setattr__(self, "thread_id", require_domain_id(self.thread_id, DomainNamespace.THREAD, "thread_id"))
        object.__setattr__(self, "project_id", require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"))
        for name in ("name", "description", "status", "created_at", "updated_at"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "importance", _number(self.importance, "importance"))
        object.__setattr__(self, "opened_scene_id", require_domain_id(self.opened_scene_id, DomainNamespace.SCENE, "opened_scene_id"))
        object.__setattr__(self, "closed_scene_id", _optional_id(self.closed_scene_id, DomainNamespace.SCENE, "closed_scene_id"))
        object.__setattr__(self, "payoff_required", _bool(self.payoff_required, "payoff_required"))
        object.__setattr__(self, "target_payoff", _optional_text(self.target_payoff, "target_payoff"))
        object.__setattr__(self, "actual_payoff_ref", _optional_id(self.actual_payoff_ref, DomainNamespace.PAYOFF, "actual_payoff_ref"))
        object.__setattr__(self, "deliberately_left_reason", _optional_text(self.deliberately_left_reason, "deliberately_left_reason"))
        if self.status == "DELIBERATELY_LEFT" and self.deliberately_left_reason is None:
            raise DomainContractError("deliberately_left_reason is required")
        object.__setattr__(self, "version", _positive_int(self.version, "version"))

    @property
    def record_id(self) -> DomainId:
        return self.thread_id

    @property
    def memory_record_type(self) -> str:
        return "THREAD"


@dataclass(frozen=True)
class SetupRecord(ProjectScopedRecord):
    setup_id: DomainId | str
    project_id: DomainId | str
    created_scene_id: DomainId | str
    description: str
    importance: int | float
    expected_payoff: str
    target_range: str
    actual_payoff_scene_id: DomainId | str | None
    status: str
    version: int
    created_at: str
    updated_at: str
    frozen: bool | None = None
    author_locked: bool | None = None

    def __post_init__(self) -> None:
        validate_optional_protection(self)
        object.__setattr__(self, "setup_id", require_domain_id(self.setup_id, DomainNamespace.SETUP, "setup_id"))
        object.__setattr__(self, "project_id", require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"))
        object.__setattr__(self, "created_scene_id", require_domain_id(self.created_scene_id, DomainNamespace.SCENE, "created_scene_id"))
        for name in ("description", "expected_payoff", "target_range", "status", "created_at", "updated_at"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "importance", _number(self.importance, "importance"))
        object.__setattr__(self, "actual_payoff_scene_id", _optional_id(self.actual_payoff_scene_id, DomainNamespace.SCENE, "actual_payoff_scene_id"))
        object.__setattr__(self, "version", _positive_int(self.version, "version"))

    @property
    def record_id(self) -> DomainId:
        return self.setup_id

    @property
    def memory_record_type(self) -> str:
        return "SETUP"


@dataclass(frozen=True)
class PayoffRecord(ProjectScopedRecord):
    payoff_id: DomainId | str
    project_id: DomainId | str
    setup_id: DomainId | str
    completed_scene_id: DomainId | str
    description: str
    source_artifact_ref: str | None
    status: str
    version: int
    created_at: str
    updated_at: str
    frozen: bool | None = None
    author_locked: bool | None = None

    def __post_init__(self) -> None:
        validate_optional_protection(self)
        object.__setattr__(self, "payoff_id", require_domain_id(self.payoff_id, DomainNamespace.PAYOFF, "payoff_id"))
        object.__setattr__(self, "project_id", require_domain_id(self.project_id, DomainNamespace.PROJECT, "project_id"))
        object.__setattr__(self, "setup_id", require_domain_id(self.setup_id, DomainNamespace.SETUP, "setup_id"))
        object.__setattr__(self, "completed_scene_id", require_domain_id(self.completed_scene_id, DomainNamespace.SCENE, "completed_scene_id"))
        for name in ("description", "status", "created_at", "updated_at"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "source_artifact_ref", _optional_text(self.source_artifact_ref, "source_artifact_ref"))
        _require_provenance("payoff", self.completed_scene_id, self.source_artifact_ref)
        object.__setattr__(self, "version", _positive_int(self.version, "version"))

    @property
    def record_id(self) -> DomainId:
        return self.payoff_id

    @property
    def memory_record_type(self) -> str:
        return "PAYOFF"


__all__ = [
    "DOMAIN_RECORD_SCHEMA_VERSION",
    "ActRecord",
    "BookRecord",
    "CharacterState",
    "ChapterRecord",
    "DomainContractError",
    "DomainId",
    "DomainNamespace",
    "EdgeRecord",
    "EdgeRelationType",
    "EventRecord",
    "FactRecord",
    "KnowledgeEvent",
    "PayoffRecord",
    "ProjectRecord",
    "SceneContract",
    "SequenceRecord",
    "SeriesRecord",
    "SetupRecord",
    "ThreadRecord",
    "build_domain_id",
    "parse_domain_id",
    "require_domain_id",
]

"""Deterministic, project-scoped context selection for P20 model calls."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from app.p20_core.domain_records import (
    DomainContractError,
    DomainId,
    DomainNamespace,
    SceneContract,
)
from app.p20_core.project_graph import DependencyTraversalPolicy, GraphNodeRef
from app.p20_core.project_repository import (
    ProjectRepository,
    ProjectStorageError,
    SeriesAccessContext,
    SeriesRepository,
    StorageScope,
)


CONTEXT_PACKAGE_SCHEMA_VERSION = 1
RANKING_DIMENSIONS = (
    "graph_proximity",
    "task_relevance",
    "narrative_recency",
    "confidence",
    "semantic_similarity",
    "story_importance",
)


class ContextBuilderError(ValueError):
    pass


class ContextOverflowError(ContextBuilderError):
    def __init__(self, *, available_tokens: int, mandatory_tokens: int) -> None:
        self.available_tokens = available_tokens
        self.mandatory_tokens = mandatory_tokens
        self.status = "ESCALATION_REQUIRED"
        super().__init__(
            "mandatory context exceeds available token budget; escalation required"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "available_tokens": self.available_tokens,
            "mandatory_tokens": self.mandatory_tokens,
        }


class MissingMandatoryContextError(ContextBuilderError):
    pass


class ContextRole(str, Enum):
    WRITER = "WRITER"
    CRITIC = "CRITIC"
    CONTINUITY = "CONTINUITY"
    CANON = "CANON"


class ContextLayer(str, Enum):
    TASK = "TASK"
    CANON = "CANON"
    BOOK_BIBLE = "BOOK_BIBLE"
    MUST_INCLUDE = "MUST_INCLUDE"
    CONFLICT = "CONFLICT"
    STRUCTURED_MEMORY = "STRUCTURED_MEMORY"
    CHARACTER_KNOWLEDGE = "CHARACTER_KNOWLEDGE"
    TIMELINE = "TIMELINE"
    THREAD_SETUP_PAYOFF = "THREAD_SETUP_PAYOFF"
    PLACE_ROUTE = "PLACE_ROUTE"
    GRAPH = "GRAPH"
    SERIES_MEMORY = "SERIES_MEMORY"
    PREVIOUS_CONTEXT = "PREVIOUS_CONTEXT"
    STRUCTURED_RANKED = "STRUCTURED_RANKED"
    SEMANTIC = "SEMANTIC"


STRUCTURED_FIRST_LAYER_ORDER = tuple(ContextLayer)


class RepresentationType(str, Enum):
    FULL = "FULL"
    STRUCTURED = "STRUCTURED"
    COMPRESSED = "COMPRESSED"
    SUMMARY = "SUMMARY"
    WARNING = "WARNING"


class OverflowBehavior(str, Enum):
    ESCALATE = "ESCALATE"


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContextBuilderError(f"{field_name} is required")
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _domain_id(
    value: Any,
    field_name: str,
    namespace: DomainNamespace | None = None,
) -> str:
    try:
        parsed = DomainId.parse(_required_text(value, field_name))
        if namespace is not None:
            parsed.require_namespace(namespace, field_name)
    except DomainContractError as exc:
        raise ContextBuilderError(str(exc)) from exc
    return str(parsed)


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContextBuilderError(f"{field_name} must be a positive integer")
    return value


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContextBuilderError(f"{field_name} must be a non-negative integer")
    return value


def _optional_unit_score(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContextBuilderError(f"{field_name} must be a number")
    score = float(value)
    if score < 0 or score > 1:
        raise ContextBuilderError(f"{field_name} must be between 0 and 1")
    return score


def _coerce_enum(enum_type: type[Enum], value: Any, field_name: str) -> Any:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise ContextBuilderError(f"unsupported {field_name}: {value!r}") from exc


def _serialize(value: Any) -> Any:
    if isinstance(value, DomainId):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _serialize(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_serialize(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _serialize(to_dict())
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(_serialize(value), sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    serialized = value if isinstance(value, str) else _canonical_json(value)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _context_package_semantic_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    role = _coerce_enum(ContextRole, values["role"], "role")
    return {
        "schema_version": CONTEXT_PACKAGE_SCHEMA_VERSION,
        "project_id": values["project_id"],
        "book_id": values["book_id"],
        "series_id": values["series_id"],
        "role": role.value,
        "mode": values["mode"],
        "context_policy_id": values["context_policy_id"],
        "context_policy_version": values["context_policy_version"],
        "context_profile_id": values["context_profile_id"],
        "context_profile_version": values["context_profile_version"],
        "effective_model": values["effective_model"],
        "tokenizer_id": values["tokenizer_id"],
        "tokenizer_version": values["tokenizer_version"],
        "context_window": values["context_window"],
        "reserved_output_tokens": values["reserved_output_tokens"],
        "reserved_system_tokens": values["reserved_system_tokens"],
        "available_context_tokens": values["available_context_tokens"],
        "canon_version": values["canon_version"],
        "book_bible_version": values["book_bible_version"],
        "style_version": values["style_version"],
        "memory_snapshot_id": values["memory_snapshot_id"],
        "graph_version": values["graph_version"],
        "included_items": [item.to_dict() for item in values["included_items"]],
    }


class _Serializable:
    def to_dict(self) -> dict[str, Any]:
        return {field.name: _serialize(getattr(self, field.name)) for field in fields(self)}

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


@runtime_checkable
class TokenCounter(Protocol):
    """Exact counter supplied by model/tokenizer integration."""

    @property
    def tokenizer_id(self) -> str: ...

    @property
    def tokenizer_version(self) -> str | None: ...

    def count_tokens(self, text: str, *, model: str) -> int: ...


@dataclass(frozen=True)
class SemanticProvenance(_Serializable):
    embedding_model: str
    index_version: str
    source_version: str | int
    source_hash: str

    def __post_init__(self) -> None:
        for name in ("embedding_model", "index_version", "source_hash"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        if isinstance(self.source_version, bool) or not isinstance(
            self.source_version, (str, int)
        ):
            raise ContextBuilderError("source_version must be text or integer")


@dataclass(frozen=True)
class ContextProfile(_Serializable):
    profile_id: str
    version: int
    role: ContextRole | str
    allowed_layers: Iterable[ContextLayer | str]
    required_layers: Iterable[ContextLayer | str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _required_text(self.profile_id, "profile_id"))
        object.__setattr__(self, "version", _positive_int(self.version, "version"))
        object.__setattr__(self, "role", _coerce_enum(ContextRole, self.role, "role"))
        if isinstance(self.allowed_layers, (str, bytes)):
            raise ContextBuilderError("allowed_layers must be a collection")
        allowed = tuple(_coerce_enum(ContextLayer, layer, "layer") for layer in self.allowed_layers)
        if not allowed or len(set(allowed)) != len(allowed):
            raise ContextBuilderError("allowed_layers must be non-empty and unique")
        required = tuple(
            _coerce_enum(ContextLayer, layer, "required layer")
            for layer in self.required_layers
        )
        if len(set(required)) != len(required) or not set(required).issubset(allowed):
            raise ContextBuilderError("required_layers must be unique allowed layers")
        object.__setattr__(self, "allowed_layers", allowed)
        object.__setattr__(self, "required_layers", required)


def default_context_profiles() -> dict[ContextRole, ContextProfile]:
    all_layers = STRUCTURED_FIRST_LAYER_ORDER
    return {
        ContextRole.WRITER: ContextProfile(
            "P20_WRITER", 1, ContextRole.WRITER, all_layers,
            (ContextLayer.TASK, ContextLayer.CANON, ContextLayer.BOOK_BIBLE),
        ),
        ContextRole.CRITIC: ContextProfile(
            "P20_CRITIC", 1, ContextRole.CRITIC,
            (
                ContextLayer.TASK,
                ContextLayer.CANON,
                ContextLayer.BOOK_BIBLE,
                ContextLayer.MUST_INCLUDE,
                ContextLayer.CONFLICT,
                ContextLayer.STRUCTURED_MEMORY,
                ContextLayer.GRAPH,
                ContextLayer.PREVIOUS_CONTEXT,
            ),
            (ContextLayer.TASK,),
        ),
        ContextRole.CONTINUITY: ContextProfile(
            "P20_CONTINUITY", 1, ContextRole.CONTINUITY, all_layers,
            (ContextLayer.TASK, ContextLayer.CANON),
        ),
        ContextRole.CANON: ContextProfile(
            "P20_CANON", 1, ContextRole.CANON,
            tuple(layer for layer in all_layers if layer != ContextLayer.SEMANTIC),
            (ContextLayer.TASK, ContextLayer.CANON, ContextLayer.BOOK_BIBLE),
        ),
    }


@dataclass(frozen=True)
class ContextPolicy(_Serializable):
    policy_id: str
    version: int
    profile_id: str
    context_window: int
    reserved_output_tokens: int
    reserved_system_tokens: int
    technical_overhead_tokens: int
    layer_priorities: Iterable[ContextLayer | str]
    ranking_weights: Mapping[str, int | float]
    graph_max_depth: int
    graph_max_edges: int
    overflow_behavior: OverflowBehavior | str
    allowed_representations: Iterable[RepresentationType | str]
    top_rejected_limit: int = 5
    semantic_limit: int = 10

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _required_text(self.policy_id, "policy_id"))
        object.__setattr__(self, "profile_id", _required_text(self.profile_id, "profile_id"))
        object.__setattr__(self, "version", _positive_int(self.version, "version"))
        object.__setattr__(self, "context_window", _positive_int(self.context_window, "context_window"))
        for name in (
            "reserved_output_tokens",
            "reserved_system_tokens",
            "technical_overhead_tokens",
            "graph_max_depth",
            "top_rejected_limit",
            "semantic_limit",
        ):
            object.__setattr__(self, name, _non_negative_int(getattr(self, name), name))
        object.__setattr__(self, "graph_max_edges", _positive_int(self.graph_max_edges, "graph_max_edges"))
        priorities = tuple(
            _coerce_enum(ContextLayer, layer, "layer priority")
            for layer in self.layer_priorities
        )
        if len(priorities) != len(ContextLayer) or set(priorities) != set(ContextLayer):
            raise ContextBuilderError("layer_priorities must contain every layer exactly once")
        object.__setattr__(self, "layer_priorities", priorities)
        unknown = set(self.ranking_weights) - set(RANKING_DIMENSIONS)
        if unknown:
            raise ContextBuilderError(f"unsupported ranking weights: {sorted(unknown)}")
        weights = {}
        for name in RANKING_DIMENSIONS:
            value = self.ranking_weights.get(name, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ContextBuilderError(f"ranking weight {name} must be non-negative")
            weights[name] = float(value)
        object.__setattr__(self, "ranking_weights", MappingProxyType(weights))
        object.__setattr__(self, "overflow_behavior", _coerce_enum(
            OverflowBehavior, self.overflow_behavior, "overflow_behavior"
        ))
        representations = tuple(
            _coerce_enum(RepresentationType, item, "representation_type")
            for item in self.allowed_representations
        )
        if not representations or len(set(representations)) != len(representations):
            raise ContextBuilderError("allowed_representations must be non-empty and unique")
        object.__setattr__(self, "allowed_representations", representations)
        if self.available_context_tokens < 0:
            raise ContextBuilderError("reserved tokens exceed context window")

    @property
    def available_context_tokens(self) -> int:
        return (
            self.context_window
            - self.reserved_output_tokens
            - self.reserved_system_tokens
            - self.technical_overhead_tokens
        )


@dataclass(frozen=True)
class ContextCandidate:
    project_id: str
    entity_type: str
    entity_id: str
    layer: ContextLayer | str
    reason: str
    representations: Mapping[RepresentationType | str, str]
    source_version: str | int
    source_ref: str | None = None
    mandatory: bool = False
    graph_proximity: int | float | None = None
    task_relevance: int | float | None = None
    narrative_recency: int | float | None = None
    confidence: int | float | None = None
    semantic_similarity: int | float | None = None
    story_importance: int | float | None = None
    narrative_order: int | None = None
    semantic_provenance: SemanticProvenance | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _domain_id(
            self.project_id, "project_id", DomainNamespace.PROJECT
        ))
        object.__setattr__(self, "entity_id", _domain_id(self.entity_id, "entity_id"))
        for name in ("entity_type", "reason"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "source_ref", _optional_text(self.source_ref, "source_ref"))
        object.__setattr__(self, "layer", _coerce_enum(ContextLayer, self.layer, "layer"))
        if not isinstance(self.mandatory, bool):
            raise ContextBuilderError("mandatory must be boolean")
        if isinstance(self.source_version, bool) or not isinstance(
            self.source_version, (str, int)
        ):
            raise ContextBuilderError("source_version must be text or integer")
        if not isinstance(self.representations, Mapping) or not self.representations:
            raise ContextBuilderError("representations must be a non-empty mapping")
        representations = {}
        for key, value in self.representations.items():
            representation = _coerce_enum(RepresentationType, key, "representation_type")
            representations[representation] = _required_text(value, "representation content")
        object.__setattr__(self, "representations", MappingProxyType(representations))
        for name in RANKING_DIMENSIONS:
            object.__setattr__(self, name, _optional_unit_score(getattr(self, name), name))
        if self.narrative_order is not None:
            object.__setattr__(self, "narrative_order", _non_negative_int(
                self.narrative_order, "narrative_order"
            ))
        if self.semantic_provenance is not None and not isinstance(
            self.semantic_provenance, SemanticProvenance
        ):
            raise ContextBuilderError("semantic_provenance must be SemanticProvenance")

    @property
    def key(self) -> tuple[str, str]:
        return self.entity_type, self.entity_id


@runtime_checkable
class SemanticRetrievalProvider(Protocol):
    @property
    def embedding_model(self) -> str: ...

    @property
    def index_version(self) -> str: ...

    def retrieve(
        self,
        query: str,
        *,
        project_id: str,
        limit: int,
    ) -> Iterable[ContextCandidate]: ...


@dataclass(frozen=True)
class ContextItem(_Serializable):
    entity_type: str
    entity_id: str
    layer: ContextLayer | str
    mandatory: bool
    reason: str
    score: float | None
    score_breakdown: Mapping[str, float] | None
    representation_type: RepresentationType | str
    token_count: int
    source_version: str | int
    content_hash: str
    content: str
    source_ref: str | None = None
    semantic_provenance: SemanticProvenance | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_id", _domain_id(self.entity_id, "entity_id"))
        for name in ("entity_type", "reason", "content_hash", "content"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "source_ref", _optional_text(self.source_ref, "source_ref"))
        object.__setattr__(self, "layer", _coerce_enum(ContextLayer, self.layer, "layer"))
        object.__setattr__(self, "representation_type", _coerce_enum(
            RepresentationType, self.representation_type, "representation_type"
        ))
        if not isinstance(self.mandatory, bool):
            raise ContextBuilderError("mandatory must be boolean")
        object.__setattr__(self, "token_count", _non_negative_int(self.token_count, "token_count"))
        if self.score is not None:
            object.__setattr__(self, "score", float(self.score))
        if self.score_breakdown is not None:
            breakdown = {str(key): float(value) for key, value in self.score_breakdown.items()}
            object.__setattr__(self, "score_breakdown", MappingProxyType(breakdown))
        if self.content_hash != _sha256(self.content):
            raise ContextBuilderError("context item content_hash does not match content")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ContextItem:
        data = dict(payload)
        provenance = data.get("semantic_provenance")
        if provenance is not None:
            data["semantic_provenance"] = SemanticProvenance(**provenance)
        return cls(**data)


@dataclass(frozen=True)
class SelectionSummary(_Serializable):
    candidate_count: int
    selected_count: int
    excluded_count: int
    cutoff: float | None
    top_rejected: Iterable[Mapping[str, Any]]
    ranking_statistics: Mapping[str, float | int | None]
    semantic_retrieval_used: bool

    def __post_init__(self) -> None:
        for name in ("candidate_count", "selected_count", "excluded_count"):
            object.__setattr__(self, name, _non_negative_int(getattr(self, name), name))
        if self.selected_count + self.excluded_count != self.candidate_count:
            raise ContextBuilderError("selection summary counts do not balance")
        rejected = tuple(MappingProxyType(dict(item)) for item in self.top_rejected)
        object.__setattr__(self, "top_rejected", rejected)
        object.__setattr__(self, "ranking_statistics", MappingProxyType(
            dict(self.ranking_statistics)
        ))
        if not isinstance(self.semantic_retrieval_used, bool):
            raise ContextBuilderError("semantic_retrieval_used must be boolean")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SelectionSummary:
        return cls(**dict(payload))


@dataclass(frozen=True)
class ContextPackage(_Serializable):
    context_package_id: str
    project_id: str
    book_id: str
    series_id: str | None
    run_id: str
    step_id: str
    role: ContextRole | str
    mode: str
    context_policy_id: str
    context_policy_version: int
    context_profile_id: str
    context_profile_version: int
    effective_model: str
    tokenizer_id: str
    tokenizer_version: str | None
    context_window: int
    reserved_output_tokens: int
    reserved_system_tokens: int
    available_context_tokens: int
    canon_version: str | int
    book_bible_version: str | int
    style_version: str | int | None
    memory_snapshot_id: str | None
    graph_version: str | int | None
    included_items: Iterable[ContextItem]
    selection_summary: SelectionSummary
    total_tokens: int
    context_hash: str
    created_at: str

    def __post_init__(self) -> None:
        package_id = DomainId.parse(self.context_package_id)
        if package_id.namespace.value != "CONTEXT":
            raise ContextBuilderError("context_package_id must use CONTEXT- namespace")
        object.__setattr__(self, "project_id", _domain_id(
            self.project_id, "project_id", DomainNamespace.PROJECT
        ))
        object.__setattr__(self, "book_id", _domain_id(
            self.book_id, "book_id", DomainNamespace.BOOK
        ))
        if self.series_id is not None:
            object.__setattr__(self, "series_id", _domain_id(
                self.series_id, "series_id", DomainNamespace.SERIES
            ))
        for name in (
            "run_id", "step_id", "mode",
            "context_policy_id", "context_profile_id", "effective_model",
            "tokenizer_id", "context_hash", "created_at",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "tokenizer_version", _optional_text(
            self.tokenizer_version, "tokenizer_version"
        ))
        object.__setattr__(self, "memory_snapshot_id", _optional_text(
            self.memory_snapshot_id, "memory_snapshot_id"
        ))
        object.__setattr__(self, "role", _coerce_enum(ContextRole, self.role, "role"))
        for name in (
            "context_policy_version", "context_profile_version", "context_window",
            "canon_version", "book_bible_version",
        ):
            value = getattr(self, name)
            if name in {"canon_version", "book_bible_version"}:
                if isinstance(value, bool) or not isinstance(value, (str, int)):
                    raise ContextBuilderError(f"{name} must be text or integer")
            else:
                object.__setattr__(self, name, _positive_int(value, name))
        for name in (
            "reserved_output_tokens", "reserved_system_tokens",
            "available_context_tokens", "total_tokens",
        ):
            object.__setattr__(self, name, _non_negative_int(getattr(self, name), name))
        items = tuple(self.included_items)
        if any(not isinstance(item, ContextItem) for item in items):
            raise ContextBuilderError("included_items must contain ContextItem values")
        object.__setattr__(self, "included_items", items)
        if not isinstance(self.selection_summary, SelectionSummary):
            raise ContextBuilderError("selection_summary must be SelectionSummary")
        if sum(item.token_count for item in items) != self.total_tokens:
            raise ContextBuilderError("total_tokens does not match included items")
        if self.total_tokens > self.available_context_tokens:
            raise ContextBuilderError("context package exceeds available token budget")
        if self.context_hash != self.compute_context_hash():
            raise ContextBuilderError("context_hash does not match semantic package content")

    def semantic_payload(self) -> dict[str, Any]:
        return _context_package_semantic_payload(vars(self))

    def compute_context_hash(self) -> str:
        return _sha256(self.semantic_payload())

    @classmethod
    def create(cls, **values: Any) -> ContextPackage:
        values["context_hash"] = _sha256(_context_package_semantic_payload(values))
        return cls(**values)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ContextPackage:
        data = dict(payload)
        data["included_items"] = tuple(
            ContextItem.from_dict(item) for item in data["included_items"]
        )
        data["selection_summary"] = SelectionSummary.from_dict(data["selection_summary"])
        return cls(**data)


@dataclass(frozen=True)
class ContextBuildRequest:
    context_package_id: str
    operation_id: str
    project_id: str
    book_id: str
    series_id: str | None
    run_id: str
    step_id: str
    role: ContextRole | str
    mode: str
    effective_model: str
    canon_version: str | int
    book_bible_version: str | int
    style_version: str | int | None
    memory_snapshot_id: str | None
    graph_version: str | int | None
    created_at: str
    direct_candidates: Iterable[ContextCandidate] = ()
    scene_contract: SceneContract | None = None
    graph_starts: Iterable[GraphNodeRef] = ()
    semantic_query: str | None = None

    def __post_init__(self) -> None:
        package_id = DomainId.parse(self.context_package_id)
        if package_id.namespace.value != "CONTEXT":
            raise ContextBuilderError("context_package_id must use CONTEXT- namespace")
        object.__setattr__(self, "project_id", _domain_id(
            self.project_id, "project_id", DomainNamespace.PROJECT
        ))
        object.__setattr__(self, "book_id", _domain_id(
            self.book_id, "book_id", DomainNamespace.BOOK
        ))
        if self.series_id is not None:
            object.__setattr__(self, "series_id", _domain_id(
                self.series_id, "series_id", DomainNamespace.SERIES
            ))
        for name in (
            "operation_id", "run_id", "step_id",
            "mode", "effective_model", "created_at",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "memory_snapshot_id", _optional_text(
            self.memory_snapshot_id, "memory_snapshot_id"
        ))
        object.__setattr__(self, "semantic_query", _optional_text(
            self.semantic_query, "semantic_query"
        ))
        object.__setattr__(self, "role", _coerce_enum(ContextRole, self.role, "role"))
        candidates = tuple(self.direct_candidates)
        if any(not isinstance(item, ContextCandidate) for item in candidates):
            raise ContextBuilderError("direct_candidates must contain ContextCandidate values")
        object.__setattr__(self, "direct_candidates", candidates)
        graph_starts = tuple(self.graph_starts)
        if any(not isinstance(item, GraphNodeRef) for item in graph_starts):
            raise ContextBuilderError("graph_starts must contain GraphNodeRef values")
        object.__setattr__(self, "graph_starts", graph_starts)
        if self.scene_contract is not None and not isinstance(self.scene_contract, SceneContract):
            raise ContextBuilderError("scene_contract must be SceneContract")


def _memory_layer(record_type: str) -> ContextLayer:
    if record_type in {"CHARACTER_STATE", "KNOWLEDGE_EVENT", "RELATIONSHIP_CHANGE"}:
        return ContextLayer.CHARACTER_KNOWLEDGE
    if record_type == "EVENT":
        return ContextLayer.TIMELINE
    if record_type in {"THREAD", "SETUP", "PAYOFF"}:
        return ContextLayer.THREAD_SETUP_PAYOFF
    if record_type in {"PLACE", "ROUTE"}:
        return ContextLayer.PLACE_ROUTE
    return ContextLayer.STRUCTURED_MEMORY


class ContextBuilder:
    """Build and persist one auditable ContextPackage for a P20 operation."""

    def __init__(
        self,
        repository: ProjectRepository,
        token_counter: TokenCounter,
        *,
        series_repository: SeriesRepository | None = None,
        semantic_provider: SemanticRetrievalProvider | None = None,
    ) -> None:
        if not isinstance(repository, ProjectRepository):
            raise ContextBuilderError("repository must be ProjectRepository")
        if not callable(getattr(token_counter, "count_tokens", None)):
            raise ContextBuilderError("token_counter must implement TokenCounter")
        self.repository = repository
        self.token_counter = token_counter
        self.series_repository = series_repository
        self.semantic_provider = semantic_provider

    @staticmethod
    def _calculate_budget(policy: ContextPolicy) -> int:
        return policy.available_context_tokens

    def reuse_for_retry(self, *, project_id: str, operation_id: str) -> ContextPackage:
        if project_id != self.repository.context.project_id:
            raise ContextBuilderError("retry project scope does not match repository")
        package = self.repository.get_context_package_for_operation(operation_id)
        if package is None:
            raise ContextBuilderError("no persisted ContextPackage exists for technical retry")
        return package

    def build(
        self,
        request: ContextBuildRequest,
        policy: ContextPolicy,
        profile: ContextProfile,
        *,
        series_access: SeriesAccessContext | None = None,
    ) -> ContextPackage:
        if not isinstance(request, ContextBuildRequest):
            raise ContextBuilderError("request must be ContextBuildRequest")
        if not isinstance(policy, ContextPolicy) or not isinstance(profile, ContextProfile):
            raise ContextBuilderError("policy and profile contracts are required")
        self._validate_scope_and_profile(request, policy, profile, series_access)

        replay = self.repository.get_context_package_for_operation(request.operation_id)
        if replay is not None:
            return replay

        # This value is fixed before any repository, graph, series, or semantic retrieval.
        available_tokens = self._calculate_budget(policy)
        candidates, semantic_used = self._collect_candidates(
            request, policy, profile, series_access
        )
        items, summary = self._select(
            candidates,
            request=request,
            policy=policy,
            profile=profile,
            available_tokens=available_tokens,
            semantic_used=semantic_used,
        )
        package = ContextPackage.create(
            context_package_id=request.context_package_id,
            project_id=request.project_id,
            book_id=request.book_id,
            series_id=request.series_id,
            run_id=request.run_id,
            step_id=request.step_id,
            role=request.role,
            mode=request.mode,
            context_policy_id=policy.policy_id,
            context_policy_version=policy.version,
            context_profile_id=profile.profile_id,
            context_profile_version=profile.version,
            effective_model=request.effective_model,
            tokenizer_id=_required_text(self.token_counter.tokenizer_id, "tokenizer_id"),
            tokenizer_version=_optional_text(
                self.token_counter.tokenizer_version, "tokenizer_version"
            ),
            context_window=policy.context_window,
            reserved_output_tokens=policy.reserved_output_tokens,
            reserved_system_tokens=policy.reserved_system_tokens,
            available_context_tokens=available_tokens,
            canon_version=request.canon_version,
            book_bible_version=request.book_bible_version,
            style_version=request.style_version,
            memory_snapshot_id=request.memory_snapshot_id,
            graph_version=request.graph_version,
            included_items=items,
            selection_summary=summary,
            total_tokens=sum(item.token_count for item in items),
            created_at=request.created_at,
        )
        return self.repository.save_context_package(
            package, operation_id=request.operation_id
        )

    def _validate_scope_and_profile(
        self,
        request: ContextBuildRequest,
        policy: ContextPolicy,
        profile: ContextProfile,
        series_access: SeriesAccessContext | None,
    ) -> None:
        if request.project_id != self.repository.context.project_id:
            raise ContextBuilderError("request project scope does not match repository")
        if request.book_id != self.repository.context.book_id:
            raise ContextBuilderError("request book scope does not match repository")
        if request.role != profile.role:
            raise ContextBuilderError("ContextProfile role does not match request")
        if policy.profile_id != profile.profile_id:
            raise ContextBuilderError("ContextPolicy does not reference supplied profile")
        if request.scene_contract is not None:
            request.scene_contract.require_project_scope(StorageScope.project(request.project_id))
        if request.series_id is None:
            if series_access is not None:
                raise ContextBuilderError("independent project cannot use Series Scope")
            return
        if self.series_repository is None or series_access is None:
            raise ContextBuilderError("Series Scope requires repository and access context")
        series_access.require_project(request.project_id)
        series_access.require_series(request.series_id)
        if self.series_repository.context.series_id != request.series_id:
            raise ContextBuilderError("series repository does not match requested series")

    def _collect_candidates(
        self,
        request: ContextBuildRequest,
        policy: ContextPolicy,
        profile: ContextProfile,
        series_access: SeriesAccessContext | None,
    ) -> tuple[tuple[ContextCandidate, ...], bool]:
        candidates: dict[tuple[str, str], ContextCandidate] = {}
        for candidate in request.direct_candidates:
            self._add_candidate(candidates, candidate, request.project_id)

        if request.scene_contract is not None:
            scene = request.scene_contract
            self._add_candidate(
                candidates,
                ContextCandidate(
                    project_id=request.project_id,
                    entity_type="SCENE",
                    entity_id=str(scene.scene_id),
                    layer=ContextLayer.TASK,
                    reason="active SceneContract",
                    representations={RepresentationType.STRUCTURED: scene.to_json()},
                    source_version=scene.version,
                    mandatory=True,
                    task_relevance=1,
                    narrative_recency=1,
                    narrative_order=scene.narrative_order,
                ),
                request.project_id,
            )

        memory_records = self.repository.list_structured_memory_records()
        memory_scopes = self.repository.list_structured_memory_record_scopes()
        for record_id, payload_json in memory_records.items():
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError as exc:
                raise ContextBuilderError("structured memory contains malformed JSON") from exc
            if str(payload.get("project_id")) != request.project_id:
                raise ContextBuilderError("structured memory record escaped project scope")
            record_type = str(memory_scopes[record_id]["record_type"])
            representations = {RepresentationType.STRUCTURED: _canonical_json(payload)}
            summary = payload.get("summary")
            if isinstance(summary, str) and summary.strip():
                representations[RepresentationType.SUMMARY] = summary.strip()
            self._add_candidate(
                candidates,
                ContextCandidate(
                    project_id=request.project_id,
                    entity_type=record_type,
                    entity_id=record_id,
                    layer=_memory_layer(record_type),
                    reason="project structured memory",
                    representations=representations,
                    source_version=payload.get("version", 1),
                    source_ref=self._memory_source_ref(payload),
                    confidence=payload.get("confidence"),
                    story_importance=payload.get("importance"),
                    narrative_order=payload.get("narrative_order"),
                ),
                request.project_id,
            )

        self._apply_graph_retrieval(candidates, request, policy)
        self._apply_series_retrieval(candidates, request, series_access)
        semantic_used = self._apply_semantic_retrieval(candidates, request, policy)
        required_ids = self._required_scene_ids(request.scene_contract)
        found_ids = {
            candidate.entity_id
            for candidate in candidates.values()
            if candidate.layer != ContextLayer.SEMANTIC
        }
        missing = sorted(required_ids - found_ids)
        if missing:
            raise MissingMandatoryContextError(
                "required SceneContract context is missing: " + ", ".join(missing)
            )

        required_layers = set(profile.required_layers)
        present_layers = {candidate.layer for candidate in candidates.values()}
        missing_layers = sorted(layer.value for layer in required_layers - present_layers)
        if missing_layers:
            raise MissingMandatoryContextError(
                "required context layers are missing: " + ", ".join(missing_layers)
            )

        normalized = []
        for candidate in candidates.values():
            mandatory = (
                candidate.mandatory
                or candidate.layer in required_layers
                or (
                    candidate.entity_id in required_ids
                    and candidate.layer != ContextLayer.SEMANTIC
                )
                or candidate.layer == ContextLayer.CONFLICT
            )
            is_required_id = (
                candidate.entity_id in required_ids
                and candidate.layer != ContextLayer.SEMANTIC
            )
            layer = ContextLayer.MUST_INCLUDE if is_required_id else candidate.layer
            reason = (
                "SceneContract mandatory reference"
                if is_required_id
                else candidate.reason
            )
            if candidate.layer == ContextLayer.CONFLICT:
                representations = {
                    RepresentationType.WARNING: self._conflict_warning(candidate)
                }
            else:
                representations = candidate.representations
            normalized.append(replace(
                candidate,
                mandatory=mandatory,
                layer=layer,
                reason=reason,
                representations=representations,
            ))
        return tuple(normalized), semantic_used

    @staticmethod
    def _memory_source_ref(payload: Mapping[str, Any]) -> str | None:
        for name in (
            "source_artifact_ref", "source_ref", "source_scene_id",
            "established_scene_id", "learned_at_scene_id", "opened_scene_id",
            "created_scene_id", "completed_scene_id",
        ):
            value = payload.get(name)
            if isinstance(value, str) and value:
                return value
        refs = payload.get("source_refs")
        if isinstance(refs, list) and refs:
            return str(refs[0])
        return None

    @staticmethod
    def _add_candidate(
        candidates: dict[tuple[str, str], ContextCandidate],
        candidate: ContextCandidate,
        project_id: str,
    ) -> None:
        if candidate.project_id != project_id:
            raise ContextBuilderError("cross-project context candidate is forbidden")
        existing = candidates.get(candidate.key)
        if existing is None:
            candidates[candidate.key] = candidate
            return
        if (
            _serialize(existing.representations) != _serialize(candidate.representations)
            or existing.source_version != candidate.source_version
        ):
            raise ContextBuilderError("conflicting duplicate context candidate")
        candidates[candidate.key] = replace(
            existing,
            mandatory=existing.mandatory or candidate.mandatory,
            graph_proximity=max(
                value for value in (existing.graph_proximity, candidate.graph_proximity, 0)
            ),
        )

    def _apply_graph_retrieval(
        self,
        candidates: dict[tuple[str, str], ContextCandidate],
        request: ContextBuildRequest,
        policy: ContextPolicy,
    ) -> None:
        traversal_policy = DependencyTraversalPolicy(
            max_depth=policy.graph_max_depth,
            max_edges=policy.graph_max_edges,
            read_only=True,
        )
        for start in sorted(request.graph_starts, key=lambda item: str(item.node_id)):
            self.repository.require_scope(start.scope)
            for step in self.repository.traverse_dependencies(start, traversal_policy):
                target_id = str(step.to_node)
                existing_key = next(
                    (key for key in candidates if key[1] == target_id), None
                )
                proximity = 1.0 / step.depth
                if existing_key is not None:
                    current = candidates[existing_key]
                    candidates[existing_key] = replace(
                        current,
                        graph_proximity=max(current.graph_proximity or 0, proximity),
                    )
                    continue
                edge_payload = step.edge.to_dict()
                self._add_candidate(
                    candidates,
                    ContextCandidate(
                        project_id=request.project_id,
                        entity_type=step.to_node.namespace.name,
                        entity_id=target_id,
                        layer=ContextLayer.GRAPH,
                        reason=f"dependency graph depth {step.depth}",
                        representations={
                            RepresentationType.STRUCTURED: _canonical_json(edge_payload)
                        },
                        source_version=step.edge.version,
                        source_ref=step.edge.source_ref,
                        graph_proximity=proximity,
                    ),
                    request.project_id,
                )

    def _apply_series_retrieval(
        self,
        candidates: dict[tuple[str, str], ContextCandidate],
        request: ContextBuildRequest,
        series_access: SeriesAccessContext | None,
    ) -> None:
        if request.series_id is None:
            return
        assert self.series_repository is not None and series_access is not None
        records = (
            tuple(self.series_repository.list_series_canon(series_access))
            + tuple(self.series_repository.list_series_memory(series_access))
        )
        for record in records:
            is_canon = record.state_kind.value == "SERIES_CANON"
            content = record.to_json()
            candidate = ContextCandidate(
                project_id=request.project_id,
                entity_type=f"{record.state_kind.value}:{record.record_type}",
                entity_id=str(record.record_id),
                layer=ContextLayer.CANON if is_canon else ContextLayer.SERIES_MEMORY,
                reason="authorized Series Scope state",
                representations={RepresentationType.STRUCTURED: content},
                source_version=record.version,
                source_ref=record.source_ref,
                mandatory=is_canon,
                confidence=record.state.get("confidence"),
                story_importance=record.state.get("importance"),
                narrative_order=record.state.get("narrative_order"),
            )
            self._add_candidate(candidates, candidate, request.project_id)

    def _apply_semantic_retrieval(
        self,
        candidates: dict[tuple[str, str], ContextCandidate],
        request: ContextBuildRequest,
        policy: ContextPolicy,
    ) -> bool:
        if self.semantic_provider is None or request.semantic_query is None:
            return False
        for candidate in self.semantic_provider.retrieve(
            request.semantic_query,
            project_id=request.project_id,
            limit=policy.semantic_limit,
        ):
            if not isinstance(candidate, ContextCandidate):
                raise ContextBuilderError("semantic provider returned invalid candidate")
            if candidate.semantic_provenance is None:
                raise ContextBuilderError("semantic candidate provenance is required")
            if (
                candidate.semantic_provenance.embedding_model
                != self.semantic_provider.embedding_model
                or candidate.semantic_provenance.index_version
                != self.semantic_provider.index_version
            ):
                raise ContextBuilderError("semantic candidate provenance does not match provider")
            semantic = replace(
                candidate,
                layer=ContextLayer.SEMANTIC,
                mandatory=False,
                reason="semantic supporting retrieval; not a truth source",
            )
            if semantic.key not in candidates:
                self._add_candidate(candidates, semantic, request.project_id)
        return True

    @staticmethod
    def _required_scene_ids(scene: SceneContract | None) -> set[str]:
        if scene is None:
            return set()
        values = (
            tuple(scene.facts_required)
            + tuple(scene.must_include_facts)
            + tuple(scene.must_include_events)
            + tuple(scene.must_include_threads)
            + tuple(scene.must_include_character_states)
        )
        return {str(value) for value in values}

    @staticmethod
    def _conflict_warning(candidate: ContextCandidate) -> str:
        source = next(iter(candidate.representations.values()))
        return _canonical_json({
            "warning": "PROJECT DATA CONTAINS UNRESOLVED CONFLICT",
            "conflict": source,
            "instruction": (
                "Treat all disputed versions as unconfirmed until an author decision resolves them."
            ),
        })

    @staticmethod
    def _score(
        candidate: ContextCandidate,
        policy: ContextPolicy,
    ) -> tuple[float, Mapping[str, float]]:
        breakdown = {
            name: policy.ranking_weights[name] * float(getattr(candidate, name) or 0)
            for name in RANKING_DIMENSIONS
        }
        return sum(breakdown.values()), MappingProxyType(breakdown)

    def _candidate_representations(
        self,
        candidate: ContextCandidate,
        request: ContextBuildRequest,
        policy: ContextPolicy,
        *,
        score: float | None,
        breakdown: Mapping[str, float] | None,
    ) -> tuple[ContextItem, ...]:
        result = []
        for representation in policy.allowed_representations:
            content = candidate.representations.get(representation)
            if content is None:
                continue
            token_count = self.token_counter.count_tokens(
                content, model=request.effective_model
            )
            if isinstance(token_count, bool) or not isinstance(token_count, int) or token_count < 0:
                raise ContextBuilderError("TokenCounter returned an invalid token count")
            result.append(ContextItem(
                entity_type=candidate.entity_type,
                entity_id=candidate.entity_id,
                layer=candidate.layer,
                mandatory=candidate.mandatory,
                reason=candidate.reason,
                score=score,
                score_breakdown=breakdown,
                representation_type=representation,
                token_count=token_count,
                source_version=candidate.source_version,
                content_hash=_sha256(content),
                content=content,
                source_ref=candidate.source_ref,
                semantic_provenance=candidate.semantic_provenance,
            ))
        if not result:
            raise ContextBuilderError(
                f"candidate {candidate.entity_id} has no allowed representation"
            )
        return tuple(result)

    def _select(
        self,
        candidates: tuple[ContextCandidate, ...],
        *,
        request: ContextBuildRequest,
        policy: ContextPolicy,
        profile: ContextProfile,
        available_tokens: int,
        semantic_used: bool,
    ) -> tuple[tuple[ContextItem, ...], SelectionSummary]:
        priority = {layer: index for index, layer in enumerate(policy.layer_priorities)}
        eligible = [
            candidate for candidate in candidates
            if candidate.mandatory or candidate.layer in profile.allowed_layers
        ]
        excluded = [
            {
                "entity_type": candidate.entity_type,
                "entity_id": candidate.entity_id,
                "reason": "layer excluded by ContextProfile",
                "score": None,
            }
            for candidate in candidates
            if candidate not in eligible
        ]
        mandatory = sorted(
            (candidate for candidate in eligible if candidate.mandatory),
            key=lambda item: (priority[item.layer], item.entity_type, item.entity_id),
        )
        optional_scored = []
        for candidate in eligible:
            if candidate.mandatory:
                continue
            score, breakdown = self._score(candidate, policy)
            optional_scored.append((candidate, score, breakdown))
        optional_scored.sort(
            key=lambda item: (
                priority[item[0].layer],
                -item[1],
                item[0].entity_type,
                item[0].entity_id,
            )
        )

        mandatory_options = [
            list(self._candidate_representations(
                candidate, request, policy, score=None, breakdown=None
            ))
            for candidate in mandatory
        ]
        chosen_indexes = [0] * len(mandatory_options)
        mandatory_tokens = sum(options[0].token_count for options in mandatory_options)
        while mandatory_tokens > available_tokens:
            downgrade = None
            for index, options in enumerate(mandatory_options):
                current = chosen_indexes[index]
                if current + 1 >= len(options):
                    continue
                saving = options[current].token_count - options[current + 1].token_count
                candidate_key = (mandatory[index].entity_type, mandatory[index].entity_id)
                choice = (saving, candidate_key, index)
                if saving > 0 and (downgrade is None or choice > downgrade):
                    downgrade = choice
            if downgrade is None:
                raise ContextOverflowError(
                    available_tokens=available_tokens,
                    mandatory_tokens=mandatory_tokens,
                )
            index = downgrade[2]
            old = mandatory_options[index][chosen_indexes[index]].token_count
            chosen_indexes[index] += 1
            new = mandatory_options[index][chosen_indexes[index]].token_count
            mandatory_tokens -= old - new

        selected = [
            options[chosen_indexes[index]]
            for index, options in enumerate(mandatory_options)
        ]
        remaining = available_tokens - mandatory_tokens
        selected_optional_scores = []
        all_optional_scores = []
        for candidate, score, breakdown in optional_scored:
            all_optional_scores.append(score)
            options = self._candidate_representations(
                candidate, request, policy, score=score, breakdown=breakdown
            )
            chosen = next((item for item in options if item.token_count <= remaining), None)
            if chosen is None:
                excluded.append({
                    "entity_type": candidate.entity_type,
                    "entity_id": candidate.entity_id,
                    "reason": "token budget exhausted",
                    "score": score,
                })
                continue
            selected.append(chosen)
            remaining -= chosen.token_count
            selected_optional_scores.append(score)

        selected.sort(key=lambda item: (
            priority[item.layer],
            0 if item.mandatory else 1,
            -(item.score or 0),
            item.entity_type,
            item.entity_id,
        ))
        excluded.sort(key=lambda item: (
            -(item["score"] if item["score"] is not None else -1),
            item["entity_type"],
            item["entity_id"],
        ))
        statistics = {
            "optional_scored_count": len(all_optional_scores),
            "minimum_score": min(all_optional_scores) if all_optional_scores else None,
            "maximum_score": max(all_optional_scores) if all_optional_scores else None,
            "average_score": (
                sum(all_optional_scores) / len(all_optional_scores)
                if all_optional_scores else None
            ),
        }
        summary = SelectionSummary(
            candidate_count=len(candidates),
            selected_count=len(selected),
            excluded_count=len(candidates) - len(selected),
            cutoff=min(selected_optional_scores) if selected_optional_scores else None,
            top_rejected=tuple(excluded[:policy.top_rejected_limit]),
            ranking_statistics=statistics,
            semantic_retrieval_used=semantic_used,
        )
        return tuple(selected), summary


__all__ = [
    "CONTEXT_PACKAGE_SCHEMA_VERSION",
    "ContextBuildRequest",
    "ContextBuilder",
    "ContextBuilderError",
    "ContextCandidate",
    "ContextItem",
    "ContextLayer",
    "ContextOverflowError",
    "ContextPackage",
    "ContextPolicy",
    "ContextProfile",
    "ContextRole",
    "MissingMandatoryContextError",
    "OverflowBehavior",
    "RANKING_DIMENSIONS",
    "RepresentationType",
    "STRUCTURED_FIRST_LAYER_ORDER",
    "SelectionSummary",
    "SemanticProvenance",
    "SemanticRetrievalProvider",
    "TokenCounter",
    "default_context_profiles",
]

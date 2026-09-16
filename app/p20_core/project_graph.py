from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.p20_core.domain_records import (
    DomainContractError, DomainId, EdgeRecord, EdgeRelationType,
)
from app.p20_core.project_repository import StorageScope


@dataclass(frozen=True)
class GraphNodeRef:
    scope: StorageScope
    node_id: DomainId | str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, StorageScope):
            raise DomainContractError("graph node scope must be PROJECT or SERIES")
        node_id = self.node_id if isinstance(self.node_id, DomainId) else DomainId.parse(self.node_id)
        if (self.scope.scope_type.value == "PROJECT"
                and node_id.namespace.value == "PROJ"
                and str(node_id) != self.scope.scope_id):
            raise DomainContractError("cross-project graph node is forbidden")
        if (self.scope.scope_type.value == "SERIES"
                and node_id.namespace.value == "SERIES"
                and str(node_id) != self.scope.scope_id):
            raise DomainContractError("cross-series graph node is forbidden")
        object.__setattr__(self, "node_id", node_id)


class TraversalDirection(str, Enum):
    OUTGOING = "OUTGOING"
    INCOMING = "INCOMING"
    BOTH = "BOTH"


class GraphTraversalLimitError(ValueError):
    pass


@dataclass(frozen=True)
class DependencyTraversalPolicy:
    direction: TraversalDirection | str = TraversalDirection.OUTGOING
    max_depth: int = 2
    relation_types: tuple[EdgeRelationType | str, ...] | None = None
    max_edges: int = 10000
    relation_directions: tuple[tuple[EdgeRelationType | str, TraversalDirection | str], ...] | None = None
    read_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.read_only, bool):
            raise DomainContractError("read_only must be boolean")
        if self.relation_directions is not None:
            try:
                directions = tuple((EdgeRelationType(r), TraversalDirection(d)) for r, d in self.relation_directions)
            except (ValueError, TypeError) as exc:
                raise DomainContractError("invalid relation direction policy") from exc
            if len({r for r, _ in directions}) != len(directions):
                raise DomainContractError("duplicate relation direction")
            object.__setattr__(self, "relation_directions", tuple(sorted(directions)))
        try:
            object.__setattr__(self, "direction", TraversalDirection(self.direction))
        except ValueError as exc:
            raise DomainContractError("unsupported traversal direction") from exc
        for name, minimum in (("max_depth", 0), ("max_edges", 1)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise DomainContractError(f"{name} must be an integer >= {minimum}")
        if self.relation_types is not None:
            if isinstance(self.relation_types, (str, bytes)):
                raise DomainContractError("relation_types must be a collection")
            try:
                relations = tuple(sorted({EdgeRelationType(r) for r in self.relation_types}))
            except (ValueError, TypeError) as exc:
                raise DomainContractError("unsupported traversal relation type") from exc
            object.__setattr__(self, "relation_types", relations)


@dataclass(frozen=True)
class DependencyTraversalStep:
    edge: EdgeRecord
    depth: int
    from_node: DomainId
    to_node: DomainId

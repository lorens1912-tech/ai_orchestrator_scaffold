"""Read-only potential impact over a repository-scoped graph."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from app.p20_core.domain_records import DomainContractError, DomainId, DomainNamespace, EdgeRelationType
from app.p20_core.project_graph import DependencyTraversalPolicy, GraphNodeRef, TraversalDirection
from app.p20_core.project_repository import (
    ProjectRepository, ScopeType, SeriesAccessContext, SeriesAccessError,
    SeriesRepository, StorageScope,
)


class ImpactScopeUnsupportedError(ValueError):
    pass


class ImpactClass(str, Enum):
    DIRECT = "DIRECT"
    TRANSITIVE = "TRANSITIVE"
    DERIVED = "DERIVED"


# Edge X DEPENDS_ON Y: change Y affects X. Causal/narrative edges flow
# forward; symmetric relations are conservative potential-impact signals.
IMPACT_RELATION_DIRECTIONS = MappingProxyType({
    EdgeRelationType.REQUIRES: TraversalDirection.INCOMING,
    EdgeRelationType.DEPENDS_ON: TraversalDirection.INCOMING,
    EdgeRelationType.KNOWS: TraversalDirection.INCOMING,
    EdgeRelationType.BELIEVES: TraversalDirection.INCOMING,
    EdgeRelationType.USES: TraversalDirection.INCOMING,
    # Participation/location/revelation constrain both endpoints. A changed
    # character affects its scenes; a changed scene can affect its participants.
    EdgeRelationType.LOCATED_AT: TraversalDirection.BOTH,
    EdgeRelationType.PRESENT_IN: TraversalDirection.BOTH,
    EdgeRelationType.CAUSES: TraversalDirection.OUTGOING,
    EdgeRelationType.MOTIVATES: TraversalDirection.OUTGOING,
    EdgeRelationType.REVEALS: TraversalDirection.BOTH,
    EdgeRelationType.OPENS: TraversalDirection.OUTGOING,
    EdgeRelationType.PROGRESSES: TraversalDirection.OUTGOING,
    EdgeRelationType.CLOSES: TraversalDirection.OUTGOING,
    EdgeRelationType.SETS_UP: TraversalDirection.OUTGOING,
    EdgeRelationType.PAYS_OFF: TraversalDirection.OUTGOING,
    EdgeRelationType.PART_OF: TraversalDirection.OUTGOING,
    EdgeRelationType.CONTRADICTS: TraversalDirection.BOTH,
    EdgeRelationType.RELATED_TO: TraversalDirection.BOTH,
})
IMPACT_POLICY_VERSION = 1
DERIVED_ENTITY_TYPES = frozenset({DomainNamespace.EVALUATION, DomainNamespace.CONTEXT})


@dataclass(frozen=True)
class ImpactRequest:
    project_id: str
    scope_type: str
    scope_id: str
    entity_type: str
    entity_id: DomainId | str
    operation_type: str | None = None

    def __post_init__(self) -> None:
        DomainId.parse(self.project_id).require_namespace(DomainNamespace.PROJECT)
        scope = StorageScope(self.scope_type, self.scope_id)
        node = self.entity_id if isinstance(self.entity_id, DomainId) else DomainId.parse(self.entity_id)
        if self.entity_type not in (node.namespace.name, node.namespace.value):
            raise DomainContractError("entity_type does not match domain ID")
        if scope.scope_type == ScopeType.PROJECT and scope.scope_id != self.project_id:
            raise DomainContractError("impact project and scope must match")
        if self.operation_type is not None and (not isinstance(self.operation_type, str) or not self.operation_type.strip()):
            raise DomainContractError("operation_type must be non-empty text")
        object.__setattr__(self, "scope_type", scope.scope_type.value)
        object.__setattr__(self, "entity_type", node.namespace.name)
        object.__setattr__(self, "entity_id", node)

    @property
    def scope(self) -> StorageScope:
        return StorageScope(self.scope_type, self.scope_id)


@dataclass(frozen=True)
class ImpactPathHop:
    edge_id: str
    edge_version: int
    relation_type: str
    from_id: str
    to_id: str
    source_ref: str | None

    def to_dict(self) -> dict:
        return dict(vars(self))


@dataclass(frozen=True)
class ImpactItem:
    entity_id: str
    entity_type: str
    scope: StorageScope
    impact_class: ImpactClass
    path: tuple[ImpactPathHop, ...]

    @property
    def depth(self) -> int:
        return len(self.path)

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id, "entity_type": self.entity_type,
            "scope": self.scope.to_dict(), "impact_class": self.impact_class.value,
            "dependency_class": "DIRECT" if self.depth == 1 else "TRANSITIVE",
            "depth": self.depth, "path": [hop.to_dict() for hop in self.path],
        }


@dataclass(frozen=True)
class ImpactResult:
    source: ImpactRequest
    max_depth: int
    max_edges: int
    impacts: tuple[ImpactItem, ...]

    def to_dict(self) -> dict:
        return {
            "policy_version": IMPACT_POLICY_VERSION,
            "source": {
                "project_id": self.source.project_id, "scope": self.source.scope.to_dict(),
                "entity_id": str(self.source.entity_id), "entity_type": self.source.entity_type,
                "operation_type": self.source.operation_type,
            },
            "max_depth": self.max_depth, "max_edges": self.max_edges,
            "coverage": (
                "BOUNDED_PROJECT_GRAPH"
                if self.source.scope.scope_type == ScopeType.PROJECT
                else "BOUNDED_SERIES_GRAPH"
            ),
            "impacts": [item.to_dict() for item in self.impacts],
        }


def analyze_impact(
    repository: ProjectRepository | SeriesRepository,
    request: ImpactRequest,
    *,
    max_depth: int = 8,
    max_edges: int = 10000,
    series_access: SeriesAccessContext | None = None,
) -> ImpactResult:
    """Return one deterministic shortest explanation per affected graph node.

    No temporal filtering or automatic invalidation is implied. The result covers
    only persisted edges within the explicit depth bound, not unrecorded links.
    """
    if not isinstance(repository, (ProjectRepository, SeriesRepository)) or not isinstance(request, ImpactRequest):
        raise DomainContractError("impact requires a scoped repository and ImpactRequest")
    repository.require_scope(request.scope)
    if request.scope.scope_type == ScopeType.PROJECT:
        if not isinstance(repository, ProjectRepository):
            raise DomainContractError("PROJECT impact requires ProjectRepository")
    else:
        if not isinstance(repository, SeriesRepository):
            raise DomainContractError("SERIES impact requires SeriesRepository")
        if not isinstance(series_access, SeriesAccessContext):
            raise SeriesAccessError("impact SERIES requires explicit access context")
        series_access.require_project(request.project_id)
        series_access.require_series(request.scope_id)
    start = GraphNodeRef(request.scope, request.entity_id)
    policy = DependencyTraversalPolicy(
        direction=TraversalDirection.BOTH, max_depth=max_depth, max_edges=max_edges,
        relation_directions=tuple(IMPACT_RELATION_DIRECTIONS.items()), read_only=True,
    )
    steps = (
        repository.traverse_dependencies(series_access, start, policy)
        if isinstance(repository, SeriesRepository)
        else repository.traverse_dependencies(start, policy)
    )
    # GAP-010 supplies BFS order. Retain its first shortest path, not a second traversal.
    paths: dict[str, tuple[ImpactPathHop, ...]] = {str(start.node_id): ()}
    items = []
    for step in steps:
        node = str(step.to_node)
        if node in paths:
            continue
        hop = ImpactPathHop(step.edge.edge_id, step.edge.version, step.edge.relation_type.value,
                            str(step.from_node), node, step.edge.source_ref)
        path = paths[str(step.from_node)] + (hop,)
        paths[node] = path
        classification = ImpactClass.DIRECT if len(path) == 1 else ImpactClass.TRANSITIVE
        if step.to_node.namespace in DERIVED_ENTITY_TYPES:
            classification = ImpactClass.DERIVED
        items.append(ImpactItem(node, step.to_node.namespace.name, request.scope, classification, path))
    return ImpactResult(request, max_depth, max_edges, tuple(sorted(items, key=lambda x: (x.depth, x.entity_type, x.entity_id))))

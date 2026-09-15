from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from app.p20_core.context_builder import (
    ContextBuildRequest,
    ContextBuilder,
    ContextCandidate,
    ContextLayer,
    ContextPackage,
    ContextPolicy,
    ContextRole,
    OverflowBehavior,
    RepresentationType,
    STRUCTURED_FIRST_LAYER_ORDER,
    default_context_profiles,
)
from app.p20_core.domain_records import DomainId, DomainNamespace
from app.p20_core.project_graph import GraphNodeRef
from app.p20_core.project_repository import (
    ProjectRepository,
    SeriesAccessContext,
    SeriesAccessError,
    SeriesRepository,
    StorageResolver,
    SystemStorageError,
    ensure_system_repository,
)


RUNTIME_CONTEXT_POLICY_VERSION = 1
_SAFE_BODY = re.compile(r"[^A-Za-z0-9_.-]+")
_ROLE_BY_TEAM = {
    "CRITIC": ContextRole.CRITIC,
    "QA": ContextRole.CRITIC,
    "CONTINUITY": ContextRole.CONTINUITY,
    "FACTCHECK": ContextRole.CANON,
    "EXTRACTOR": ContextRole.CANON,
    "VERIFIER": ContextRole.CANON,
}


class ProjectExecutionIdentityError(ValueError):
    pass


def _required_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _canonical_domain_id(
    value: Any,
    namespace: DomainNamespace,
    *,
    field_name: str,
) -> str:
    raw = _required_text(value, field_name)
    try:
        parsed = DomainId.parse(raw)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.namespace == namespace:
        return str(parsed)

    body = _SAFE_BODY.sub("-", raw).strip(".-") or hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:16]
    if len(body) > 96:
        body = f"{body[:79]}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"
    return str(DomainId(namespace, f"{namespace.value}-{body}"))


def canonical_project_id(project_id: Any) -> str:
    return _canonical_domain_id(
        project_id,
        DomainNamespace.PROJECT,
        field_name="project_id",
    )


def canonical_book_id(book_id: Any) -> str:
    return _canonical_domain_id(
        book_id,
        DomainNamespace.BOOK,
        field_name="book_id",
    )


def canonical_series_id(series_id: Any) -> str | None:
    if series_id is None or not str(series_id).strip():
        return None
    return _canonical_domain_id(
        series_id,
        DomainNamespace.SERIES,
        field_name="series_id",
    )


def _validate_project_binding(project_id: str, book_id: str) -> None:
    try:
        repository = ProjectRepository(
            StorageResolver().resolve_project(project_id, book_id=book_id)
        )
        repository.initialize()
        identity = repository.get_project_identity()
    except ValueError as exc:
        raise ProjectExecutionIdentityError(str(exc)) from exc
    if identity["project_id"] != project_id or identity["book_id"] != book_id:
        raise ProjectExecutionIdentityError(
            "project repository identity does not match execution identity"
        )


def resolve_runtime_project_id(project_id: Any, *, book_id: Any) -> tuple[str, str]:
    canonical_book = canonical_book_id(book_id)
    registry = ensure_system_repository()
    try:
        registered_project = registry.resolve_project_id_for_book(canonical_book)
        discovered_projects = registry.discover_project_bindings(canonical_book)
    except SystemStorageError as exc:
        raise ProjectExecutionIdentityError(str(exc)) from exc

    explicit = str(project_id or "").strip()
    if explicit:
        canonical_project = canonical_project_id(explicit)
        if registered_project is not None and registered_project != canonical_project:
            raise ProjectExecutionIdentityError(
                "book_id is registered to a different project_id"
            )
        foreign = [
            candidate
            for candidate in discovered_projects
            if candidate != canonical_project
        ]
        if foreign:
            raise ProjectExecutionIdentityError(
                "book_id has a conflicting persisted project identity"
            )
        _validate_project_binding(canonical_project, canonical_book)
        try:
            registry.bind_project(canonical_project, canonical_book)
        except SystemStorageError as exc:
            raise ProjectExecutionIdentityError(str(exc)) from exc
        return canonical_project, canonical_book

    if registered_project is not None:
        foreign = [
            candidate
            for candidate in discovered_projects
            if candidate != registered_project
        ]
        if foreign:
            raise ProjectExecutionIdentityError(
                "book_id resolves to more than one persisted project"
            )
        _validate_project_binding(registered_project, canonical_book)
        return registered_project, canonical_book

    if len(discovered_projects) > 1:
        raise ProjectExecutionIdentityError(
            "book_id resolves to more than one persisted project"
        )
    if len(discovered_projects) == 1:
        resolved_project = discovered_projects[0]
        try:
            DomainId.parse(resolved_project).require_namespace(
                DomainNamespace.PROJECT,
                "project_id",
            )
        except ValueError as exc:
            raise ProjectExecutionIdentityError(
                "legacy project identity cannot be resolved safely; project_id is required"
            ) from exc
        _validate_project_binding(resolved_project, canonical_book)
        try:
            registry.bind_project(resolved_project, canonical_book)
        except SystemStorageError as exc:
            raise ProjectExecutionIdentityError(str(exc)) from exc
        return resolved_project, canonical_book

    proposed_project = f"PROJ-runtime-{uuid4().hex}"
    try:
        resolved_project = registry.resolve_or_bind_project(
            proposed_project,
            canonical_book,
        )
    except SystemStorageError as exc:
        raise ProjectExecutionIdentityError(str(exc)) from exc
    _validate_project_binding(resolved_project, canonical_book)
    return resolved_project, canonical_book


def resolve_runtime_series_id(
    series_id: Any,
    *,
    project_id: str,
    book_id: str,
) -> str | None:
    requested_series = canonical_series_id(series_id)
    registry = ensure_system_repository()
    try:
        memberships = registry.discover_series_memberships(project_id)
    except SystemStorageError as exc:
        raise ProjectExecutionIdentityError(str(exc)) from exc

    if requested_series is None:
        if memberships:
            raise ProjectExecutionIdentityError(
                "series_id is required for a project registered in Series Scope"
            )
        return None

    matching = [
        membership
        for membership in memberships
        if membership[0] == requested_series
    ]
    if not matching:
        raise ProjectExecutionIdentityError(
            "project is not a registered member of requested series_id"
        )
    if any(bound_book != book_id for _series, bound_book in matching):
        raise ProjectExecutionIdentityError(
            "series membership book does not match requested book_id"
        )
    if any(bound_series != requested_series for bound_series, _book in memberships):
        raise ProjectExecutionIdentityError(
            "project has ambiguous Series Scope membership"
        )
    return requested_series


@dataclass(frozen=True)
class ProjectExecutionContext:
    project_id: str
    book_id: str
    series_id: str | None
    run_id: str
    step_id: str
    technical_retry: bool = False
    series_access_context: SeriesAccessContext | None = None

    def __post_init__(self) -> None:
        DomainId.parse(self.project_id).require_namespace(
            DomainNamespace.PROJECT, "project_id"
        )
        DomainId.parse(self.book_id).require_namespace(DomainNamespace.BOOK, "book_id")
        if self.series_id is not None:
            DomainId.parse(self.series_id).require_namespace(
                DomainNamespace.SERIES, "series_id"
            )
            if self.series_access_context is None:
                raise ProjectExecutionIdentityError(
                    "Series Scope requires SeriesAccessContext"
                )
            self.series_access_context.require_project(self.project_id)
            self.series_access_context.require_series(self.series_id)
        elif self.series_access_context is not None:
            raise ProjectExecutionIdentityError(
                "project without series cannot carry SeriesAccessContext"
            )
        for name in ("run_id", "step_id"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        if not isinstance(self.technical_retry, bool):
            raise ValueError("technical_retry must be boolean")

    @classmethod
    def create(
        cls,
        *,
        project_id: Any,
        book_id: Any,
        series_id: Any,
        run_id: Any,
        step_id: Any,
        technical_retry: bool = False,
    ) -> ProjectExecutionContext:
        canonical_project = canonical_project_id(project_id)
        canonical_book = canonical_book_id(book_id)
        canonical_series = canonical_series_id(series_id)
        return cls(
            project_id=canonical_project,
            book_id=canonical_book,
            series_id=canonical_series,
            run_id=_required_text(run_id, "run_id"),
            step_id=_required_text(step_id, "step_id"),
            technical_retry=technical_retry,
            series_access_context=(
                SeriesAccessContext.bind(canonical_project, canonical_series)
                if canonical_series is not None
                else None
            ),
        )

    def for_step(self, index: int, mode: str) -> ProjectExecutionContext:
        return replace(
            self,
            step_id=f"{self.step_id}:{int(index):03d}:{_required_text(mode, 'mode').upper()}",
        )

    @property
    def operation_id(self) -> str:
        return f"context:{self.project_id}:{self.run_id}:{self.step_id}"

    @property
    def context_package_id(self) -> str:
        digest = hashlib.sha256(self.operation_id.encode("utf-8")).hexdigest()[:32]
        return f"CONTEXT-{digest}"


class RuntimeLexemeTokenCounter:
    """Exact counter for the current deterministic offline P20 provider boundary."""

    tokenizer_id = "p20-runtime-lexeme"
    tokenizer_version = "1"

    _TOKENS = re.compile(r"\w+|[^\w\s]", re.UNICODE)

    def count_tokens(self, text: str, *, model: str) -> int:
        del model
        return len(self._TOKENS.findall(str(text)))


def _context_role(team_id: str) -> ContextRole:
    return _ROLE_BY_TEAM.get(str(team_id or "").upper().strip(), ContextRole.WRITER)


def _runtime_policy(role: ContextRole, mode: str) -> ContextPolicy:
    profile = default_context_profiles()[role]
    return ContextPolicy(
        policy_id=f"P20_{str(mode).upper()}_CONTEXT",
        version=RUNTIME_CONTEXT_POLICY_VERSION,
        profile_id=profile.profile_id,
        context_window=32768,
        reserved_output_tokens=4096,
        reserved_system_tokens=2048,
        technical_overhead_tokens=512,
        layer_priorities=STRUCTURED_FIRST_LAYER_ORDER,
        ranking_weights={
            "graph_proximity": 1,
            "task_relevance": 1,
            "narrative_recency": 1,
            "confidence": 1,
            "semantic_similarity": 1,
            "story_importance": 1,
        },
        graph_max_depth=2,
        graph_max_edges=100,
        overflow_behavior=OverflowBehavior.ESCALATE,
        allowed_representations=(
            RepresentationType.FULL,
            RepresentationType.STRUCTURED,
            RepresentationType.COMPRESSED,
            RepresentationType.SUMMARY,
            RepresentationType.WARNING,
        ),
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _semantic_canon(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    canon = dict(value)
    meta = canon.get("_meta")
    if isinstance(meta, Mapping):
        meta = dict(meta)
        meta.pop("generated_at", None)
        canon["_meta"] = meta
    return canon


def _source_version(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _task_content(tool_input: Mapping[str, Any], *, mode: str, role: str) -> str:
    public_input = {
        str(key): value
        for key, value in tool_input.items()
        if key != "_context_package" and not str(key).startswith("_context_runtime")
    }
    return _canonical_json({"mode": mode, "role": role, "input": public_input})


def _graph_starts(
    tool_input: Mapping[str, Any],
    repository: ProjectRepository,
) -> tuple[GraphNodeRef, ...]:
    raw = tool_input.get("context_graph_starts")
    if raw is None:
        raw = tool_input.get("graph_starts")
    if raw is None:
        return ()
    if isinstance(raw, (str, Mapping)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("context_graph_starts must be a collection")

    starts = []
    for item in raw:
        node_id = item.get("node_id") if isinstance(item, Mapping) else item
        starts.append(GraphNodeRef(repository.scope, _required_text(node_id, "graph node_id")))
    return tuple(starts)


def build_runtime_context_package(
    *,
    execution_context: ProjectExecutionContext,
    mode: str,
    role: str,
    requested_model: str | None,
    effective_model: str,
    tool_input: Mapping[str, Any],
    context_sources: Mapping[str, Any],
) -> ContextPackage:
    del requested_model
    resolver = StorageResolver()
    repository = ProjectRepository(
        resolver.resolve_project(
            execution_context.project_id,
            book_id=execution_context.book_id,
        )
    )
    repository.initialize()

    series_repository = None
    series_access = execution_context.series_access_context
    if execution_context.series_id is not None:
        series_repository = SeriesRepository(
            resolver.resolve_series(execution_context.series_id)
        )
        series_repository.initialize()
        assert series_access is not None
        try:
            series_repository.require_registered_member(
                series_access,
                book_id=execution_context.book_id,
            )
        except SeriesAccessError as exc:
            raise ProjectExecutionIdentityError(str(exc)) from exc

    builder = ContextBuilder(
        repository,
        RuntimeLexemeTokenCounter(),
        series_repository=series_repository,
    )
    if execution_context.technical_retry:
        return builder.reuse_for_retry(
            project_id=execution_context.project_id,
            operation_id=execution_context.operation_id,
        )

    context_role = _context_role(role)
    profile = default_context_profiles()[context_role]
    policy = _runtime_policy(context_role, mode)
    canon = _semantic_canon(context_sources.get("canon") or {})
    book_bible = context_sources.get("book_bible") or {}
    task = _task_content(tool_input, mode=mode, role=role)
    canon_content = _canonical_json(canon)
    bible_content = _canonical_json(book_bible)
    canon_version = str(context_sources.get("canon_version") or _source_version(canon))
    bible_version = str(
        context_sources.get("book_bible_version") or _source_version(book_bible)
    )
    source_ref = f"runs/{execution_context.run_id}/steps/{execution_context.step_id}"

    direct_candidates = (
        ContextCandidate(
            project_id=execution_context.project_id,
            entity_type="TASK",
            entity_id=f"CONTEXT-task-{hashlib.sha256(execution_context.operation_id.encode('utf-8')).hexdigest()[:24]}",
            layer=ContextLayer.TASK,
            reason="active P20 step input",
            representations={RepresentationType.STRUCTURED: task},
            source_version=1,
            source_ref=source_ref,
            mandatory=True,
            task_relevance=1,
        ),
        ContextCandidate(
            project_id=execution_context.project_id,
            entity_type="CANON",
            entity_id=f"CONTEXT-canon-{hashlib.sha256(execution_context.project_id.encode('utf-8')).hexdigest()[:24]}",
            layer=ContextLayer.CANON,
            reason="active project canon snapshot",
            representations={RepresentationType.STRUCTURED: canon_content},
            source_version=canon_version,
            source_ref=str(context_sources.get("canon_snapshot_path") or "project canon"),
            mandatory=True,
        ),
        ContextCandidate(
            project_id=execution_context.project_id,
            entity_type="BOOK_BIBLE",
            entity_id=execution_context.book_id,
            layer=ContextLayer.BOOK_BIBLE,
            reason="active Book Bible",
            representations={RepresentationType.STRUCTURED: bible_content},
            source_version=bible_version,
            source_ref=str(context_sources.get("book_bible_path") or "book_bible.json"),
            mandatory=True,
        ),
    )
    request = ContextBuildRequest(
        context_package_id=execution_context.context_package_id,
        operation_id=execution_context.operation_id,
        project_id=execution_context.project_id,
        book_id=execution_context.book_id,
        series_id=execution_context.series_id,
        run_id=execution_context.run_id,
        step_id=execution_context.step_id,
        role=context_role,
        mode=str(mode).upper(),
        effective_model=_required_text(effective_model, "effective_model"),
        canon_version=canon_version,
        book_bible_version=bible_version,
        style_version=context_sources.get("style_version"),
        memory_snapshot_id=context_sources.get("memory_snapshot_id"),
        graph_version=repository.get_schema_version(),
        created_at=datetime.now(timezone.utc).isoformat(),
        direct_candidates=direct_candidates,
        graph_starts=_graph_starts(tool_input, repository),
        semantic_query=None,
    )
    return builder.build(
        request,
        policy,
        profile,
        series_access=series_access,
    )


__all__ = [
    "ProjectExecutionIdentityError",
    "ProjectExecutionContext",
    "RUNTIME_CONTEXT_POLICY_VERSION",
    "RuntimeLexemeTokenCounter",
    "build_runtime_context_package",
    "canonical_book_id",
    "canonical_project_id",
    "canonical_series_id",
    "resolve_runtime_project_id",
    "resolve_runtime_series_id",
]

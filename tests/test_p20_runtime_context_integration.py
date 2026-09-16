from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.domain_records import EdgeRecord, FactRecord
from app.p20_core.project_repository import (
    ProjectRepository,
    SeriesAccessContext,
    SeriesRepository,
    StorageResolver,
)
from app.p20_core.series_memory import (
    SeriesMembershipRecord,
    SeriesStateKind,
    SeriesStateRecord,
)
from app.p20_core.storage_paths import get_runs_root, get_storage_root
import app.p20_core.executor as executor


REPO_ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-09-15T10:00:00Z"


def _read_public_json(path: str) -> dict:
    public_path = Path(path)
    assert not public_path.is_absolute()
    return json.loads((get_storage_root() / public_path).read_text(encoding="utf-8"))


def _fact(project_id: str, fact_id: str, book_id: str, value: str) -> FactRecord:
    return FactRecord(
        fact_id=fact_id,
        project_id=project_id,
        subject_id="CHAR-f001-ada",
        predicate="knows",
        object_type="TEXT",
        object_id=None,
        object_value=value,
        reality_status="TRUE",
        verification_status="VERIFIED",
        confidence=1,
        frozen=False,
        author_locked=False,
        valid_from="day-1",
        valid_to=None,
        established_event_id=None,
        established_scene_id="SCENE-f001-source",
        source_artifact_ref=f"books/{book_id}/chapters/source.json",
        source_refs=("SCENE-f001-source#artifact",),
        canon_version=1,
        version=1,
        created_at=STAMP,
        updated_at=STAMP,
    )


def _project_repository(project_id: str, book_id: str) -> ProjectRepository:
    repository = ProjectRepository(
        StorageResolver().resolve_project(project_id, book_id=book_id)
    )
    repository.initialize()
    return repository


def _persist_fact_and_graph(
    repository: ProjectRepository,
    *,
    project_id: str,
    book_id: str,
    fact_id: str,
) -> None:
    with repository.domain_transaction() as transaction:
        transaction.add_structured_memory_record(
            _fact(project_id, fact_id, book_id, "the observatory key is hidden")
        )
        transaction.add_edge(
            EdgeRecord(
                edge_id="edge-f001-context",
                scope_type="PROJECT",
                scope_id=project_id,
                source_type="SCENE",
                source_id="SCENE-f001-source",
                relation_type="REQUIRES",
                target_type="FACT",
                target_id=fact_id,
                valid_from=None,
                valid_to=None,
                confidence=1,
                source_ref="SCENE-f001-source#contract",
                version=1,
            ),
            source_scope=repository.scope,
            target_scope=repository.scope,
        )


def _register_series_member(
    project_id: str,
    book_id: str,
    series_id: str,
) -> None:
    access = SeriesAccessContext.bind(project_id, series_id)
    repository = SeriesRepository(StorageResolver().resolve_series(series_id))
    repository.initialize()
    repository.register_member(
        access,
        SeriesMembershipRecord(
            series_id=series_id,
            project_id=project_id,
            book_id=book_id,
            source_ref=f"projects/{project_id}/series-membership.json",
            version=1,
            created_at=STAMP,
        ),
    )
    repository.save_series_memory(
        access,
        SeriesStateRecord(
            series_id=series_id,
            state_kind=SeriesStateKind.MEMORY,
            source_project_id=project_id,
            source_book_id=book_id,
            record_type="FACT",
            record_id="FACT-f001-series-oath",
            source_version=1,
            source_ref="SCENE-f001-volume-one",
            provenance_refs=("SCENE-f001-volume-one#artifact",),
            transfer_reason="required series continuity",
            state={"value": "the family oath persists", "importance": 1},
            operation_id="series-f001-context",
            version=1,
            frozen=False,
            author_locked=False,
            created_at=STAMP,
            updated_at=STAMP,
        ),
    )


def test_agent_step_builds_persists_and_uses_real_context_package(
    isolated_agentpro_storage,
    monkeypatch,
) -> None:
    project_id = "PROJ-f001-runtime-a"
    book_id = "BOOK-f001-runtime-a"
    series_id = "SERIES-f001-runtime"
    run_id = "run-f001-functional"
    fact_id = "FACT-f001-observatory-key"
    foreign_project_id = "PROJ-f001-runtime-b"
    foreign_book_id = "BOOK-f001-runtime-b"

    assert not (REPO_ROOT / "books" / book_id).exists()
    assert not (REPO_ROOT / "runs" / run_id).exists()
    ensure_test_book_bible(book_id)

    repository = _project_repository(project_id, book_id)
    _persist_fact_and_graph(
        repository,
        project_id=project_id,
        book_id=book_id,
        fact_id=fact_id,
    )
    foreign_repository = _project_repository(foreign_project_id, foreign_book_id)
    with foreign_repository.domain_transaction() as transaction:
        transaction.add_structured_memory_record(
            _fact(
                foreign_project_id,
                "FACT-f001-foreign-secret",
                foreign_book_id,
                "foreign project only",
            )
        )
    _register_series_member(project_id, book_id, series_id)

    provider_inputs: list[dict] = []

    def provider_adapter(payload: dict) -> dict:
        provider_inputs.append(json.loads(json.dumps(payload)))
        return {
            "tool": "WRITE",
            "payload": {
                "text": (
                    "Ada entered the observatory and recovered the hidden key. "
                    "The family oath shaped her choice, while the locked archive "
                    "remained beyond the eastern stair."
                ),
                "meta": {"requested_model": payload.get("_requested_model")},
            },
        }

    monkeypatch.setitem(executor.TOOLS, "WRITE", provider_adapter)
    response = TestClient(app).post(
        "/agent/step",
        json={
            "mode": "WRITE",
            "project_id": project_id,
            "book_id": book_id,
            "series_id": series_id,
            "run_id": run_id,
            "step_id": "step-f001-functional",
            "payload": {
                "text": "Write the next observatory scene.",
                "model": "gpt-f001-requested",
                "context_graph_starts": ["SCENE-f001-source"],
            },
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True, data
    assert data["project_id"] == project_id
    assert data["series_id"] == series_id
    assert len(provider_inputs) == 1

    boundary_package = provider_inputs[0]["_context_package"]
    package_id = boundary_package["context_package_id"]
    context_hash = boundary_package["context_hash"]
    assert provider_inputs[0]["context_package_id"] == package_id
    assert provider_inputs[0]["context_hash"] == context_hash
    assert boundary_package["project_id"] == project_id
    assert boundary_package["series_id"] == series_id

    included = {
        item["entity_id"]: item for item in boundary_package["included_items"]
    }
    assert fact_id in included
    assert included[fact_id]["score_breakdown"]["graph_proximity"] == 1.0
    assert "FACT-f001-series-oath" in included
    assert "FACT-f001-foreign-secret" not in included

    step = _read_public_json(data["artifact_paths"][0])
    assert step["project_id"] == project_id
    assert step["series_id"] == series_id
    assert step["mode"] == "WRITE"
    assert step["role"] == "WRITER"
    assert step["requested_model"] == "gpt-f001-requested"
    assert step["effective_model"] == "gpt-f001-requested"
    assert step["context_package_id"] == package_id
    assert step["context_hash"] == context_hash
    assert step["input"]["_context_package"] == boundary_package

    run_state = json.loads(
        (get_runs_root() / run_id / "run_state.json").read_text(encoding="utf-8")
    )
    audit = json.loads(
        (get_runs_root() / run_id / "audit.json").read_text(encoding="utf-8")
    )
    for record in (run_state, audit):
        assert record["project_id"] == project_id
        assert record["series_id"] == series_id
        assert record["context_package_id"] == package_id
        assert record["context_hash"] == context_hash
        assert record["context_packages"][0]["role"] == "WRITER"
        assert record["context_packages"][0]["mode"] == "WRITE"
        assert record["context_packages"][0]["requested_model"] == "gpt-f001-requested"
        assert record["context_packages"][0]["effective_model"] == "gpt-f001-requested"

    chapter = _read_public_json(data["chapter_path"])
    assert chapter["project_id"] == project_id
    assert chapter["series_id"] == series_id
    assert chapter["context_package_id"] == package_id
    assert chapter["context_hash"] == context_hash

    del repository
    reopened = _project_repository(project_id, book_id)
    persisted = reopened.get_context_package(package_id)
    assert persisted is not None
    assert persisted.context_hash == context_hash
    assert persisted.compute_context_hash() == context_hash
    assert reopened.get_context_package_for_operation(
        "context:PROJ-f001-runtime-a:run-f001-functional:"
        "step-f001-functional:001:WRITE"
    ) == persisted
    assert _read_public_json(data["artifact_paths"][0])["context_hash"] == context_hash

    assert not (REPO_ROOT / "books" / book_id).exists()
    assert not (REPO_ROOT / "runs" / run_id).exists()


def test_technical_retry_reuses_context_package_without_retrieval(
    isolated_agentpro_storage,
    monkeypatch,
) -> None:
    project_id = "PROJ-f001-retry"
    book_id = "BOOK-f001-retry"
    run_id = "run-f001-retry"
    step_id = "step-f001-retry"
    repository = _project_repository(project_id, book_id)
    with repository.domain_transaction() as transaction:
        transaction.add_structured_memory_record(
            _fact(
                project_id,
                "FACT-f001-retry-memory",
                book_id,
                "retrieved only for the first provider attempt",
            )
        )

    retrieval_calls = 0
    original_retrieval = ProjectRepository.list_structured_memory_records

    def observed_retrieval(self, *args, **kwargs):
        nonlocal retrieval_calls
        retrieval_calls += 1
        return original_retrieval(self, *args, **kwargs)

    provider_packages: list[dict] = []

    def provider_adapter(payload: dict) -> dict:
        provider_packages.append(json.loads(json.dumps(payload["_context_package"])))
        return {
            "tool": "CRITIC",
            "payload": {
                "SUMMARY": "Technical provider attempt completed.",
                "ISSUES": [],
                "meta": {"requested_model": payload.get("_requested_model")},
            },
        }

    monkeypatch.setattr(
        ProjectRepository,
        "list_structured_memory_records",
        observed_retrieval,
    )
    monkeypatch.setitem(executor.TOOLS, "CRITIC", provider_adapter)
    client = TestClient(app)
    request = {
        "mode": "CRITIC",
        "project_id": project_id,
        "book_id": book_id,
        "run_id": run_id,
        "step_id": step_id,
        "payload": {"text": "Review the observatory scene."},
    }

    first = client.post("/agent/step", json=request)
    assert first.status_code == 200, first.text
    assert retrieval_calls == 1

    retry_request = dict(request)
    retry_request["technical_retry"] = True
    retry = client.post("/agent/step", json=retry_request)
    assert retry.status_code == 200, retry.text
    assert retrieval_calls == 1
    assert len(provider_packages) == 2
    assert provider_packages[0] == provider_packages[1]
    assert (
        first.json()["context_package_id"]
        == retry.json()["context_package_id"]
        == provider_packages[0]["context_package_id"]
    )
    assert first.json()["context_hash"] == retry.json()["context_hash"]

    reopened = _project_repository(project_id, book_id)
    assert len(reopened.list_context_packages()) == 1
    persisted = reopened.get_context_package(provider_packages[0]["context_package_id"])
    assert persisted is not None
    assert persisted.context_hash == provider_packages[0]["context_hash"]

    retry_step = _read_public_json(retry.json()["artifact_paths"][0])
    assert retry_step["context_package_id"] == persisted.context_package_id
    assert retry_step["context_hash"] == persisted.context_hash
    assert not (REPO_ROOT / "books" / book_id).exists()
    assert not (REPO_ROOT / "runs" / run_id).exists()


def test_f007_agent_step_deduplicates_project_series_and_preserves_conflicts(
    isolated_agentpro_storage,
    monkeypatch,
) -> None:
    project_id = "PROJ-f007-api"
    book_id = "BOOK-f007-api"
    series_id = "SERIES-f007-api"
    foreign_series_id = "SERIES-f007-foreign"
    run_id = "run-f007-api"
    exact = _fact(project_id, "FACT-f007-api-exact", book_id, "neutral exact value")
    conflict = _fact(
        project_id,
        "FACT-f007-api-conflict",
        book_id,
        "neutral project value",
    )
    project_only = _fact(
        project_id,
        "FACT-f007-api-project-only",
        book_id,
        "neutral project-only value",
    )
    ensure_test_book_bible(book_id)
    project = _project_repository(project_id, book_id)
    with project.domain_transaction() as transaction:
        for record in (exact, conflict, project_only):
            transaction.add_structured_memory_record(record)

    access = SeriesAccessContext.bind(project_id, series_id)
    series = SeriesRepository(StorageResolver().resolve_series(series_id))
    series.initialize()
    series.register_member(
        access,
        SeriesMembershipRecord(
            series_id=series_id,
            project_id=project_id,
            book_id=book_id,
            source_ref="synthetic-f007-api-membership",
            version=1,
            created_at=STAMP,
        ),
    )

    def save_series_record(
        record: FactRecord,
        *,
        state: dict | None = None,
        operation_id: str,
    ) -> None:
        series.save_series_memory(
            access,
            SeriesStateRecord(
                series_id=series_id,
                state_kind=SeriesStateKind.MEMORY,
                source_project_id=project_id,
                source_book_id=book_id,
                record_type=record.memory_record_type,
                record_id=record.record_id,
                source_version=record.version,
                source_ref=f"SCENE-{operation_id}",
                provenance_refs=(f"SCENE-{operation_id}#artifact",),
                transfer_reason="synthetic F-007 API proof",
                state=state or record.to_dict(),
                operation_id=operation_id,
                version=record.version,
                frozen=record.frozen,
                author_locked=record.author_locked,
                created_at=STAMP,
                updated_at=STAMP,
            ),
        )

    save_series_record(exact, operation_id="series-f007-api-exact")
    changed_state = conflict.to_dict()
    changed_state["object_value"] = "neutral series variant"
    save_series_record(
        conflict,
        state=changed_state,
        operation_id="series-f007-api-conflict",
    )
    series_only = _fact(
        project_id,
        "FACT-f007-api-series-only",
        book_id,
        "neutral series-only value",
    )
    save_series_record(series_only, operation_id="series-f007-api-series-only")

    foreign_project_id = "PROJ-f007-foreign"
    foreign_book_id = "BOOK-f007-foreign"
    foreign_access = SeriesAccessContext.bind(foreign_project_id, foreign_series_id)
    foreign_series = SeriesRepository(
        StorageResolver().resolve_series(foreign_series_id)
    )
    foreign_series.initialize()
    foreign_series.register_member(
        foreign_access,
        SeriesMembershipRecord(
            series_id=foreign_series_id,
            project_id=foreign_project_id,
            book_id=foreign_book_id,
            source_ref="synthetic-f007-foreign-membership",
            version=1,
            created_at=STAMP,
        ),
    )
    foreign_record = _fact(
        foreign_project_id,
        "FACT-f007-api-foreign-series",
        foreign_book_id,
        "foreign series value",
    )
    foreign_series.save_series_memory(
        foreign_access,
        SeriesStateRecord(
            series_id=foreign_series_id,
            state_kind=SeriesStateKind.MEMORY,
            source_project_id=foreign_project_id,
            source_book_id=foreign_book_id,
            record_type=foreign_record.memory_record_type,
            record_id=foreign_record.record_id,
            source_version=foreign_record.version,
            source_ref="SCENE-f007-foreign",
            provenance_refs=("SCENE-f007-foreign#artifact",),
            transfer_reason="foreign series isolation proof",
            state=foreign_record.to_dict(),
            operation_id="series-f007-api-foreign",
            version=1,
            frozen=False,
            author_locked=False,
            created_at=STAMP,
            updated_at=STAMP,
        ),
    )

    foreign_project = _project_repository(foreign_project_id, foreign_book_id)
    with foreign_project.domain_transaction() as transaction:
        transaction.add_structured_memory_record(
            _fact(
                foreign_project_id,
                "FACT-f007-api-foreign-project",
                foreign_book_id,
                "foreign project value",
            )
        )

    provider_packages: list[dict] = []

    def provider_adapter(payload: dict) -> dict:
        provider_packages.append(json.loads(json.dumps(payload["_context_package"])))
        return {
            "tool": "WRITE",
            "payload": {
                "text": (
                    "The neutral test character reviewed the synthetic records, "
                    "noted the unresolved variant, and continued through the "
                    "controlled test scene without changing any user data."
                )
            },
        }

    monkeypatch.setitem(executor.TOOLS, "WRITE", provider_adapter)
    api_request = {
        "mode": "WRITE",
        "project_id": project_id,
        "book_id": book_id,
        "series_id": series_id,
        "run_id": run_id,
        "step_id": "step-f007-api",
        "payload": {"text": "Review the neutral synthetic context."},
    }
    client = TestClient(app)
    response = client.post("/agent/step", json=api_request)
    assert response.status_code == 200, response.text
    package = provider_packages[0]
    items = package["included_items"]

    exact_items = [item for item in items if item["entity_id"] == str(exact.fact_id)]
    assert len(exact_items) == 1
    assert {item["source_scope"] for item in exact_items[0]["provenance"]} == {
        "PROJECT",
        "SERIES",
    }
    project_only_item = next(
        item for item in items if item["entity_id"] == str(project_only.fact_id)
    )
    series_only_item = next(
        item for item in items if item["entity_id"] == str(series_only.fact_id)
    )
    assert {item["source_scope"] for item in project_only_item["provenance"]} == {
        "PROJECT"
    }
    assert {item["source_scope"] for item in series_only_item["provenance"]} == {
        "SERIES"
    }
    assert series_only_item["provenance"][0]["source_project_id"] == project_id
    assert series_only_item["provenance"][0]["source_book_id"] == book_id
    assert str(foreign_record.fact_id) not in {item["entity_id"] for item in items}
    assert "FACT-f007-api-foreign-project" not in {
        item["entity_id"] for item in items
    }

    conflict_items = [
        item for item in items if item["entity_id"] == str(conflict.fact_id)
    ]
    assert len(conflict_items) == 3
    assert sum(item["layer"] == "CONFLICT" for item in conflict_items) == 1
    assert all(item["mandatory"] for item in conflict_items)
    trace = package["selection_summary"]["deduplication_trace"]
    assert any(
        item["action"] == "MERGED_EXACT"
        and item["logical_identity"] == f"FACT:{exact.fact_id}"
        for item in trace
    )
    assert any(
        item["action"] == "PRESERVED_CONFLICT"
        and item["logical_identity"] == f"FACT:{conflict.fact_id}"
        and "different content_hash" in item["not_merged_reasons"]
        for item in trace
    )

    retry_request = dict(api_request)
    retry_request["technical_retry"] = True
    retry = client.post("/agent/step", json=retry_request)
    assert retry.status_code == 200, retry.text
    assert provider_packages[1] == package

    reopened = _project_repository(project_id, book_id)
    persisted = reopened.get_context_package(package["context_package_id"])
    assert persisted is not None
    assert persisted.to_dict() == package
    assert persisted.context_hash == package["context_hash"]
    assert not (REPO_ROOT / "books" / book_id).exists()
    assert not (REPO_ROOT / "runs" / run_id).exists()

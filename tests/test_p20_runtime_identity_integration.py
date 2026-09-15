from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
import app.p20_core.executor as executor
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.domain_records import FactRecord
from app.p20_core.project_repository import (
    ProjectRepository,
    SeriesAccessContext,
    SeriesAccessError,
    SeriesRepository,
    StorageResolver,
    ensure_system_repository,
)
from app.p20_core.series_memory import (
    SeriesMembershipRecord,
    SeriesStateKind,
    SeriesStateRecord,
)
from app.p20_core.storage_paths import get_runs_root, get_storage_root


REPO_ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-09-15T12:00:00Z"


def _project_repository(project_id: str, book_id: str) -> ProjectRepository:
    repository = ProjectRepository(
        StorageResolver().resolve_project(project_id, book_id=book_id)
    )
    repository.initialize()
    return repository


def _fact(project_id: str, book_id: str, suffix: str) -> FactRecord:
    return FactRecord(
        fact_id=f"FACT-f003-{suffix}",
        project_id=project_id,
        subject_id=f"CHAR-f003-{suffix}",
        predicate="knows",
        object_type="TEXT",
        object_id=None,
        object_value=f"project {suffix} private fact",
        reality_status="TRUE",
        verification_status="VERIFIED",
        confidence=1,
        frozen=False,
        author_locked=False,
        valid_from="day-1",
        valid_to=None,
        established_event_id=None,
        established_scene_id=f"SCENE-f003-{suffix}",
        source_artifact_ref=f"books/{book_id}/chapters/source.json",
        source_refs=(f"SCENE-f003-{suffix}#artifact",),
        canon_version=1,
        version=1,
        created_at=STAMP,
        updated_at=STAMP,
    )


def _seed_project(project_id: str, book_id: str, suffix: str) -> ProjectRepository:
    repository = _project_repository(project_id, book_id)
    with repository.domain_transaction() as transaction:
        transaction.add_structured_memory_record(_fact(project_id, book_id, suffix))
    return repository


def _seed_series(
    project_id: str,
    book_id: str,
    series_id: str,
    suffix: str,
) -> SeriesRepository:
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
            record_id=f"FACT-f003-series-{suffix}",
            source_version=1,
            source_ref=f"SCENE-f003-series-{suffix}",
            provenance_refs=(f"SCENE-f003-series-{suffix}#artifact",),
            transfer_reason="required series continuity",
            state={"value": f"series {suffix} private fact", "importance": 1},
            operation_id=f"series-f003-{suffix}",
            version=1,
            frozen=False,
            author_locked=False,
            created_at=STAMP,
            updated_at=STAMP,
        ),
    )
    return repository


def _read_public_json(path: str) -> dict:
    public_path = Path(path)
    assert not public_path.is_absolute()
    return json.loads((get_storage_root() / public_path).read_text(encoding="utf-8"))


def _request(
    *,
    project_id: str | None,
    book_id: str,
    series_id: str | None,
    run_id: str,
    step_id: str,
    resume: bool = False,
    mode: str = "CRITIC",
) -> dict:
    request = {
        "mode": mode,
        "book_id": book_id,
        "run_id": run_id,
        "step_id": step_id,
        "resume": resume,
        "payload": {"text": f"Review {book_id}."},
    }
    if project_id is not None:
        request["project_id"] = project_id
    if series_id is not None:
        request["series_id"] = series_id
    return request


def _provider_result(payload: dict) -> dict:
    return {
        "tool": "CRITIC",
        "payload": {
            "SUMMARY": f"Reviewed {payload['project_id']}.",
            "ISSUES": [],
        },
    }


def _writer_result(payload: dict) -> dict:
    return {
        "tool": "WRITE",
        "payload": {
            "text": f"A focused scene for {payload['project_id']}.",
        },
    }


def test_explicit_project_book_and_series_identity_reaches_every_runtime_boundary(
    isolated_agentpro_storage,
    monkeypatch,
) -> None:
    identities = {
        "a": ("PROJ-f003-a", "BOOK-f003-a", "SERIES-f003-a"),
        "b": ("PROJ-f003-b", "BOOK-f003-b", "SERIES-f003-b"),
        "c": ("PROJ-f003-c", "BOOK-f003-c", None),
    }
    project_repositories = {
        key: _seed_project(project_id, book_id, key)
        for key, (project_id, book_id, _series_id) in identities.items()
    }
    series_repositories = {
        key: _seed_series(project_id, book_id, series_id, key)
        for key, (project_id, book_id, series_id) in identities.items()
        if series_id is not None
    }
    for _project_id, book_id, _series_id in identities.values():
        ensure_test_book_bible(book_id)

    provider_inputs: list[dict] = []

    def provider_adapter(payload: dict) -> dict:
        provider_inputs.append(json.loads(json.dumps(payload)))
        return _writer_result(payload)

    monkeypatch.setitem(executor.TOOLS, "WRITE", provider_adapter)
    client = TestClient(app)
    responses: dict[str, dict] = {}

    for key, (project_id, book_id, series_id) in identities.items():
        response = client.post(
            "/agent/step",
            json=_request(
                project_id=project_id,
                book_id=book_id,
                series_id=series_id,
                run_id=f"run-f003-{key}",
                step_id=f"step-f003-{key}",
                mode="WRITE",
            ),
        )
        assert response.status_code == 200, response.text
        responses[key] = response.json()

    assert len(provider_inputs) == 3
    for index, key in enumerate(("a", "b", "c")):
        project_id, book_id, series_id = identities[key]
        run_id = f"run-f003-{key}"
        base_step_id = f"step-f003-{key}"
        data = responses[key]
        provider_input = provider_inputs[index]
        package = provider_input["_context_package"]

        assert data["project_id"] == project_id
        assert data["book_id"] == book_id
        assert data["domain_book_id"] == book_id
        assert data["series_id"] == series_id
        assert data["run_id"] == run_id
        assert data["step_id"] == base_step_id

        assert provider_input["project_id"] == project_id
        assert provider_input["domain_book_id"] == book_id
        assert provider_input["series_id"] == series_id
        assert provider_input["step_id"] == f"{base_step_id}:001:WRITE"
        assert package["project_id"] == project_id
        assert package["book_id"] == book_id
        assert package["series_id"] == series_id
        assert package["run_id"] == run_id
        assert package["step_id"] == f"{base_step_id}:001:WRITE"

        included_ids = {item["entity_id"] for item in package["included_items"]}
        assert f"FACT-f003-{key}" in included_ids
        for foreign_key in {"a", "b", "c"} - {key}:
            assert f"FACT-f003-{foreign_key}" not in included_ids
            assert f"FACT-f003-series-{foreign_key}" not in included_ids
        if series_id is None:
            assert not any(
                item["layer"] == "SERIES_MEMORY"
                for item in package["included_items"]
            )
        else:
            assert f"FACT-f003-series-{key}" in included_ids

        step = _read_public_json(data["artifact_paths"][0])
        run_state = json.loads(
            (get_runs_root() / run_id / "run_state.json").read_text(encoding="utf-8")
        )
        executor_state = json.loads(
            (get_runs_root() / run_id / "state.json").read_text(encoding="utf-8")
        )
        audit = json.loads(
            (get_runs_root() / run_id / "audit.json").read_text(encoding="utf-8")
        )
        sequence = json.loads(
            (get_runs_root() / run_id / "steps" / "000_SEQUENCE.json").read_text(
                encoding="utf-8"
            )
        )
        for record in (step, run_state, executor_state, audit, sequence):
            assert record["project_id"] == project_id
            assert record.get("domain_book_id", record.get("book_id")) == book_id
            assert record["series_id"] == series_id
        assert step["step_id"] == f"{base_step_id}:001:WRITE"
        assert run_state["step_id"] == base_step_id
        assert audit["step_id"] == base_step_id
        chapter = _read_public_json(data["chapter_path"])
        assert chapter["project_id"] == project_id
        assert chapter["series_id"] == series_id

        assert not (REPO_ROOT / "books" / book_id).exists()
        assert not (REPO_ROOT / "runs" / run_id).exists()

    del project_repositories
    del series_repositories

    registry = ensure_system_repository()
    for key, (project_id, book_id, series_id) in identities.items():
        assert registry.resolve_project_id_for_book(book_id) == project_id
        reopened_project = _project_repository(project_id, book_id)
        assert reopened_project.get_project_identity()["project_id"] == project_id
        assert reopened_project.get_project_identity()["book_id"] == book_id
        if series_id is not None:
            reopened_series = SeriesRepository(StorageResolver().resolve_series(series_id))
            membership = reopened_series.require_registered_member(
                SeriesAccessContext.bind(project_id, series_id),
                book_id=book_id,
            )
            assert str(membership.project_id) == project_id
            assert str(membership.book_id) == book_id


def test_cross_project_cross_series_and_resume_identity_are_rejected(
    isolated_agentpro_storage,
    monkeypatch,
) -> None:
    del isolated_agentpro_storage
    project_a, book_a, series_a = "PROJ-f003-negative-a", "BOOK-f003-negative-a", "SERIES-f003-negative-a"
    project_b, book_b, series_b = "PROJ-f003-negative-b", "BOOK-f003-negative-b", "SERIES-f003-negative-b"
    _seed_project(project_a, book_a, "negative-a")
    _seed_project(project_b, book_b, "negative-b")
    series_repository_a = _seed_series(project_a, book_a, series_a, "negative-a")
    _seed_series(project_b, book_b, series_b, "negative-b")

    provider_calls = 0

    def provider_adapter(payload: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        return _provider_result(payload)

    monkeypatch.setitem(executor.TOOLS, "CRITIC", provider_adapter)
    client = TestClient(app)

    mismatch = client.post(
        "/agent/step",
        json=_request(
            project_id=project_b,
            book_id=book_a,
            series_id=None,
            run_id="run-f003-project-mismatch",
            step_id="step-f003-project-mismatch",
        ),
    )
    assert mismatch.status_code == 422
    assert "project" in mismatch.json()["detail"]

    cross_series = client.post(
        "/agent/step",
        json=_request(
            project_id=project_a,
            book_id=book_a,
            series_id=series_b,
            run_id="run-f003-series-mismatch",
            step_id="step-f003-series-mismatch",
        ),
    )
    assert cross_series.status_code == 422
    assert "series" in cross_series.json()["detail"].lower()

    missing_series = client.post(
        "/agent/step",
        json=_request(
            project_id=project_a,
            book_id=book_a,
            series_id=None,
            run_id="run-f003-series-required",
            step_id="step-f003-series-required",
        ),
    )
    assert missing_series.status_code == 422
    assert "series_id is required" in missing_series.json()["detail"]

    with pytest.raises(SeriesAccessError):
        series_repository_a.list_series_memory(
            SeriesAccessContext.bind(project_b, series_a)
        )

    resume_project_a = "PROJ-f003-resume-a"
    resume_book_a = "BOOK-f003-resume-a"
    resume_project_b = "PROJ-f003-resume-b"
    resume_book_b = "BOOK-f003-resume-b"
    _seed_project(resume_project_a, resume_book_a, "resume-a")
    _seed_project(resume_project_b, resume_book_b, "resume-b")
    first = client.post(
        "/agent/step",
        json=_request(
            project_id=resume_project_a,
            book_id=resume_book_a,
            series_id=None,
            run_id="run-f003-resume",
            step_id="step-f003-resume-a",
        ),
    )
    assert first.status_code == 200, first.text
    mismatched_resume = client.post(
        "/agent/step",
        json=_request(
            project_id=resume_project_b,
            book_id=resume_book_b,
            series_id=None,
            run_id="run-f003-resume",
            step_id="step-f003-resume-b",
            resume=True,
        ),
    )
    assert mismatched_resume.status_code == 422
    assert "different project_id" in mismatched_resume.json()["detail"]
    assert provider_calls == 1


def test_legacy_book_request_uses_persisted_binding_and_rejects_ambiguity(
    isolated_agentpro_storage,
    monkeypatch,
) -> None:
    del isolated_agentpro_storage
    provider_inputs: list[dict] = []

    def provider_adapter(payload: dict) -> dict:
        provider_inputs.append(json.loads(json.dumps(payload)))
        return _provider_result(payload)

    monkeypatch.setitem(executor.TOOLS, "CRITIC", provider_adapter)
    client = TestClient(app)
    legacy_book = "BOOK-f003-legacy"

    first = client.post(
        "/agent/step",
        json=_request(
            project_id=None,
            book_id=legacy_book,
            series_id=None,
            run_id="run-f003-legacy-one",
            step_id="step-f003-legacy-one",
        ),
    )
    second = client.post(
        "/agent/step",
        json=_request(
            project_id=None,
            book_id=legacy_book,
            series_id=None,
            run_id="run-f003-legacy-two",
            step_id="step-f003-legacy-two",
        ),
    )
    assert first.status_code == second.status_code == 200
    resolved_project = first.json()["project_id"]
    assert resolved_project.startswith("PROJ-")
    assert resolved_project != legacy_book
    assert second.json()["project_id"] == resolved_project
    assert provider_inputs[0]["project_id"] == resolved_project
    assert provider_inputs[1]["project_id"] == resolved_project
    assert ensure_system_repository().resolve_project_id_for_book(legacy_book) == resolved_project

    ambiguous_book = "BOOK-f003-ambiguous"
    _project_repository("PROJ-f003-ambiguous-a", ambiguous_book)
    _project_repository("PROJ-f003-ambiguous-b", ambiguous_book)
    ambiguous = client.post(
        "/agent/step",
        json=_request(
            project_id=None,
            book_id=ambiguous_book,
            series_id=None,
            run_id="run-f003-ambiguous",
            step_id="step-f003-ambiguous",
        ),
    )
    assert ambiguous.status_code == 422
    assert "more than one persisted project" in ambiguous.json()["detail"]
    assert len(provider_inputs) == 2
    assert not (REPO_ROOT / "books" / legacy_book).exists()
    assert not (REPO_ROOT / "runs" / "run-f003-legacy-one").exists()

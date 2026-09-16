from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.operator_dpapi import read_secret
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.domain_records import EdgeRecord, FactRecord
from app.p20_core.local_operator import initialize_operator, rotate_operator
from app.p20_core.project_repository import (
    ProjectRepository, SeriesAccessContext, SeriesRepository, StorageResolver,
    ensure_system_repository,
)
from app.p20_core.series_memory import (
    SeriesMembershipRecord, SeriesStateKind, SeriesStateRecord,
)
import app.tools as tools


PROJECT = "PROJ-series-canonical"
BOOK = "BOOK-series-canonical"
SERIES = "SERIES-canonical"
FACT = "FACT-series-canonical"
STAMP = "2026-09-16T00:00:00Z"


def fact(*, version=1, protected=False, source=None, value="series state",
         project=PROJECT, identity=FACT):
    source = source or {
        "scene_id": "SCENE-series-source",
        "artifact_ref": "synthetic:series-source",
    }
    return FactRecord(
        fact_id=identity, project_id=project, subject_id=identity,
        predicate="description", object_type="TEXT", object_id=None,
        object_value=value, reality_status="TRUE", verification_status="VERIFIED",
        confidence=1, frozen=protected, author_locked=protected,
        valid_from="test-start", valid_to=None, established_event_id=None,
        established_scene_id=source["scene_id"],
        source_artifact_ref=source["artifact_ref"], source_refs=(source["scene_id"],),
        canon_version=version, version=version, created_at=STAMP, updated_at=STAMP,
    )


def membership(series_id=SERIES, project_id=PROJECT, book_id=BOOK):
    return SeriesMembershipRecord(
        series_id=series_id, project_id=project_id, book_id=book_id,
        source_ref="synthetic:series-membership", version=1, created_at=STAMP,
    )


def seed_series_canon(repository, access, record):
    state = record.to_dict()
    identity = str(record.record_id)
    wrapper = SeriesStateRecord(
        series_id=access.series_id, state_kind=SeriesStateKind.CANON,
        source_project_id=access.project_id, source_book_id=BOOK,
        record_type="FACT", record_id=identity, source_version=state["version"],
        source_ref="synthetic:series-seed", provenance_refs=("synthetic:series-seed",),
        transfer_reason="SYNTHETIC_CANON_SEED", state=state,
        operation_id="series-seed-" + hashlib.sha256(
            f"{identity}|{state['version']}".encode("utf-8")
        ).hexdigest(), version=state["version"],
        frozen=bool(state.get("frozen", True)),
        author_locked=bool(state.get("author_locked", True)),
        created_at=STAMP, updated_at=STAMP,
    )
    repository.save_series_canon(access, wrapper)


@pytest.fixture
def series_pipeline(isolated_agentpro_storage, monkeypatch, tmp_path):
    ensure_test_book_bible(BOOK)
    system = ensure_system_repository()
    system.bind_project(PROJECT, BOOK)
    project_repository = ProjectRepository(
        StorageResolver().resolve_project(PROJECT, book_id=BOOK)
    )
    project_repository.initialize()
    access = SeriesAccessContext.bind(PROJECT, SERIES)
    repository = SeriesRepository(StorageResolver().resolve_series(SERIES))
    repository.initialize()
    repository.register_member(access, membership())

    secret = tmp_path / "series-operator" / "operator.dpapi"
    initialize_operator(system, secret)
    token = read_secret(secret)
    controls = {
        "version": 1,
        "protected": False,
        "verdict": "ACCEPT",
        "calls": [],
        "value": "accepted synthetic series state",
    }

    from openai.resources.responses.responses import Responses

    def create(_self, **kwargs):
        prompt = json.loads(kwargs["input"])
        controls["calls"].append(prompt)
        task = next(
            item for item in prompt["context_package"]["included_items"]
            if item["layer"] == "TASK"
        )
        payload = json.loads(task["content"])["input"]
        if payload["role"] == "EXTRACTOR":
            record = fact(
                version=controls["version"], protected=controls["protected"],
                source=payload["source"], value=controls["value"],
            )
            output = {"records": [{"record_type": "FACT", "payload": record.to_dict()}]}
        else:
            output = {
                "precision_status": controls["verdict"],
                "completeness_status": controls["verdict"],
            }
        return SimpleNamespace(
            output_text=json.dumps(output), model=kwargs["model"], output=[],
        )

    monkeypatch.setattr(Responses, "create", create)
    monkeypatch.setitem(tools.TOOLS, "WRITE", lambda payload: {
        "tool": "WRITE",
        "payload": {"text": "A neutral synthetic series observation is accepted."},
    })
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 52000),
    ) as client:
        yield {
            "client": client,
            "series": repository,
            "access": access,
            "project": project_repository,
            "controls": controls,
            "headers": {"Authorization": "Bearer " + token},
            "system": system,
            "secret": secret,
            "storage": isolated_agentpro_storage,
        }


def step(env, *, run="run-series", step_id="step-series", retry=False,
         series_id=SERIES):
    response = env["client"].post("/agent/step", json={
        "mode": "WRITE", "project_id": PROJECT, "book_id": BOOK,
        "series_id": series_id, "run_id": run, "step_id": step_id,
        "payload": {
            "text": "Write neutral synthetic series data.",
            "technical_retry": retry,
            "scope_type": "SERIES",
        },
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["quality_gate"]["decision"] == "ACCEPT", data
    return data["canonical_change"]


def proposal_record(env, change):
    return json.loads(env["series"].get_metadata(
        "canonical_proposal.v1:" + change["proposal_id"]
    ))["versions"]["1"]


def base(change):
    return f"/operator/projects/{PROJECT}/proposals/{change['proposal_id']}"


def approve(env, change):
    response = env["client"].post(base(change) + "/review", headers=env["headers"])
    assert response.status_code == 200, response.text
    review = response.json()
    request = {
        key: review["proposal"][key]
        for key in ("proposal_hash", "scope_type", "scope_id")
    }
    request.update(challenge_id=review["challenge"]["challenge_id"], decision="APPROVE")
    response = env["client"].post(
        base(change) + "/decision", headers=env["headers"], json=request,
    )
    assert response.status_code == 200, response.text
    return response.json()


def commit(env, change, *, headers=None):
    return env["client"].post(
        base(change) + "/commit", headers=env["headers"] if headers is None else headers,
        json={"proposal_hash": change["proposal_hash"]},
    )


def add_impact_edge(env, edge_id="series-impact", source="SCENE-series-dependent"):
    edge = EdgeRecord(
        edge_id=edge_id, scope_type="SERIES", scope_id=SERIES,
        source_type=source.split("-", 1)[0], source_id=source,
        relation_type="REQUIRES", target_type="FACT", target_id=FACT,
        valid_from=None, valid_to=None, confidence=1,
        source_ref="synthetic:series-impact", version=1,
    )
    with env["series"].domain_transaction(env["access"]) as transaction:
        transaction.add_edge(
            edge, source_scope=env["series"].scope,
            target_scope=env["series"].scope,
        )


def test_series_api_commit_reopen_retry_isolation_and_local_audit(series_pipeline):
    env = series_pipeline
    change = step(env)
    assert change["status"] == "COMMITTED" and change["canonical_commit"] is True
    assert change["scope_type"] == "SERIES" and change["scope_id"] == SERIES
    assert change["authorization_ref"] is None
    assert env["project"].list_structured_memory_records() == {}
    cross_store = env["project"].list_cross_store_operations()
    assert cross_store  # The accepted WRITE artifact is a real file boundary.
    assert all(item["operation_type"] != "SERIES_CANONICAL_CHANGE" for item in cross_store)
    reopened = SeriesRepository(StorageResolver().resolve_series(SERIES))
    current = reopened.list_series_canon(env["access"])
    assert len(current) == 1 and current[0].state["version"] == 1
    assert reopened.get_metadata("canonical_commit.v1:" + change["operation_id"])
    replay = step(env, retry=True)
    assert replay == change
    assert len(env["controls"]["calls"]) == 2

    env["controls"].update(version=2, value="second accepted synthetic series state")
    second = step(env, run="run-series-next", step_id="step-series-next")
    assert second["canonical_commit"] is True
    assert reopened.list_series_canon(env["access"])[0].state["version"] == 2
    assert "accepted synthetic series state" in json.dumps(
        env["controls"]["calls"][2]["context_package"]
    )

    other_id = "SERIES-canonical-other"
    other_access = SeriesAccessContext.bind(PROJECT, other_id)
    other = SeriesRepository(StorageResolver().resolve_series(other_id))
    other.initialize()
    other.register_member(other_access, membership(series_id=other_id))
    assert other.list_series_canon(other_access) == ()
    assert other.list_edges(other_access) == ()


def test_protected_series_change_impact_approval_final_guard_and_reopen(series_pipeline):
    env = series_pipeline
    seed_series_canon(env["series"], env["access"], fact(protected=True))
    add_impact_edge(env)
    env["controls"].update(version=2, protected=True)
    change = step(env)
    assert change["status"] == "AWAITING_USER_APPROVAL"
    assert env["series"].list_series_canon(env["access"])[0].state["version"] == 1
    record = proposal_record(env, change)
    assert record["impact"]["coverage"] == "BOUNDED_SERIES_GRAPH"
    assert record["impact"]["result"][0]["impacts"][0]["entity_id"] == "SCENE-series-dependent"
    assert record["initial_guard"]["outcome"] == "REQUIRE_USER_APPROVAL"
    evidence = approve(env, change)
    assert evidence["scope_type"] == "SERIES"
    assert evidence["scope_id"] == SERIES
    assert evidence["proposal_hash"] == change["proposal_hash"]
    assert evidence["authorization_ref"]
    receipt = commit(env, change).json()
    assert receipt["canonical_commit"] is True
    assert receipt["authorization_ref"] == evidence["authorization_ref"]
    saved = proposal_record(env, change)
    assert saved["final_guard"]["outcome"] == "ALLOW"
    assert commit(env, change).json() == receipt
    reopened = SeriesRepository(StorageResolver().resolve_series(SERIES))
    assert reopened.list_series_canon(env["access"])[0].state["version"] == 2
    versions = json.loads(reopened.get_metadata("canonical_versions.v1:FACT:" + FACT))
    assert [item["version"] for item in versions] == [1, 2]


@pytest.mark.parametrize("alteration", ["record", "graph", "credential"])
def test_series_stale_proposal_and_approval_never_commit(series_pipeline, alteration):
    env = series_pipeline
    seed_series_canon(env["series"], env["access"], fact(protected=True))
    env["controls"].update(version=2, protected=True)
    change = step(env)
    approve(env, change)
    if alteration == "record":
        seed_series_canon(
            env["series"], env["access"],
            fact(identity="FACT-series-concurrent", value="concurrent synthetic state"),
        )
        response = commit(env, change)
        assert response.status_code == 200
        assert response.json()["status"] == "STALE"
    elif alteration == "graph":
        add_impact_edge(env, edge_id="series-stale-edge", source="SCENE-series-stale")
        response = commit(env, change)
        assert response.status_code == 200
        assert response.json()["status"] == "STALE"
    else:
        rotate_operator(env["system"], env["secret"])
        assert commit(env, change).status_code == 401
        fresh_headers = {"Authorization": "Bearer " + read_secret(env["secret"])}
        assert commit(env, change, headers=fresh_headers).status_code == 403
    assert env["series"].list_series_canon(env["access"])[0].state["version"] == 1


def test_series_missing_impact_blocks_final_guard_without_mutation(series_pipeline):
    env = series_pipeline
    seed_series_canon(env["series"], env["access"], fact(protected=True))
    env["controls"].update(version=2, protected=True)
    change = step(env)
    approve(env, change)
    with env["series"].canonical_proposal_transaction(
        env["access"], change["proposal_id"],
    ) as (document, _snapshot):
        del document["versions"]["1"]["impact"]
    response = commit(env, change)
    assert response.status_code == 409
    assert response.json()["detail"] == "REVIEW_EVIDENCE_MISSING"
    assert env["series"].list_series_canon(env["access"])[0].state["version"] == 1


def test_series_commit_audit_failure_rolls_back_state_history_and_receipt(series_pipeline):
    env = series_pipeline
    with env["series"].connect() as connection:
        connection.execute(
            "CREATE TRIGGER reject_series_commit_audit BEFORE INSERT ON series_metadata "
            "WHEN NEW.key LIKE 'canonical_commit.v1:%' "
            "BEGIN SELECT RAISE(ABORT, 'synthetic series audit failure'); END"
        )
    failed = step(env, run="run-series-audit-failure", step_id="step-series-audit-failure")
    assert failed["status"] == "FAILED"
    assert env["series"].list_series_canon(env["access"]) == ()
    assert env["series"].get_metadata("canonical_versions.v1:FACT:" + FACT) is None
    assert not any(
        key.startswith("canonical_commit.v1:")
        for key in env["series"].list_metadata()
    )


def test_series_legacy_unknown_and_derived_remain_fail_closed(series_pipeline):
    env = series_pipeline
    legacy = fact(protected=True).to_dict()
    legacy.pop("frozen")
    legacy.pop("author_locked")
    wrapper = SeriesStateRecord(
        series_id=SERIES, state_kind=SeriesStateKind.CANON,
        source_project_id=PROJECT, source_book_id=BOOK, record_type="FACT",
        record_id=FACT, source_version=1, source_ref="synthetic:legacy",
        provenance_refs=("synthetic:legacy",), transfer_reason="SYNTHETIC_LEGACY",
        state=legacy, operation_id="series-legacy-seed", version=1,
        frozen=True, author_locked=True, created_at=STAMP, updated_at=STAMP,
    )
    env["series"].save_series_canon(env["access"], wrapper)
    env["controls"].update(version=2, protected=True)
    rejected = step(env)
    assert rejected["status"] == "REJECTED"
    assert proposal_record(env, rejected)["initial_guard"]["reason"] == "CURRENT_PROTECTION_UNKNOWN"
    assert "frozen" not in env["series"].list_series_canon(env["access"])[0].state

    add_impact_edge(env, edge_id="series-derived", source="CONTEXT-series-derived")
    derived = step(env, run="run-series-derived", step_id="step-series-derived")
    assert derived["reason"] == "DERIVED_REBUILD_UNSUPPORTED"
    assert env["series"].list_series_canon(env["access"])[0].state["version"] == 1


def test_series_foreign_membership_and_scope_do_not_fallback_to_project(series_pipeline):
    env = series_pipeline
    response = env["client"].post("/agent/step", json={
        "mode": "WRITE", "project_id": PROJECT, "book_id": BOOK,
        "series_id": "SERIES-foreign", "run_id": "run-series-foreign",
        "step_id": "step-series-foreign", "payload": {
            "text": "Write neutral synthetic series data.", "scope_type": "SERIES",
        },
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "project is not a registered member of requested series_id"
    assert env["project"].list_structured_memory_records() == {}
    assert not any(
        key.startswith("canonical_proposal.v1:")
        for key in env["project"].list_metadata()
    )

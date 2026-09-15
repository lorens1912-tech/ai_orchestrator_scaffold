from __future__ import annotations

import json
from dataclasses import replace

import pytest
import app.tools as tools

from tests.test_canonical_pipeline import pipeline, step, approve, commit, proposal_record, base, PROJECT, BOOK
from tests.test_p20_memory_extraction_integrity import (
    _fact, _character_state, _event, _knowledge, _thread, _setup, _payoff, _relationship_change,
)
from app.p20_core.project_repository import ProjectRepository, StorageResolver
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.domain_records import EdgeRecord
from app.p20_core.memory_extraction import SUPPORTED_MEMORY_RECORD_TYPES, decode_memory_entities


def mixed_records(payload, version, protected=False):
    source = payload["source"]
    scene, artifact = source["scene_id"], source["artifact_ref"]
    common = dict(project_id=payload["project_id"], version=version, frozen=protected, author_locked=protected)
    sourced = dict(common, source_scene_id=scene, source_artifact_ref=artifact)
    entities = [
        _fact(**common, established_scene_id=scene, source_artifact_ref=artifact,
              source_refs=[scene], object_value=f"neutral mixed state version {version}"),
        _character_state(**sourced),
        _character_state(**sourced, state_id="CONTEXT-gap008-bo-state", character_id="CHAR-bo"),
        _event(**sourced),
        _knowledge(**common, learned_at_scene_id=scene, source_ref=scene),
        _thread(**common, opened_scene_id=scene, actual_payoff_ref="PAYOFF-gap008-marker"),
        _setup(**common, created_scene_id=scene, actual_payoff_scene_id=scene),
        _payoff(**common, completed_scene_id=scene, source_artifact_ref=artifact),
        _relationship_change(**sourced),
    ]
    return [{"record_type": entity.memory_record_type, "payload": entity.to_dict()} for entity in entities]


def configure(controls, *, protected=False):
    controls["records"] = lambda records, payload: mixed_records(payload, controls["version"], protected)


def test_all_project_types_mixed_commit_operator_reopen_retry_and_isolation(pipeline, isolated_agentpro_storage, monkeypatch):
    client, repo, controls, headers, _, _ = pipeline
    write_inputs = []
    write_adapter = tools.TOOLS["WRITE"]
    def write(payload):
        write_inputs.append(payload)
        return write_adapter(payload)
    monkeypatch.setitem(tools.TOOLS, "WRITE", write)
    configure(controls, protected=True)
    first = step(client)["canonical_change"]
    assert first["status"] == "COMMITTED", first
    assert len(first["resulting_versions"]) == 9
    initial = proposal_record(repo, first)
    assert {m["target_entity_type"] for m in initial["proposal"]["proposed_mutations"]} == SUPPORTED_MEMORY_RECORD_TYPES
    controls["version"] = 2
    second = step(client, run="run-mixed-update", step_id="step-mixed-update")["canonical_change"]
    assert second["status"] == "AWAITING_USER_APPROVAL", second
    assert {json.loads(v)["version"] for v in repo.list_structured_memory_records().values()} == {1}
    assert commit(client, second, headers).json()["canonical_commit"] is False
    decision, _ = approve(client, second, headers)
    receipt = commit(client, second, headers).json()
    assert receipt["canonical_commit"] is True, receipt
    assert receipt["authorization_ref"] == decision["authorization_ref"]
    assert set(receipt["resulting_versions"].values()) == {2}
    reopened = ProjectRepository(StorageResolver().resolve_project(PROJECT, book_id=BOOK))
    assert reopened.list_structured_memory_records() == repo.list_structured_memory_records()
    assert commit(client, second, headers).json() == receipt
    calls_before = len(controls["calls"])
    assert step(client, run="run-mixed-update", step_id="step-mixed-update", retry=True)["canonical_change"] == receipt
    assert len(controls["calls"]) == calls_before
    for mutation in initial["proposal"]["proposed_mutations"]:
        history = json.loads(repo.get_metadata("canonical_versions.v1:" + mutation["target_entity_type"] + ":" + mutation["target_entity_id"]))
        assert [v["version"] for v in history] == [1, 2]
        assert all(v["frozen"] and v["author_locked"] for v in history)
    controls["version"] = 3
    step(client, run="run-mixed-next", step_id="step-mixed-next")
    assert "neutral mixed state version 2" in json.dumps(write_inputs[-1])
    context = json.dumps(controls["calls"][-2]["_context_package"])
    assert "neutral mixed state version 2" in context
    assert "raw importance retained; unit-scale ranking unavailable" in context
    assert json.loads(repo.list_structured_memory_records()["SETUP-gap008-marker"])["importance"] == 7
    foreign_project, foreign_book = "PROJ-mixed-foreign", "BOOK-mixed-foreign"
    ensure_test_book_bible(foreign_book)
    controls["version"] = 1
    foreign = step(client, project=foreign_project, book=foreign_book, run="run-mixed-foreign", step_id="step-mixed-foreign")
    assert foreign["canonical_change"]["canonical_commit"] is True, foreign
    assert "neutral mixed state version 2" not in json.dumps(controls["calls"][-2]["_context_package"])
    assert "neutral mixed state version 2" not in json.dumps(write_inputs[-1])
    (isolated_agentpro_storage / "mixed-project-proof.json").write_text(json.dumps({
        "create": initial, "update": proposal_record(repo, second), "receipt": receipt,
        "record_types": sorted(SUPPORTED_MEMORY_RECORD_TYPES), "record_count": 9,
        "reopen_retry_isolation": True}, indent=2), encoding="utf-8")


@pytest.mark.parametrize("defect", ["missing_reference", "invalid_time", "missing_protection", "cross_project"])
def test_mixed_invalid_element_never_partially_promotes(pipeline, defect):
    client, repo, controls, _, _, _ = pipeline
    def records(_, payload):
        result = mixed_records(payload, 1)
        by_type = {r["record_type"]: r["payload"] for r in result}
        if defect == "missing_reference":
            by_type["PAYOFF"]["setup_id"] = "SETUP-not-present"
        elif defect == "invalid_time":
            by_type["EVENT"]["time_end"] = "day-0"
        elif defect == "missing_protection":
            del by_type["RELATIONSHIP_CHANGE"]["author_locked"]
        else:
            by_type["KNOWLEDGE_EVENT"]["project_id"] = "PROJ-foreign"
        return result
    controls["records"] = records
    result = step(client)["canonical_change"]
    assert result["canonical_commit"] is False, result
    assert repo.list_structured_memory_records() == {}
    assert not any(k.startswith("canonical_versions") for k in repo.list_metadata())


def test_mixed_operator_rejection_preserves_whole_set(pipeline):
    client, repo, controls, headers, _, _ = pipeline
    configure(controls, protected=True)
    assert step(client)["canonical_change"]["canonical_commit"] is True
    before = repo.list_structured_memory_records()
    controls["version"] = 2
    change = step(client, run="run-mixed-reject", step_id="step-mixed-reject")["canonical_change"]
    review = client.post(base(change) + "/review", headers=headers).json()
    body = {k: review["proposal"][k] for k in ("proposal_hash", "scope_type", "scope_id")}
    body.update(challenge_id=review["challenge"]["challenge_id"], decision="REJECT")
    assert client.post(base(change) + "/decision", headers=headers, json=body).status_code == 200
    assert commit(client, change, headers).json()["status"] == "REJECTED"
    assert repo.list_structured_memory_records() == before


def test_mixed_guard_denial_preserves_whole_set(pipeline):
    client, repo, controls, _, _, _ = pipeline
    configure(controls, protected=True)
    assert step(client)["canonical_change"]["canonical_commit"] is True
    before = repo.list_structured_memory_records()
    controls["version"] = 2
    configure(controls, protected=False)
    denial = step(client, run="run-mixed-deny", step_id="step-mixed-deny")["canonical_change"]
    assert denial["status"] == "REJECTED", denial
    assert repo.list_structured_memory_records() == before


def test_mixed_sql_failure_rolls_back_every_record_and_history(pipeline):
    client, repo, controls, _, _, _ = pipeline
    configure(controls)
    with repo.connect() as conn:
        conn.execute("CREATE TRIGGER abort_mixed_audit BEFORE INSERT ON project_metadata "
                     "WHEN NEW.key LIKE 'canonical_commit.v1:%' BEGIN SELECT RAISE(ABORT, 'test audit failure'); END")
    result = step(client)["canonical_change"]
    assert result["status"] == "FAILED", result
    assert repo.list_structured_memory_records() == {}
    assert not any(k.startswith(("canonical_versions", "canonical_commit")) for k in repo.list_metadata())


def test_legacy_unknown_protection_and_derived_rebuild_remain_blocked(pipeline):
    client, repo, controls, _, _, _ = pipeline
    old = _event(project_id=PROJECT)
    assert "frozen" not in old.to_dict() and "author_locked" not in old.to_dict()
    with repo.domain_transaction() as tx:
        tx.add_structured_memory_record(old)
    configure(controls)
    controls["version"] = 2
    result = step(client)["canonical_change"]
    assert result["status"] == "REJECTED", result
    assert proposal_record(repo, result)["initial_guard"]["reason"] == "CURRENT_PROTECTION_UNKNOWN"
    assert list(repo.list_structured_memory_records()) == [str(old.record_id)]
    with repo.domain_transaction() as tx:
        tx.add_edge(EdgeRecord(edge_id="edge-derived-mixed", scope_type="PROJECT", scope_id=PROJECT,
            source_type="CONTEXT", source_id="CONTEXT-derived", relation_type="REQUIRES",
            target_type="EVENT", target_id=str(old.record_id), valid_from=None, valid_to=None,
            confidence=1, source_ref="neutral", version=1), source_scope=repo.scope, target_scope=repo.scope)
    result = step(client, run="run-mixed-derived", step_id="step-mixed-derived")["canonical_change"]
    assert result["reason"] == "DERIVED_REBUILD_UNSUPPORTED", result
    assert list(repo.list_structured_memory_records()) == [str(old.record_id)]


@pytest.mark.parametrize("kind", sorted(SUPPORTED_MEMORY_RECORD_TYPES - {"FACT"}))
def test_added_protection_fields_validate_without_changing_legacy_serialization(kind):
    payload = {"source": {"scene_id": "SCENE-check", "artifact_ref": "neutral:source"}, "project_id": PROJECT}
    record = next(r for r in mixed_records(payload, 1) if r["record_type"] == kind)
    decoded = decode_memory_entities([record])[0]
    assert decoded.to_dict()["frozen"] is False
    for flag in ("frozen", "author_locked"):
        with pytest.raises(ValueError):
            replace(decoded, **{flag: "false"})
    legacy = replace(decoded, frozen=None, author_locked=None)
    legacy_payload = legacy.to_dict()
    assert "frozen" not in legacy_payload and "author_locked" not in legacy_payload
    assert decode_memory_entities([{"record_type": kind, "payload": legacy_payload}])[0].to_json() == legacy.to_json()

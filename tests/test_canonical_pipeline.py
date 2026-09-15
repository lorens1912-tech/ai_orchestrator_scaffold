from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.operator_dpapi import read_secret
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.domain_records import FactRecord, EdgeRecord
from app.p20_core.project_repository import ProjectRepository, StorageResolver, ensure_system_repository
from app.p20_core.local_operator import initialize_operator, rotate_operator
from app.p20_core.storage_paths import get_storage_root
import app.tools as tools

PROJECT = "PROJ-pipeline-proof"
BOOK = "BOOK-pipeline-proof"
FACT = "FACT-pipeline-proof"
STAMP = "2026-09-15T18:00:00Z"


def fact(*, project=PROJECT, identity=FACT, source=None, version=1, protected=False, value="old"):
    source = source or {"scene_id": "SCENE-original", "artifact_ref": "synthetic:original"}
    return FactRecord(fact_id=identity, project_id=project, subject_id=identity,
        predicate="description", object_type="TEXT", object_id=None, object_value=value,
        reality_status="TRUE", verification_status="VERIFIED", confidence=1,
        frozen=protected, author_locked=protected, valid_from="day-1", valid_to=None,
        established_event_id=None, established_scene_id=source["scene_id"],
        source_artifact_ref=source["artifact_ref"], source_refs=(source["scene_id"],),
        canon_version=version, version=version, created_at=STAMP, updated_at=STAMP)


@pytest.fixture
def pipeline(isolated_agentpro_storage, monkeypatch, tmp_path):
    ensure_test_book_bible(BOOK)
    system = ensure_system_repository()
    system.bind_project(PROJECT, BOOK)
    repo = ProjectRepository(StorageResolver().resolve_project(PROJECT, book_id=BOOK))
    repo.initialize()
    secret = tmp_path / "pipeline-operator" / "operator.dpapi"
    initialize_operator(system, secret)
    token = read_secret(secret)
    controls = {"version": 1, "protected": False, "verdict": "ACCEPT", "calls": [], "records": None}

    def provider(payload):
        controls["calls"].append(payload)
        if payload["role"] == "EXTRACTOR":
            record = fact(project=payload["project_id"], source=payload["source"],
                version=controls["version"], protected=controls["protected"], value="accepted synthetic state")
            records = [{"record_type": "FACT", "payload": record.to_dict()}]
            if controls["records"]:
                records = controls["records"](records, payload)
            return {"records": records}
        return {"precision_status": controls["verdict"], "completeness_status": controls["verdict"]}

    monkeypatch.setattr(tools, "memory_integrity_provider", provider)
    # Only the external WRITE adapter is controlled; P20, extraction and gates are real.
    monkeypatch.setitem(tools.TOOLS, "WRITE", lambda payload: {"tool": "WRITE", "payload": {
        "text": "A synthetic inscription describes the newly accepted state of the observatory."}})
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 51000)) as client:
        yield client, repo, controls, {"Authorization": "Bearer " + token}, system, secret


def step(client, *, project=PROJECT, book=BOOK, run="run-pipeline", step_id="step-pipeline", retry=False, scope="PROJECT"):
    response = client.post("/agent/step", json={"mode": "WRITE", "project_id": project,
        "book_id": book, "run_id": run, "step_id": step_id,
        "payload": {"text": "Write synthetic test data.", "technical_retry": retry, "scope_type": scope}})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["quality_gate"]["decision"] == "ACCEPT", data
    assert data["canonical_change"] is not None, data
    return data


def proposal_record(repo, change):
    return json.loads(repo.get_metadata("canonical_proposal.v1:" + change["proposal_id"]))["versions"]["1"]


def base(change, project=PROJECT):
    return f"/operator/projects/{project}/proposals/{change['proposal_id']}"


def approve(client, change, headers):
    response = client.post(base(change) + "/review", headers=headers)
    assert response.status_code == 200, response.text
    review = response.json()
    body = {k: review["proposal"][k] for k in ("proposal_hash", "scope_type", "scope_id")}
    body.update(challenge_id=review["challenge"]["challenge_id"], decision="APPROVE")
    response = client.post(base(change) + "/decision", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def commit(client, change, headers, **body):
    return client.post(base(change) + "/commit", headers=headers,
                       json={"proposal_hash": change["proposal_hash"], **body})


def test_active_api_commit_reopen_retry_context_and_isolation(pipeline, isolated_agentpro_storage):
    client, repo, controls, headers, _, _ = pipeline
    first = step(client)
    change = first["canonical_change"]
    assert change["status"] == "COMMITTED", change
    assert change["resulting_versions"] == {FACT: 1}
    record = proposal_record(repo, change)
    assert record["initial_guard"]["outcome"] == record["final_guard"]["outcome"] == "ALLOW"
    assert record["pipeline"]["extractor"]["invocation"]["call_id"] != record["pipeline"]["verifier"]["invocation"]["call_id"]
    assert all(c["context_package_id"] and c["context_hash"] for c in controls["calls"])
    reopened = ProjectRepository(StorageResolver().resolve_project(PROJECT, book_id=BOOK))
    assert FACT in reopened.list_structured_memory_records()
    replay = step(client, retry=True)["canonical_change"]
    assert replay == change
    assert len(controls["calls"]) == 2
    assert commit(client, change, headers).json() == change
    assert len(json.loads(repo.get_metadata("canonical_versions.v1:FACT:" + FACT))) == 1
    controls["version"] = 2
    assert step(client, run="run-next", step_id="step-next")["canonical_change"]["canonical_commit"] is True
    assert "accepted synthetic state" in json.dumps(controls["calls"][2]["_context_package"])
    other_book, other_project = "BOOK-pipeline-other", "PROJ-pipeline-other"
    ensure_test_book_bible(other_book)
    controls["version"] = 1
    step(client, project=other_project, book=other_book, run="run-other", step_id="step-other")
    assert "accepted synthetic state" not in json.dumps(controls["calls"][4]["_context_package"])
    assert client.post(base(change, other_project) + "/commit", headers=headers,
                       json={"proposal_hash": change["proposal_hash"]}).status_code == 404
    (isolated_agentpro_storage / "canonical-api-proof.json").write_text(json.dumps(record, indent=2), encoding="utf-8")


def protected_change(pipeline):
    client, repo, controls, headers, _, _ = pipeline
    with repo.domain_transaction() as tx:
        tx.add_structured_memory_record(fact(protected=True))
        tx.add_edge(EdgeRecord(edge_id="edge-protected-impact", scope_type="PROJECT", scope_id=PROJECT,
            source_type="SCENE", source_id="SCENE-dependent", relation_type="REQUIRES",
            target_type="FACT", target_id=FACT, valid_from=None, valid_to=None,
            confidence=1, source_ref="synthetic", version=1), source_scope=repo.scope, target_scope=repo.scope)
    controls.update(version=2, protected=True)
    change = step(client)["canonical_change"]
    assert change["status"] == "AWAITING_USER_APPROVAL", change
    assert json.loads(repo.list_structured_memory_records()[FACT])["version"] == 1
    return change


def test_protected_operator_commit_and_legacy_file_cannot_bypass(pipeline, isolated_agentpro_storage):
    from app.p20_core.canon_service import commit_chapter_to_canon, rebuild_canon_from_chapters
    client, repo, controls, headers, _, _ = pipeline
    change = protected_change(pipeline)
    assert commit(client, change, headers).json()["status"] == "AWAITING_USER_APPROVAL"
    assert commit(client, change, {}).status_code == 401
    book = get_storage_root() / "books" / BOOK
    chapter = next((book / "chapters").glob("chapter_*.json"))
    chapter_doc = json.loads(chapter.read_text(encoding="utf-8"))
    chapter_doc["facts"] = {FACT: {"version": 999, "frozen": False, "author_locked": False}}
    chapter_doc["proposed_mutations"] = chapter_doc["facts"]
    chapter.write_text(json.dumps(chapter_doc), encoding="utf-8")
    before = json.loads((book / "memory" / "canon.json").read_text(encoding="utf-8"))
    commit_chapter_to_canon(book_id=BOOK, run_id="run-pipeline", chapter_path=str(chapter),
        chapter_id=chapter_doc["chapter_id"], chapter_sha256=chapter_doc["sha256"], project_id=PROJECT, domain_book_id=BOOK)
    after = rebuild_canon_from_chapters(BOOK, project_id=PROJECT, domain_book_id=BOOK)
    for field in ("facts", "timeline", "decisions"):
        assert after[field] == before[field]
    assert json.loads(repo.list_structured_memory_records()[FACT])["version"] == 1
    evidence, request = approve(client, change, headers)
    assert evidence["canonical_commit"] is False
    for bad in ({"proposal_hash": "a" * 64}, {"scope_id": "PROJ-foreign"}, {"scope_type": "SERIES"}):
        assert client.post(base(change) + "/decision", headers=headers, json=dict(request, **bad)).status_code == 409
    assert commit(client, change, headers, proposal_hash="a" * 64).status_code == 409
    assert commit(client, change, headers, authorization_ref=evidence["authorization_ref"]).status_code == 422
    response = commit(client, change, headers)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["canonical_commit"] is True, receipt
    assert receipt["authorization_ref"] == evidence["authorization_ref"]
    assert commit(client, change, headers).json() == receipt
    versions = json.loads(repo.get_metadata("canonical_versions.v1:FACT:" + FACT))
    assert [v["version"] for v in versions] == [1, 2]
    assert all(v["frozen"] and v["author_locked"] for v in versions)
    (isolated_agentpro_storage / "protected-api-proof.json").write_text(json.dumps(proposal_record(repo, change), indent=2), encoding="utf-8")


@pytest.mark.parametrize("alteration", ["record", "graph", "credential"])
def test_stale_basis_and_operator_rotation(pipeline, alteration):
    client, repo, _, headers, system, secret = pipeline
    change = protected_change(pipeline)
    approve(client, change, headers)
    if alteration == "credential":
        rotate_operator(system, secret)
        assert commit(client, change, headers).status_code == 401
        headers = {"Authorization": "Bearer " + read_secret(secret)}
        assert commit(client, change, headers).status_code == 403
    else:
        with repo.domain_transaction() as tx:
            if alteration == "record":
                tx.add_structured_memory_record(fact(identity="FACT-extra"))
            else:
                tx.add_edge(EdgeRecord(edge_id="edge-new", scope_type="PROJECT", scope_id=PROJECT,
                    source_type="SCENE", source_id="SCENE-dependent", relation_type="REQUIRES",
                    target_type="FACT", target_id=FACT, valid_from=None, valid_to=None,
                    confidence=1, source_ref="synthetic", version=1), source_scope=repo.scope, target_scope=repo.scope)
        assert commit(client, change, headers).json()["status"] == "STALE"
    assert json.loads(repo.list_structured_memory_records()[FACT])["version"] == 1


@pytest.mark.parametrize("verdict", ["REJECT", "REVISE"])
def test_verifier_reject_and_bounded_revision(pipeline, verdict):
    from app.p20_core.memory_extraction import DEFAULT_MEMORY_EXTRACTION_POLICY
    client, repo, controls, _, _, _ = pipeline
    controls["verdict"] = verdict
    result = step(client)["canonical_change"]
    assert result["status"] == ("REJECT" if verdict == "REJECT" else "ESCALATED"), result
    assert repo.list_structured_memory_records() == {}
    assert not any(k.startswith("canonical_proposal") for k in repo.list_metadata())
    assert len(controls["calls"]) == (2 if verdict == "REJECT" else DEFAULT_MEMORY_EXTRACTION_POLICY.max_attempts * 2)


def test_guard_denial_rolls_back_whole_set(pipeline):
    client, repo, controls, _, _, _ = pipeline
    with repo.domain_transaction() as tx:
        tx.add_structured_memory_record(fact(protected=True))
    controls["version"] = 2
    def two_records(records, payload):
        records.append({"record_type": "FACT", "payload": fact(identity="FACT-second", source=payload["source"]).to_dict()})
        return records
    controls["records"] = two_records
    result = step(client)["canonical_change"]
    assert result["status"] == "REJECTED", result
    assert set(repo.list_structured_memory_records()) == {FACT}
    assert json.loads(repo.list_structured_memory_records()[FACT])["version"] == 1


def test_target_changed_by_another_approved_proposal_is_stale(pipeline):
    client, repo, _, headers, _, _ = pipeline
    first = protected_change(pipeline)
    approve(client, first, headers)
    second = step(client, run="run-concurrent", step_id="step-concurrent")["canonical_change"]
    approve(client, second, headers)
    assert commit(client, second, headers).json()["canonical_commit"] is True
    assert commit(client, first, headers).json()["status"] == "STALE"
    assert json.loads(repo.list_structured_memory_records()[FACT])["version"] == 2
    assert len(json.loads(repo.get_metadata("canonical_versions.v1:FACT:" + FACT))) == 2


def test_rejection_and_concurrent_commit_replay(pipeline):
    from concurrent.futures import ThreadPoolExecutor
    client, repo, _, headers, _, _ = pipeline
    change = protected_change(pipeline)
    approve(client, change, headers)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: commit(client, change, headers), range(3)))
    assert all(r.status_code == 200 for r in results)
    assert all(r.json() == results[0].json() for r in results)
    assert len(json.loads(repo.get_metadata("canonical_versions.v1:FACT:" + FACT))) == 2


def test_explicit_operator_reject_never_commits(pipeline):
    client, repo, _, headers, _, _ = pipeline
    change = protected_change(pipeline)
    review = client.post(base(change) + "/review", headers=headers).json()
    body = {k: review["proposal"][k] for k in ("proposal_hash", "scope_type", "scope_id")}
    body.update(challenge_id=review["challenge"]["challenge_id"], decision="REJECT")
    assert client.post(base(change) + "/decision", headers=headers, json=body).status_code == 200
    assert commit(client, change, headers).json()["status"] == "REJECTED"
    assert json.loads(repo.list_structured_memory_records()[FACT])["version"] == 1


def test_real_analysis_error_and_audit_rollback(pipeline):
    client, repo, controls, _, _, _ = pipeline
    # Real malformed persisted graph input: no analyzer replacement or fake result.
    with repo.connect() as conn:
        conn.execute("INSERT INTO edges VALUES (?,?,?,?,?,?,?)",
            ("PROJECT", PROJECT, "edge-corrupt", FACT, "SCENE-dependent", "CAUSES", "{}"))
    result = step(client)["canonical_change"]
    assert result["status"] == "FAILED", result
    assert repo.list_structured_memory_records() == {}
    with repo.connect() as conn:
        conn.execute("DELETE FROM edges WHERE edge_id='edge-corrupt'")
        conn.execute("CREATE TRIGGER reject_commit_audit BEFORE INSERT ON project_metadata "
            "WHEN NEW.key LIKE 'canonical_commit.v1:%' BEGIN SELECT RAISE(ABORT, 'synthetic audit failure'); END")
    controls["records"] = lambda records, payload: records + [{"record_type": "FACT",
        "payload": fact(identity="FACT-atomic-second", source=payload["source"]).to_dict()}]
    result = step(client, run="run-audit-failure", step_id="step-audit-failure")["canonical_change"]
    assert result["status"] == "FAILED", result
    assert repo.list_structured_memory_records() == {}
    assert not any(k.startswith("canonical_versions") for k in repo.list_metadata())
    assert not any(k.startswith("canonical_commit") for k in repo.list_metadata())


def test_missing_impact_evidence_is_a_controlled_denial_without_mutation(pipeline):
    client, repo, _, headers, _, _ = pipeline
    change = protected_change(pipeline)
    approve(client, change, headers)
    with repo.canonical_proposal_transaction(change["proposal_id"]) as (document, _snapshot):
        del document["versions"]["1"]["impact"]

    response = commit(client, change, headers)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "REVIEW_EVIDENCE_MISSING"
    assert json.loads(repo.list_structured_memory_records()[FACT])["version"] == 1
    assert repo.get_metadata("canonical_versions.v1:FACT:" + FACT) is None
    assert not any(key.startswith("canonical_commit.v1:") for key in repo.list_metadata())


def test_unsupported_series_fails_closed_without_project_fallback(pipeline):
    client, repo, controls, _, _, _ = pipeline
    result = step(client, scope="SERIES")["canonical_change"]
    assert result["reason"] == "SERIES_CANONICAL_COMMIT_UNSUPPORTED"
    assert result["canonical_commit"] is False
    assert controls["calls"] == []
    assert repo.list_structured_memory_records() == {}


def test_unresolved_reference_and_duplicate_targets_reject_full_set(pipeline):
    client, repo, controls, _, _, _ = pipeline
    def bad_reference(records, payload):
        records[0]["payload"]["object_id"] = "FACT-missing"
        return records
    controls["records"] = bad_reference
    assert step(client)["canonical_change"]["reason"] == "CANONICAL_REFERENCE_UNRESOLVED"
    controls["records"] = lambda records, payload: records * 2
    result = step(client, run="run-duplicate", step_id="step-duplicate")["canonical_change"]
    assert result["status"] == "FAILED", result
    assert repo.list_structured_memory_records() == {}


def test_upstream_evidence_tamper_and_seeded_review_cannot_commit(pipeline):
    client, repo, _, headers, _, _ = pipeline
    change = protected_change(pipeline)
    approve(client, change, headers)
    with repo.canonical_proposal_transaction(change["proposal_id"]) as (document, snapshot):
        document["versions"]["1"]["pipeline"]["source"]["text"] = "changed source"
    response = commit(client, change, headers)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "SOURCE_EVIDENCE_MISMATCH"
    with repo.canonical_proposal_transaction(change["proposal_id"]) as (document, snapshot):
        del document["versions"]["1"]["pipeline"]
    assert commit(client, change, headers).json()["detail"] == "PIPELINE_EVIDENCE_REQUIRED"
    assert json.loads(repo.list_structured_memory_records()[FACT])["version"] == 1


@pytest.mark.parametrize("frozen,locked", [(True, False), (False, True)])
def test_independent_protection_flags(pipeline, frozen, locked):
    client, repo, controls, headers, _, _ = pipeline
    with repo.domain_transaction() as tx:
        tx.add_structured_memory_record(replace(fact(), frozen=frozen, author_locked=locked))
    controls["version"] = 2
    def protection(records, payload):
        records[0]["payload"].update(frozen=frozen, author_locked=locked)
        return records
    controls["records"] = protection
    change = step(client)["canonical_change"]
    assert change["status"] == "AWAITING_USER_APPROVAL", change
    approve(client, change, headers)
    assert commit(client, change, headers).json()["canonical_commit"] is True
    current = json.loads(repo.list_structured_memory_records()[FACT])
    assert (current["frozen"], current["author_locked"]) == (frozen, locked)


def test_actual_impact_path_and_unavailable_derived_rebuild(pipeline):
    client, repo, controls, _, _, _ = pipeline
    with repo.domain_transaction() as tx:
        tx.add_edge(EdgeRecord(edge_id="edge-impact", scope_type="PROJECT", scope_id=PROJECT,
            source_type="SCENE", source_id="SCENE-dependent", relation_type="REQUIRES",
            target_type="FACT", target_id=FACT, valid_from=None, valid_to=None,
            confidence=1, source_ref="synthetic", version=1), source_scope=repo.scope, target_scope=repo.scope)
    change = step(client)["canonical_change"]
    assert change["canonical_commit"] is True, change
    impact = proposal_record(repo, change)["impact"]["result"][0]
    assert impact["impacts"][0]["entity_id"] == "SCENE-dependent"
    assert impact["impacts"][0]["path"][0]["edge_id"] == "edge-impact"
    with repo.domain_transaction() as tx:
        tx.add_edge(EdgeRecord(edge_id="edge-derived", scope_type="PROJECT", scope_id=PROJECT,
            source_type="CONTEXT", source_id="CONTEXT-derived", relation_type="REQUIRES",
            target_type="FACT", target_id=FACT, valid_from=None, valid_to=None,
            confidence=1, source_ref="synthetic", version=1), source_scope=repo.scope, target_scope=repo.scope)
    controls["version"] = 2
    change = step(client, run="run-derived", step_id="step-derived")["canonical_change"]
    assert change["reason"] == "DERIVED_REBUILD_UNSUPPORTED", change
    assert json.loads(repo.list_structured_memory_records()[FACT])["version"] == 1


def test_unconfigured_provider_fails_explicitly(pipeline, monkeypatch):
    from app.llm_client import run_completion
    client, repo, _, _, _, _ = pipeline
    monkeypatch.setattr(tools, "memory_integrity_provider", lambda payload: run_completion(payload=payload))
    result = step(client)["canonical_change"]
    assert result == {"status": "FAILED", "canonical_commit": False, "reason": "RuntimeError"}
    assert repo.list_structured_memory_records() == {}


def test_missing_explicit_protection_rejects_whole_candidate_set(pipeline):
    from app.p20_core.domain_records import CharacterState
    client, repo, controls, _, _, _ = pipeline
    def mixed_set(records, payload):
        source = payload["source"]
        character = CharacterState(state_id="CONTEXT-character", project_id=PROJECT,
            character_id="CHAR-character", source_scene_id=source["scene_id"], source_event_id=None,
            source_artifact_ref=source["artifact_ref"], state_payload={"mood": "calm"},
            valid_from="day-1", valid_to=None, version=1, created_at=STAMP, updated_at=STAMP)
        return records + [{"record_type": "CHARACTER_STATE", "payload": character.to_dict()}]
    controls["records"] = mixed_set
    result = step(client)["canonical_change"]
    assert result["reason"] == "CANONICAL_RECORD_PROTECTION_REQUIRED", result
    assert repo.list_structured_memory_records() == {}


def test_json_write_retries_only_bounded_windows_sharing_errors(tmp_path, monkeypatch):
    from pathlib import Path
    from app.p20_core.canon_service import json_write
    original = Path.replace
    calls = []
    def sharing_violation(path, target):
        calls.append(target)
        if len(calls) < 3:
            error = PermissionError("synthetic Windows sharing violation")
            error.winerror = 5
            raise error
        return original(path, target)
    monkeypatch.setattr(Path, "replace", sharing_violation)
    target = tmp_path / "state.json"
    json_write(target, {"state": "complete"})
    assert json.loads(target.read_text()) == {"state": "complete"}
    assert len(calls) == 3
    def persistent_error(path, target):
        calls.append(target)
        error = PermissionError("synthetic persistent denial")
        error.winerror = 5
        raise error
    monkeypatch.setattr(Path, "replace", persistent_error)
    calls.clear()
    with pytest.raises(PermissionError):
        json_write(target, {"state": "not replaced"})
    assert len(calls) == 5
    assert json.loads(target.read_text()) == {"state": "complete"}

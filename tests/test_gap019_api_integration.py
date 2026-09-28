"""Neutral official app.main GAP-019 integration with audited transport stub."""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.operator_dpapi import read_secret
from app.p20_core.local_operator import initialize_operator
from app.p20_core.project_repository import ensure_system_repository
from app.p20_core.translation_contract import QA_CRITERIA, TRANSLATION_POLICY_HASH, TRANSLATION_POLICY_VERSION
from tests.test_gap018_manuscript_version import case, make_ready_candidate
from tests.test_gap019_phase2 import _source


def test_http_start_rejects_missing_source_and_unpromoted_candidate(case, tmp_path):
    if os.name != "nt":
        pytest.skip("local operator uses Windows DPAPI")
    repo, _root, _manuscript, _report, candidate, _ = make_ready_candidate(case)
    system = ensure_system_repository()
    system.bind_project(repo.context.project_id, repo.context.book_id)
    secret = tmp_path / "operator-security" / "gap019-no-source.dpapi"
    initialize_operator(system, secret)
    auth = {"Authorization": "Bearer " + read_secret(secret)}
    base = f"/operator/projects/{repo.context.project_id}/books/{repo.context.book_id}"
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
        assert client.get(base + "/source-masters/current", headers=auth).json() is None
        assert client.get(base + "/translations/en-US/current", headers=auth).json() is None
        body = {"request_id": "NO-SOURCE", "source_master_id": "SM-NONEXISTENT",
                "source_master_hash": "0" * 64, "bible_id": "TB-NONEXISTENT",
                "bible_hash": "0" * 64,
                "policy_version": TRANSLATION_POLICY_VERSION,
                "policy_hash": TRANSLATION_POLICY_HASH,
                "expected_head": {"record_id": None, "version": None, "head_hash": None}}
        absent = client.post(base + "/translations/en-US/runs", headers=auth, json=body)
        assert absent.status_code == 422, absent.text
        assert absent.json()["detail"] == "SOURCE_MASTER_REQUIRED"
        unpromoted = client.post(base + "/translations/en-US/runs", headers=auth,
            json={**body, "request_id": "CANDIDATE-AS-SOURCE",
                  "source_master_id": candidate.candidate_id,
                  "source_master_hash": candidate.artifact_hash})
        assert unpromoted.status_code == 422, unpromoted.text
        assert unpromoted.json()["detail"] == "SOURCE_MASTER_REQUIRED"


def test_official_two_locale_flow_and_target_authority(case, monkeypatch, tmp_path):
    if os.name != "nt":
        pytest.skip("local operator uses Windows DPAPI")
    repo, _root, _actor, source = _source(case)
    system = ensure_system_repository()
    system.bind_project(repo.context.project_id, repo.context.book_id)
    secret = tmp_path / "operator-security" / "gap019.dpapi"
    initialize_operator(system, secret)
    auth = {"Authorization": "Bearer " + read_secret(secret)}
    base = f"/operator/projects/{repo.context.project_id}/books/{repo.context.book_id}"
    calls = []
    def legacy_translate_forbidden(_payload):
        raise AssertionError("legacy tool_translate bypassed GAP-019 authority")

    from app.tools import TOOLS
    monkeypatch.setitem(TOOLS, "TRANSLATE", legacy_translate_forbidden)

    def fake_call_text(*, prompt, model, temperature, observer, response_schema=None):
        payload = json.loads(prompt)
        calls.append(payload["protocol"])
        observer({"event": "START", "api": "responses", "sent": {"model": model}})
        observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
        if payload["protocol"] == "AGENTPRO_GAP019_TRANSLATE_V1":
            return {"text": "Neutral translated chapter 1.", "refused": False,
                    "provider_returned_model": model}
        assert payload["protocol"] == "AGENTPRO_GAP019_TRANSLATION_QA_V1"
        output = {"unit_id": payload["unit_id"], "source_hash": payload["source_hash"],
                  "target_hash": payload["target_hash"],
                  "target_locale": payload["target_locale"],
                  "bible_hash": payload["bible_hash"],
                  "coverage": [{"criterion": criterion, "status": "PASS"}
                               for criterion in QA_CRITERIA], "findings": []}
        return {"text": json.dumps(output), "refused": False,
                "provider_returned_model": model}

    monkeypatch.setattr("app.llm_provider_openai.call_text", fake_call_text)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
        assert client.get(base + "/translations/en-US/current", headers=auth).json() is None
        assert client.post(base + "/translations/en-US/runs", headers=auth, json={
            "request_id": "NO-BIBLE", "source_master_id": source["source_master_id"],
            "source_master_hash": source["artifact_hash"], "bible_id": "missing",
            "bible_hash": "0" * 64, "policy_version": TRANSLATION_POLICY_VERSION,
            "policy_hash": TRANSLATION_POLICY_HASH,
            "expected_head": {"record_id": None, "version": None, "head_hash": None},
        }).status_code == 409
        masters = []
        for locale in ("en-US", "en-GB"):
            scope = base + "/translations/" + locale
            bible_response = client.post(base + "/translation-bibles/" + locale + "/versions",
                headers=auth, json={"request_id": "BIBLE-" + locale,
                    "source_master_id": source["source_master_id"],
                    "source_master_hash": source["artifact_hash"],
                    "expected_head": {"record_id": None, "version": None, "head_hash": None},
                    "decision_entries": []})
            assert bible_response.status_code == 200, bible_response.text
            bible = bible_response.json()
            started = client.post(scope + "/runs", headers=auth, json={
                "request_id": "START-" + locale,
                "source_master_id": source["source_master_id"],
                "source_master_hash": source["artifact_hash"],
                "bible_id": bible["translation_bible_id"],
                "bible_hash": bible["record_hash"],
                "policy_version": TRANSLATION_POLICY_VERSION,
                "policy_hash": TRANSLATION_POLICY_HASH,
                "expected_head": {"record_id": None, "version": None, "head_hash": None},
            })
            assert started.status_code == 200, started.text
            run = started.json()
            unscoped = client.post("/agent/step", json={
                "mode": "TRANSLATE", "project_id": repo.context.project_id,
                "book_id": repo.context.book_id, "run_id": "P20-GAP019-" + locale,
                "payload": {"gap019_run_id": run["run_id"],
                            "target_locale": locale, "model": "stub-model"},
            })
            assert unscoped.status_code == 403, unscoped.text
            assert client.get(scope + "/current", headers=auth).json() is None
            executed = client.post(scope + "/runs/" + run["run_id"] + "/execute",
                                   headers=auth, json={"model": "stub-model"})
            assert executed.status_code == 200, executed.text
            version = executed.json()
            calls_before_replay = len(calls)
            replay = client.post(scope + "/runs/" + run["run_id"] + "/execute",
                                 headers=auth, json={"model": "stub-model"})
            assert replay.status_code == 200 and replay.json() == version
            conflict = client.post(scope + "/runs/" + run["run_id"] + "/execute",
                                   headers=auth, json={"model": "other-model"})
            assert conflict.status_code == 409, conflict.text
            assert len(calls) == calls_before_replay
            unit_raw = repo.get_gap019_record(locale, "UNIT", version["unit_ids"][0])
            invocation = json.loads(repo.get_metadata_readonly(
                "model_invocation.v1:" + unit_raw["model_invocation_refs"][0]))
            assert invocation["requested"]["model"] == "stub-model"
            assert invocation["effective_model"] == "stub-model"
            assert invocation["resolved"]["provider"] == "OPENAI"
            assert invocation["result"]["provider_returned_model"] == "stub-model"
            assert invocation["status"] == "TRANSPORT_COMPLETED"
            assert invocation["validation"] == "VALID"
            assert "locale:" + locale in invocation["artifact_refs"]
            assert bible["translation_bible_id"] in invocation["artifact_refs"]
            qa_response = client.post(scope + "/versions/" + version["translation_version_id"] + "/qa",
                                      headers=auth, json={"request_id": "QA-" + locale,
                                                          "model": "stub-model"})
            assert qa_response.status_code == 200, qa_response.text
            report = qa_response.json()
            calls_before_qa_replay = len(calls)
            qa_replay = client.post(scope + "/versions/" + version["translation_version_id"] + "/qa",
                headers=auth, json={"request_id": "QA-" + locale, "model": "stub-model"})
            assert qa_replay.status_code == 200 and qa_replay.json() == report
            qa_conflict = client.post(scope + "/versions/" + version["translation_version_id"] + "/qa",
                headers=auth, json={"request_id": "QA-" + locale, "model": "other-model"})
            assert qa_conflict.status_code == 409, qa_conflict.text
            assert len(calls) == calls_before_qa_replay
            assert (report["execution_status"], report["validation_status"],
                    report["quality_decision"]) == ("COMPLETED", "VALID", "ACCEPT")
            assert len(report["chapter_results"]) == 1
            assert report["chapter_results"][0]["quality_decision"] == "ACCEPT"
            assert report["book_result"]["quality_decision"] == "ACCEPT"
            candidate_response = client.post(scope + "/candidates", headers=auth, json={
                "request_id": "CANDIDATE-" + locale, "qa_id": report["qa_id"],
                "qa_hash": report["record_hash"],
                "expected_head": {"record_id": None, "version": None, "head_hash": None},
            })
            assert candidate_response.status_code == 200, candidate_response.text
            candidate = candidate_response.json()
            assert client.get(scope + "/masters/current", headers=auth).json() is None
            review_response = client.post(scope + "/candidates/" + candidate["candidate_id"] +
                                          "/review", headers=auth)
            assert review_response.status_code == 200, review_response.text
            review = review_response.json()
            decision_response = client.post(scope + "/candidates/" + candidate["candidate_id"] +
                                            "/decision", headers=auth, json={
                "request_id": "APPROVE-" + locale,
                "challenge_id": review["challenge_id"], "decision": "APPROVE",
                "candidate_hash": candidate["record_hash"],
                "expected_head": {"record_id": None, "version": None, "head_hash": None},
            })
            assert decision_response.status_code == 200, decision_response.text
            master = decision_response.json()
            assert master["approved_by_user"] is True
            assert client.get(scope + "/masters/current", headers=auth).json()["target_master_id"] == master["target_master_id"]
            newer_bible = client.post(base + "/translation-bibles/" + locale + "/versions",
                headers=auth, json={"request_id": "BIBLE-NEW-" + locale,
                    "source_master_id": source["source_master_id"],
                    "source_master_hash": source["artifact_hash"],
                    "expected_head": {"record_id": bible["translation_bible_id"],
                        "version": bible["version"], "head_hash": bible["record_hash"]},
                    "decision_entries": []})
            assert newer_bible.status_code == 200, newer_bible.text
            assert client.get(scope + "/masters/current", headers=auth).json()["effective_status"] == "STALE_AGAINST_BIBLE"
            historical_replay = client.post(scope + "/runs/" + run["run_id"] + "/execute",
                headers=auth, json={"model": "stub-model"})
            assert historical_replay.status_code == 200, historical_replay.text
            assert historical_replay.json() == version
            masters.append(master)
        assert masters[0]["target_master_id"] != masters[1]["target_master_id"]
        assert calls == ["AGENTPRO_GAP019_TRANSLATE_V1",
                         "AGENTPRO_GAP019_TRANSLATION_QA_V1"] * 2
        operation_id = unit_raw["model_invocation_refs"][0]
        damaged = json.loads(repo.get_metadata_readonly("model_invocation.v1:" + operation_id))
        damaged["result_hash"] = "0" * 64
        repo.set_metadata("model_invocation.v1:" + operation_id, json.dumps(damaged))
        damaged_replay = client.post(scope + "/runs/" + run["run_id"] + "/execute",
            headers=auth, json={"model": "stub-model"})
        assert damaged_replay.status_code == 409, damaged_replay.text


def test_qa_negative_revision_reject_blocked_and_user_reject(case, monkeypatch, tmp_path):
    if os.name != "nt":
        pytest.skip("local operator uses Windows DPAPI")
    repo, _root, _actor, source = _source(case)
    system = ensure_system_repository()
    system.bind_project(repo.context.project_id, repo.context.book_id)
    secret = tmp_path / "operator-security" / "gap019-negative.dpapi"
    initialize_operator(system, secret)
    auth = {"Authorization": "Bearer " + read_secret(secret)}
    base = f"/operator/projects/{repo.context.project_id}/books/{repo.context.book_id}"
    scope = base + "/translations/en-US"
    state = {"qa": "REVISE", "calls": 0}

    def fake_call_text(*, prompt, model, temperature, observer, response_schema=None):
        payload = json.loads(prompt)
        state["calls"] += 1
        observer({"event": "START", "api": "responses", "sent": {"model": model}})
        observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
        if payload["protocol"] == "AGENTPRO_GAP019_TRANSLATE_V1":
            return {"text": "Neutral translated chapter 1.", "refused": False,
                    "provider_returned_model": model}
        if state["qa"] == "BLOCKED":
            return {"text": "{}", "refused": False, "provider_returned_model": model}
        issue = state["qa"] in {"REVISE", "REJECT"}
        criterion = "OMISSIONS"
        coverage = [{"criterion": item,
                     "status": "ISSUE" if issue and item == criterion else "PASS"}
                    for item in QA_CRITERIA]
        findings = ([{"criterion": criterion, "evidence": "Synthetic missing detail",
                      "severity": "CRITICAL" if state["qa"] == "REJECT" else "MAJOR",
                      "repairable": state["qa"] == "REVISE", "blocking": True}]
                    if issue else [])
        return {"text": json.dumps({"unit_id": payload["unit_id"],
            "source_hash": payload["source_hash"], "target_hash": payload["target_hash"],
            "target_locale": payload["target_locale"], "bible_hash": payload["bible_hash"],
            "coverage": coverage, "findings": findings}),
            "refused": False, "provider_returned_model": model}

    monkeypatch.setattr("app.llm_provider_openai.call_text", fake_call_text)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
        assert client.post(base.replace(repo.context.book_id, "BOOK-FOREIGN") +
                           "/translation-bibles/en-US/versions", headers=auth,
                           json={"request_id": "wrong", "source_master_id": source["source_master_id"],
                                 "source_master_hash": source["artifact_hash"],
                                 "expected_head": {"record_id": None, "version": None,
                                                   "head_hash": None}}).status_code == 403
        bible_response = client.post(base + "/translation-bibles/en-US/versions", headers=auth,
            json={"request_id": "BIBLE-NEG", "source_master_id": source["source_master_id"],
                  "source_master_hash": source["artifact_hash"],
                  "expected_head": {"record_id": None, "version": None, "head_hash": None}})
        assert bible_response.status_code == 200, bible_response.text
        bible = bible_response.json()
        start_body = {"request_id": "START-NEG", "source_master_id": source["source_master_id"],
            "source_master_hash": source["artifact_hash"],
            "bible_id": bible["translation_bible_id"], "bible_hash": bible["record_hash"],
            "policy_version": TRANSLATION_POLICY_VERSION, "policy_hash": TRANSLATION_POLICY_HASH,
            "expected_head": {"record_id": None, "version": None, "head_hash": None}}
        assert client.post(scope + "/runs", headers=auth,
                           json={**start_body, "source_master_hash": "0" * 64}).status_code == 409
        assert client.post(scope + "/runs", headers=auth,
                           json={**start_body, "bible_hash": "0" * 64}).status_code == 409
        started = client.post(scope + "/runs", headers=auth, json=start_body)
        assert started.status_code == 200, started.text
        run = started.json()
        assert client.post(scope + "/runs", headers=auth, json=start_body).json() == run
        translated = client.post(scope + "/runs/" + run["run_id"] + "/execute",
                                 headers=auth, json={"model": "stub-model"})
        assert translated.status_code == 200, translated.text
        version = translated.json()

        def qa(request_id, version_id):
            response = client.post(scope + "/versions/" + version_id + "/qa",
                headers=auth, json={"request_id": request_id, "model": "stub-model"})
            assert response.status_code == 200, response.text
            return response.json()

        revise = qa("QA-REVISE", version["translation_version_id"])
        assert revise["quality_decision"] == "REVISE"
        assert revise["book_result"]["blocking_count"] == 1
        assert client.post(scope + "/candidates", headers=auth, json={
            "request_id": "BAD-CANDIDATE", "qa_id": revise["qa_id"],
            "qa_hash": revise["record_hash"],
            "expected_head": {"record_id": None, "version": None,
                              "head_hash": None}}).status_code == 409
        revised = client.post(scope + "/versions/" + version["translation_version_id"] +
                              "/revisions", headers=auth, json={
            "request_id": "REVISE-1", "qa_id": revise["qa_id"],
            "qa_hash": revise["record_hash"],
            "expected_head": {"record_id": version["translation_version_id"],
                              "version": version["version"],
                              "head_hash": version["record_hash"]},
            "replacements": [{"unit_id": version["unit_ids"][0],
                              "target_text": "Adjusted translated chapter 1."}],
        })
        assert revised.status_code == 200, revised.text
        child = revised.json()
        assert child["parent_version_id"] == version["translation_version_id"]
        assert child["unit_ids"][0] != version["unit_ids"][0]
        state["qa"] = "REJECT"
        reject = qa("QA-REJECT", child["translation_version_id"])
        assert reject["quality_decision"] == "REJECT"
        state["qa"] = "BLOCKED"
        blocked = qa("QA-BLOCKED", child["translation_version_id"])
        assert blocked["quality_decision"] is None
        assert blocked["execution_status"] == "BLOCKED"
        state["qa"] = "ACCEPT"
        accepted = qa("QA-ACCEPT", child["translation_version_id"])
        assert accepted["quality_decision"] == "ACCEPT"
        candidate_response = client.post(scope + "/candidates", headers=auth, json={
            "request_id": "CANDIDATE-NEG", "qa_id": accepted["qa_id"],
            "qa_hash": accepted["record_hash"],
            "expected_head": {"record_id": None, "version": None, "head_hash": None}})
        assert candidate_response.status_code == 200, candidate_response.text
        candidate = candidate_response.json()
        review = client.post(scope + "/candidates/" + candidate["candidate_id"] +
                             "/review", headers=auth).json()
        decision_body = {"request_id": "USER-REJECT", "challenge_id": review["challenge_id"],
            "decision": "REJECT", "candidate_hash": candidate["record_hash"],
            "expected_head": {"record_id": None, "version": None, "head_hash": None}}
        rejected = client.post(scope + "/candidates/" + candidate["candidate_id"] +
                               "/decision", headers=auth, json=decision_body)
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["decision"] == "REJECT"
        assert client.post(scope + "/candidates/" + candidate["candidate_id"] +
                           "/decision", headers=auth, json=decision_body).json() == rejected.json()
        assert client.get(scope + "/masters/current", headers=auth).json() is None
        assert client.post(scope + "/candidates/" + candidate["candidate_id"] +
                           "/review", headers=auth).status_code == 409

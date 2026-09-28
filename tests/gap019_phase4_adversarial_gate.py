"""GAP-019 Phase 4 adversarial practical gate, executed directly via app.main.

Provider responses are synthetic at the existing transport boundary. No real
book content or credentials are printed or sent to a provider by this gate.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from fastapi.testclient import TestClient

from app.main import app
from app.operator_dpapi import read_secret
from app.p20_core.book_qa_execution import BookQARunRequest, run_book_qa
from app.p20_core.book_qa import BookQARequest, create_book_qa_report
from app.p20_core.candidate_master import CandidateRequest, create_candidate
from app.p20_core.cross_store_recovery import CrossStoreRecoveryService
from app.p20_core.local_operator import initialize_operator
from app.p20_core.manuscript_version import seal_manuscript
from app.p20_core.project_repository import ProjectRepository, StorageResolver, ensure_system_repository
from app.p20_core.source_promotion import issue_source_review, record_source_decision
from app.p20_core.translation import (
    create_translation_bible, current_translation_record, decide_target_candidate,
    issue_target_review, load_translation_record, recover_translation,
)
from app.p20_core.translation_contract import (
    QA_CRITERIA, TRANSLATION_POLICY_HASH, TRANSLATION_POLICY_VERSION,
    TranslationContractError,
)
from tests.test_gap018_manuscript_version import case


def _ok(response):
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.json()


def _head(value=None):
    return {"record_id": None if value is None else value[0],
            "version": None if value is None else value[1],
            "head_hash": None if value is None else value[2]}


def main() -> None:
    with (tempfile.TemporaryDirectory(prefix="agentpro-gap019-adversarial-") as directory,
          tempfile.TemporaryDirectory(prefix="agentpro-gap019-operator-") as credential_directory):
        root = Path(directory)
        os.environ["AGENTPRO_STORAGE_ROOT"] = str(root)
        repo, _root, chapter, request = case.__wrapped__(root)
        source_text = "neutral " * 1001 + "ending."
        manuscript = seal_manuscript(repo, request((chapter(1, text=source_text),)))
        import app.llm_provider_openai as transport
        state = {"qa": "ACCEPT", "issue_unit": None, "model_calls": 0}

        def provider_stub(*, prompt, model, temperature, observer, response_schema=None):
            payload = json.loads(prompt)
            state["model_calls"] += 1
            observer({"event": "START", "api": "responses", "sent": {"model": model}})
            observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
            if payload["protocol"] == "AGENTPRO_GAP018_BOOK_QA_V1":
                coverage = [dict(payload["coverage_contract"], level=level, criterion=criterion)
                    for level in payload["required_levels"] for criterion in payload["required_criteria"]]
                result = {"manuscript_id": payload["manuscript_id"],
                    "content_hash": payload["content_hash"],
                    "manifest_hash": payload["manifest_hash"],
                    "coverage": coverage, "findings": []}
                if "qa_stage" in payload:
                    result.update({"protocol": payload["protocol"],
                        "qa_stage": payload["qa_stage"], "unit_id": payload["unit_id"],
                        "input_refs": payload["input_refs"],
                        "summary": "Synthetic verified source."})
                return {"text": json.dumps(result), "refused": False,
                        "provider_returned_model": model}
            if payload["protocol"] == "AGENTPRO_GAP019_TRANSLATE_V1":
                return {"text": "synthetic neutral target unit ", "refused": False,
                        "provider_returned_model": model}
            assert payload["protocol"] == "AGENTPRO_GAP019_TRANSLATION_QA_V1"
            if state["qa"] == "BLOCKED":
                return {"text": "{}", "refused": False, "provider_returned_model": model}
            issue = state["issue_unit"] == payload["unit_id"] and state["qa"] != "ACCEPT"
            criterion = state["qa"] if state["qa"] in QA_CRITERIA else "OMISSIONS"
            coverage = [{"criterion": item,
                         "status": "ISSUE" if issue and item == criterion else "PASS"}
                        for item in QA_CRITERIA]
            findings = ([{"criterion": criterion, "evidence": "Synthetic bound discrepancy",
                "severity": "CRITICAL" if state["qa"] == "REJECT" else "MAJOR",
                "repairable": state["qa"] != "REJECT", "blocking": True}]
                if issue else [])
            return {"text": json.dumps({"unit_id": payload["unit_id"],
                "source_hash": payload["source_hash"], "target_hash": payload["target_hash"],
                "target_locale": payload["target_locale"], "bible_hash": payload["bible_hash"],
                "coverage": coverage, "findings": findings}),
                "refused": False, "provider_returned_model": model}

        transport.call_text = provider_stub
        source_qa = run_book_qa(repo, BookQARunRequest(
            repo.context.project_id, repo.context.book_id,
            manuscript.manuscript_id, manuscript.content_hash, manuscript.manifest_hash,
            "ADVERSARIAL-SOURCE-QA", "stub-model", "stub-model"))
        assert source_qa.quality_decision == "ACCEPT", source_qa.blockers
        source_candidate = create_candidate(repo, CandidateRequest(
            repo.context.project_id, repo.context.book_id,
            manuscript.manuscript_id, manuscript.content_hash, manuscript.manifest_hash,
            source_qa.qa_id, source_qa.report_hash, 1, "ADVERSARIAL-SOURCE-CANDIDATE"))
        system = ensure_system_repository()
        system.bind_project(repo.context.project_id, repo.context.book_id)
        other = ProjectRepository(StorageResolver(root).resolve_project(
            "PROJ-GAP019-OTHER", book_id="BOOK-GAP019-OTHER"))
        other.initialize()
        system.bind_project(other.context.project_id, other.context.book_id)
        secret = Path(credential_directory) / "credential.dpapi"
        actor = initialize_operator(system, secret)
        auth = {"Authorization": "Bearer " + read_secret(secret)}
        base = f"/operator/projects/{repo.context.project_id}/books/{repo.context.book_id}"
        other_base = f"/operator/projects/{other.context.project_id}/books/{other.context.book_id}"
        scope = base + "/translations/en-US"
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
            assert client.get(base + "/source-masters/current", headers=auth).json() is None
            no_source = {"request_id": "NO-SOURCE", "source_master_id": "SM-ABSENT",
                "source_master_hash": "0" * 64, "bible_id": "TB-ABSENT",
                "bible_hash": "0" * 64, "policy_version": TRANSLATION_POLICY_VERSION,
                "policy_hash": TRANSLATION_POLICY_HASH, "expected_head": _head()}
            assert client.post(scope + "/runs", headers=auth, json=no_source).status_code == 422
            assert client.post(scope + "/runs", headers=auth, json={**no_source,
                "request_id": "CANDIDATE-AS-SOURCE",
                "source_master_id": source_candidate.candidate_id,
                "source_master_hash": source_candidate.artifact_hash}).status_code == 422
            source_review = issue_source_review(repo, source_candidate.candidate_id, actor,
                                                ttl_seconds=300)
            source = record_source_decision(repo, source_candidate.candidate_id, {
                "request_id": "ADVERSARIAL-SOURCE-APPROVAL",
                "challenge_id": source_review["challenge_id"], "decision": "APPROVE",
                "candidate_hash": source_candidate.artifact_hash,
                "manuscript_hash": manuscript.content_hash,
                "manifest_hash": manuscript.manifest_hash,
                "qa_report_hash": source_qa.report_hash,
                "expected_head": source_review["expected_head"]}, actor)
            assert source["status"] == "SOURCE_COMMITTED"
            assert client.get(scope + "/current", headers=auth).json() is None
            bible_entries = [
                {"decision_id": "TERM-1", "kind": "LOCKED_TERM", "source_form": "neutral",
                 "target_form": "neutral", "target_locale": "en-US", "scope": "BOOK",
                 "status": "APPROVED", "provenance": {"authority": "USER",
                     "operator_id": actor.operator_id}},
                {"decision_id": "VOICE-1", "kind": "CHARACTER_VOICE",
                 "source_form": "narrative", "target_form": "plain",
                 "target_locale": "en-US", "scope": "BOOK", "status": "APPROVED",
                 "provenance": {"authority": "USER", "operator_id": actor.operator_id}},
            ]
            bible = _ok(client.post(base + "/translation-bibles/en-US/versions",
                headers=auth, json={"request_id": "ADV-BIBLE-US",
                    "source_master_id": source["source_master_id"],
                    "source_master_hash": source["artifact_hash"],
                    "expected_head": _head(), "decision_entries": bible_entries}))
            assert client.post(other_base + "/translation-bibles/en-US/versions",
                headers=auth, json={"request_id": "FOREIGN-SOURCE",
                    "source_master_id": source["source_master_id"],
                    "source_master_hash": source["artifact_hash"],
                    "expected_head": _head(), "decision_entries": []}).status_code == 422
            assert client.get(other_base + "/translation-bibles/en-US/versions/" +
                bible["translation_bible_id"], headers=auth).status_code == 404
            assert client.get(base.replace(repo.context.book_id, "BOOK-FOREIGN") +
                "/translation-bibles/en-US/current", headers=auth).status_code == 403
            start_body = {"request_id": "ADV-START-US", "source_master_id": source["source_master_id"],
                "source_master_hash": source["artifact_hash"],
                "bible_id": bible["translation_bible_id"], "bible_hash": bible["record_hash"],
                "policy_version": TRANSLATION_POLICY_VERSION,
                "policy_hash": TRANSLATION_POLICY_HASH, "expected_head": _head()}
            assert client.post(scope + "/runs", headers=auth, json={**start_body,
                "source_master_hash": "0" * 64}).status_code == 409
            assert client.post(scope + "/runs", headers=auth, json={**start_body,
                "bible_hash": "0" * 64}).status_code == 409
            run = _ok(client.post(scope + "/runs", headers=auth, json=start_body))
            assert _ok(client.post(scope + "/runs", headers=auth, json=start_body)) == run
            assert client.post(scope + "/runs", headers=auth, json={**start_body,
                "bible_hash": "0" * 64}).status_code == 409
            version = _ok(client.post(scope + "/runs/" + run["run_id"] + "/execute",
                headers=auth, json={"model": "stub-model"}))
            assert len(version["unit_ids"]) == 2
            assert _ok(client.post(scope + "/runs/" + run["run_id"] + "/execute",
                headers=auth, json={"model": "stub-model"})) == version
            assert client.get(other_base + "/translations/en-US/versions/" +
                version["translation_version_id"], headers=auth).status_code == 404
            state["issue_unit"] = version["unit_ids"][0]

            def qa(request_id, decision):
                state["qa"] = decision
                return _ok(client.post(scope + "/versions/" +
                    version["translation_version_id"] + "/qa", headers=auth,
                    json={"request_id": request_id, "model": "stub-model"}))

            revise = qa("ADV-QA-REVISE", "REVISE")
            assert revise["quality_decision"] == "REVISE"
            assert client.post(scope + "/candidates", headers=auth, json={
                "request_id": "BAD-REVISE", "qa_id": revise["qa_id"],
                "qa_hash": revise["record_hash"], "expected_head": _head()}).status_code == 409
            rejected_qa = qa("ADV-QA-REJECT", "REJECT")
            assert rejected_qa["quality_decision"] == "REJECT"
            blocked_qa = qa("ADV-QA-BLOCKED", "BLOCKED")
            assert blocked_qa["quality_decision"] is None
            assert blocked_qa["execution_status"] == "BLOCKED"
            quality_cases = (
                "OMISSIONS", "ADDITIONS", "MISTRANSLATIONS", "TERMINOLOGY",
                "NAMES_ENTITIES", "LOCALE_CORRECTNESS", "VOICE", "BIBLE_COMPLIANCE",
            )
            for criterion in quality_cases:
                detected = qa("ADV-QUALITY-" + criterion, criterion)
                assert detected["quality_decision"] == "REVISE"
                assert any(item["criterion"] == criterion for item in detected["findings"])
            assert client.get(scope + "/masters/current", headers=auth).json() is None
            revised = _ok(client.post(scope + "/versions/" +
                version["translation_version_id"] + "/revisions", headers=auth, json={
                "request_id": "ADV-REVISE", "qa_id": revise["qa_id"],
                "qa_hash": revise["record_hash"],
                "expected_head": _head((version["translation_version_id"],
                    version["version"], version["record_hash"])),
                "replacements": [{"unit_id": version["unit_ids"][0],
                                  "target_text": "revised synthetic neutral target unit "}]}))
            assert revised["unit_ids"][0] != version["unit_ids"][0]
            assert revised["unit_ids"][1] == version["unit_ids"][1]
            assert load_translation_record(repo, "en-US", "VERSION",
                version["translation_version_id"])["record_hash"] == version["record_hash"]
            state["qa"] = "ACCEPT"
            accepted = _ok(client.post(scope + "/versions/" +
                revised["translation_version_id"] + "/qa", headers=auth,
                json={"request_id": "ADV-QA-ACCEPT", "model": "stub-model"}))
            assert accepted["quality_decision"] == "ACCEPT"
            candidate = _ok(client.post(scope + "/candidates", headers=auth, json={
                "request_id": "ADV-CANDIDATE-1", "qa_id": accepted["qa_id"],
                "qa_hash": accepted["record_hash"], "expected_head": _head()}))
            assert client.get(scope + "/masters/current", headers=auth).json() is None
            bad_review = _ok(client.post(scope + "/candidates/" +
                candidate["candidate_id"] + "/review", headers=auth))
            assert client.post(scope + "/candidates/" + candidate["candidate_id"] +
                "/decision", headers=auth, json={"request_id": "BAD-CHALLENGE",
                "challenge_id": bad_review["challenge_id"], "decision": "APPROVE",
                "candidate_hash": "0" * 64, "expected_head": _head()}).status_code == 409
            reject_body = {"request_id": "ADV-USER-REJECT",
                "challenge_id": bad_review["challenge_id"], "decision": "REJECT",
                "candidate_hash": candidate["record_hash"], "expected_head": _head()}
            user_rejected = _ok(client.post(scope + "/candidates/" +
                candidate["candidate_id"] + "/decision", headers=auth, json=reject_body))
            assert user_rejected["decision"] == "REJECT"
            assert _ok(client.post(scope + "/candidates/" +
                candidate["candidate_id"] + "/decision", headers=auth, json=reject_body)) == user_rejected
            assert client.get(scope + "/masters/current", headers=auth).json() is None
            assert client.post(scope + "/candidates/" + candidate["candidate_id"] +
                "/review", headers=auth).status_code == 409
            next_candidate = _ok(client.post(scope + "/candidates", headers=auth, json={
                "request_id": "ADV-CANDIDATE-2", "qa_id": accepted["qa_id"],
                "qa_hash": accepted["record_hash"],
                "expected_head": _head((candidate["candidate_id"],
                    candidate["version"], candidate["record_hash"]))}))
            expiring = issue_target_review(repo, "en-US", next_candidate["candidate_id"],
                                          actor, ttl_seconds=1)
            time.sleep(1.1)
            with __import__("pytest").raises(TranslationContractError,
                                               match="TARGET_REVIEW_BINDING_MISMATCH"):
                decide_target_candidate(repo, "en-US", next_candidate["candidate_id"], {
                    "request_id": "ADV-EXPIRED", "challenge_id": expiring["challenge_id"],
                    "decision": "APPROVE", "candidate_hash": next_candidate["record_hash"],
                    "expected_head": [None, None, None]}, actor)
            reviews = [issue_target_review(repo, "en-US", next_candidate["candidate_id"],
                                           actor, ttl_seconds=300) for _ in range(2)]
            barrier = Barrier(2)
            import app.p20_core.translation as translation
            original_artifact = translation._artifact_record

            def concurrent_master(repository, receipt):
                if receipt["operation_kind"] == "TARGET_DECISION":
                    barrier.wait(timeout=30)
                return original_artifact(repository, receipt)

            translation._artifact_record = concurrent_master

            def approve(index):
                body = {"request_id": f"ADV-APPROVE-{index}",
                    "challenge_id": reviews[index]["challenge_id"],
                    "decision": "APPROVE", "candidate_hash": next_candidate["record_hash"],
                    "expected_head": [None, None, None]}
                try:
                    return body, decide_target_candidate(repo, "en-US",
                        next_candidate["candidate_id"], body, actor), None
                except TranslationContractError as exc:
                    return body, None, exc.code

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(approve, (0, 1)))
            translation._artifact_record = original_artifact
            winners = [item for item in outcomes if item[2] is None]
            losers = [item for item in outcomes if item[2] is not None]
            assert len(winners) == len(losers) == 1, outcomes
            assert losers[0][2] == "HEAD_CONFLICT"
            master = winners[0][1]
            assert current_translation_record(repo, "en-US", "MASTER")["target_master_id"] == master["target_master_id"]
            assert client.get(other_base + "/translations/en-US/masters/" +
                master["target_master_id"], headers=auth).status_code == 404
            assert client.post(other_base + "/translations/en-US/candidates/" +
                next_candidate["candidate_id"] + "/decision", headers=auth,
                json={**winners[0][0], "expected_head": _head()}).status_code == 404
            assert other.get_gap019_record("en-US", "APPROVAL", master["approval_id"]) is None
            assert other.get_gap019_request("en-US", "TARGET_DECISION",
                winners[0][0]["request_id"]) is None
            assert client.post(other_base + "/translations/en-US/operations/recover",
                headers=auth, json={"operation_kind": "TARGET_DECISION",
                    "request_id": winners[0][0]["request_id"]}).status_code == 404
            assert client.post(scope + "/candidates/" + next_candidate["candidate_id"] +
                "/decision", headers=auth, json={**winners[0][0],
                    "expected_head": _head()}).status_code == 200

            old_bible = load_translation_record(repo, "en-US", "BIBLE", bible["translation_bible_id"])
            updated_bible = _ok(client.post(base + "/translation-bibles/en-US/versions",
                headers=auth, json={"request_id": "ADV-BIBLE-US-V2",
                    "source_master_id": source["source_master_id"],
                    "source_master_hash": source["artifact_hash"],
                    "expected_head": _head((bible["translation_bible_id"],
                        bible["version"], bible["record_hash"])),
                    "decision_entries": bible_entries}))
            assert updated_bible["parent_bible_id"] == bible["translation_bible_id"]
            assert load_translation_record(repo, "en-US", "BIBLE",
                bible["translation_bible_id"]) == old_bible
            assert current_translation_record(repo, "en-US", "MASTER")["effective_status"] == "STALE_AGAINST_BIBLE"
            assert client.post(other_base + "/translations/en-US/runs", headers=auth,
                json={**start_body, "request_id": "FOREIGN-RUN"}).status_code == 422
            assert client.get(other_base + "/translations/en-US/runs/" + run["run_id"],
                headers=auth).status_code == 404
            assert client.get(other_base + "/translations/en-US/qa/" + accepted["qa_id"],
                headers=auth).status_code == 404
            assert client.get(other_base + "/translations/en-US/masters/current", headers=auth).json() is None
            assert client.post(scope + "/candidates", headers=auth, json={
                "request_id": "STALE-CANDIDATE", "qa_id": accepted["qa_id"],
                "qa_hash": accepted["record_hash"],
                "expected_head": _head((next_candidate["candidate_id"],
                    next_candidate["version"], next_candidate["record_hash"]))}).status_code == 409
            gb_request = {"request_id": "ADV-BIBLE-GB-F004",
                "source_master_id": source["source_master_id"],
                "source_master_hash": source["artifact_hash"],
                "expected_head": [None, None, None], "decision_entries": []}
            original_execute = CrossStoreRecoveryService.execute
            injected = False

            def crash_after_f004(self, plan):
                nonlocal injected
                result = original_execute(self, plan)
                if not injected and plan.operation_type == "TRANSLATION_BIBLE_V1":
                    injected = True
                    raise RuntimeError("synthetic post-F004 crash")
                return result

            CrossStoreRecoveryService.execute = crash_after_f004
            try:
                try:
                    create_translation_bible(repo, "en-GB", gb_request, actor)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("F004 crash was not injected")
            finally:
                CrossStoreRecoveryService.execute = original_execute
            assert repo.get_gap019_request("en-GB", "BIBLE_VERSION",
                gb_request["request_id"])["status"] == "RECORDED"
            recovered_gb = _ok(client.post(base + "/translations/en-GB/operations/recover",
                headers=auth, json={"operation_kind": "BIBLE_VERSION",
                                    "request_id": gb_request["request_id"]}))
            assert current_translation_record(repo, "en-GB", "BIBLE")["record_hash"] == recovered_gb["record_hash"]
            assert client.post(other_base + "/translations/en-GB/operations/recover",
                headers=auth, json={"operation_kind": "BIBLE_VERSION",
                                    "request_id": gb_request["request_id"]}).status_code == 404
            next_source_qa = create_book_qa_report(repo, BookQARequest(
                repo.context.project_id, repo.context.book_id,
                manuscript.manuscript_id, manuscript.content_hash,
                manuscript.manifest_hash, "ADV-SOURCE-QA-V2",
                source_qa.coverage, source_qa.findings,
                source_qa.model_invocation_refs, reevaluation_of=source_qa.qa_id))
            next_source_candidate = create_candidate(repo, CandidateRequest(
                repo.context.project_id, repo.context.book_id,
                manuscript.manuscript_id, manuscript.content_hash,
                manuscript.manifest_hash, next_source_qa.qa_id,
                next_source_qa.report_hash, 2, "ADV-SOURCE-CANDIDATE-V2",
                source_candidate.candidate_id))
            next_source_review = issue_source_review(repo,
                next_source_candidate.candidate_id, actor, ttl_seconds=300)
            source2 = record_source_decision(repo, next_source_candidate.candidate_id, {
                "request_id": "ADV-SOURCE-APPROVAL-V2",
                "challenge_id": next_source_review["challenge_id"],
                "decision": "APPROVE", "candidate_hash": next_source_candidate.artifact_hash,
                "manuscript_hash": manuscript.content_hash,
                "manifest_hash": manuscript.manifest_hash,
                "qa_report_hash": next_source_qa.report_hash,
                "expected_head": next_source_review["expected_head"]}, actor)
            assert source2["source_master_id"] != source["source_master_id"]
            assert current_translation_record(repo, "en-US", "MASTER")["effective_status"] == "STALE_AGAINST_SOURCE"
            assert client.get(scope + "/current", headers=auth).json()["effective_status"] == "STALE_AGAINST_SOURCE"
            assert load_translation_record(repo, "en-US", "VERSION",
                revised["translation_version_id"])["record_hash"] == revised["record_hash"]
            assert client.post(scope + "/candidates", headers=auth, json={
                "request_id": "SOURCE-STALE-CANDIDATE", "qa_id": accepted["qa_id"],
                "qa_hash": accepted["record_hash"],
                "expected_head": _head((next_candidate["candidate_id"],
                    next_candidate["version"], next_candidate["record_hash"]))}).status_code == 409
        reopened = ProjectRepository(StorageResolver(root).resolve_project(
            repo.context.project_id, book_id=repo.context.book_id))
        assert current_translation_record(reopened, "en-US", "MASTER")["target_master_id"] == master["target_master_id"]
        assert other.get_gap019_head("en-US", "MASTER") == (None, None, None)
        print(json.dumps({"status": "PASS", "source_id": source["source_master_id"],
            "unit_count": len(version["unit_ids"]), "revision_preserved_units": 1,
            "qa_revise": revise["quality_decision"],
            "qa_reject": rejected_qa["quality_decision"],
            "qa_blocked": blocked_qa["execution_status"],
            "qa_accept": accepted["quality_decision"],
            "user_reject_no_master": True, "cas_loser": losers[0][2],
            "master_id": master["target_master_id"],
            "stale_against_bible": True, "project_isolation": True,
            "stale_against_source": True, "quality_cases": list(quality_cases),
            "f004_recovery": recovered_gb["translation_bible_id"],
            "model_calls": state["model_calls"]}, sort_keys=True))


if __name__ == "__main__":
    main()

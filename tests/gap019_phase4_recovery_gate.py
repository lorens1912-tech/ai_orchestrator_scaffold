"""Synthetic GAP-019 crash/retry matrix through durable project storage."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.p20_core.cross_store_recovery import (
    CrossStoreRecoveryService, FaultPoint, InjectedRecoveryCrash,
)
from app.p20_core.project_repository import ProjectRepository, StorageResolver
from app.p20_core.translation import (
    create_translation_bible, create_translation_candidate,
    current_translation_record, decide_target_candidate, issue_target_review,
    recover_translation, start_translation,
)
from app.p20_core.translation_contract import QA_CRITERIA, TRANSLATION_POLICY_HASH, TRANSLATION_POLICY_VERSION
from app.p20_core.translation_execution import execute_translation_qa, execute_translation_run
from tests.test_gap018_manuscript_version import case, make_ready_candidate
from tests.test_gap019_phase2 import _source


def _head(value=None):
    return [None, None, None] if value is None else [value[0], value[1], value[2]]


def _raise_once(message: str):
    state = {"raised": False}

    def fail(*_args, **_kwargs):
        if not state["raised"]:
            state["raised"] = True
            raise RuntimeError(message)
    return fail, state


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="agentpro-gap019-recovery-") as directory:
        root = Path(directory)
        os.environ["AGENTPRO_STORAGE_ROOT"] = str(root)
        repo, _root, actor, source = _source(None, make_ready_candidate(case.__wrapped__(root)))
        def reopen():
            return ProjectRepository(StorageResolver(root).resolve_project(
                repo.context.project_id, book_id=repo.context.book_id))

        checkpoints = []
        import app.p20_core.translation as translation
        request = {"request_id": "FAULT-PRE-INTENT", "source_master_id": source["source_master_id"],
                   "source_master_hash": source["artifact_hash"], "expected_head": _head(),
                   "decision_entries": []}
        original_reserve = translation._reserve
        fail, state = _raise_once("synthetic before translation intent")
        def fail_reserve(*args, **kwargs):
            fail()
            return original_reserve(*args, **kwargs)
        with patch.object(translation, "_reserve", fail_reserve):
            try:
                create_translation_bible(repo, "en-US", request, actor)
            except RuntimeError as exc:
                assert str(exc) == "synthetic before translation intent"
            else:
                raise AssertionError("pre-intent fault missing")
        assert state["raised"]
        assert repo.get_gap019_request("en-US", "BIBLE_VERSION", request["request_id"]) is None
        bible = create_translation_bible(reopen(), "en-US", request, actor)
        checkpoints.append("before_translation_intent")

        for point in (FaultPoint.AFTER_INTENT, FaultPoint.AFTER_PROJECT_WRITE,
                      FaultPoint.AFTER_ARTIFACT_PREPARE, FaultPoint.AFTER_ARTIFACT_COMMIT,
                      FaultPoint.AFTER_LOGICAL_COMMIT):
            previous = current_translation_record(repo, "en-US", "BIBLE")
            request = {"request_id": "FAULT-" + point.name,
                "source_master_id": source["source_master_id"],
                "source_master_hash": source["artifact_hash"],
                "expected_head": _head((previous["translation_bible_id"],
                    previous["version"], previous["record_hash"])),
                "decision_entries": []}
            injected = {"done": False}
            def inject(self, actual, *, expected=point):
                if actual == expected and not injected["done"]:
                    injected["done"] = True
                    raise InjectedRecoveryCrash(expected.value)
            with patch.object(CrossStoreRecoveryService, "_inject", inject):
                try:
                    create_translation_bible(repo, "en-US", request, actor)
                except InjectedRecoveryCrash:
                    pass
                else:
                    raise AssertionError("F004 fault missing: " + point.name)
            assert injected["done"]
            assert repo.get_gap019_request("en-US", "BIBLE_VERSION", request["request_id"])["status"] == "RECORDED"
            reopened = reopen()
            bible = recover_translation(reopened, "en-US", "BIBLE_VERSION", request["request_id"])
            assert current_translation_record(reopened, "en-US", "BIBLE")["record_hash"] == bible["record_hash"]
            assert create_translation_bible(reopened, "en-US", request, actor) == bible
            checkpoints.append(point.name)

        original_execute = CrossStoreRecoveryService.execute
        post_commit = {"done": False}
        def crash_after_f004(self, plan):
            result = original_execute(self, plan)
            if plan.operation_type == "TRANSLATION_BIBLE_V1" and not post_commit["done"]:
                post_commit["done"] = True
                raise RuntimeError("synthetic after F004 before domain record")
            return result
        previous = bible
        request = {"request_id": "FAULT-AFTER-F004", "source_master_id": source["source_master_id"],
            "source_master_hash": source["artifact_hash"],
            "expected_head": _head((previous["translation_bible_id"],
                previous["version"], previous["record_hash"])), "decision_entries": []}
        with patch.object(CrossStoreRecoveryService, "execute", crash_after_f004):
            try:
                create_translation_bible(repo, "en-US", request, actor)
            except RuntimeError as exc:
                assert str(exc) == "synthetic after F004 before domain record"
            else:
                raise AssertionError("post F004 fault missing")
        assert post_commit["done"]
        bible = recover_translation(reopen(), "en-US", "BIBLE_VERSION", request["request_id"])
        checkpoints.append("after_F004_before_domain_record")

        start_request = {"request_id": "FAULT-START", "source_master_id": source["source_master_id"],
            "source_master_hash": source["artifact_hash"], "bible_id": bible["translation_bible_id"],
            "bible_hash": bible["record_hash"], "policy_version": TRANSLATION_POLICY_VERSION,
            "policy_hash": TRANSLATION_POLICY_HASH, "expected_head": _head()}
        original_put_request = repo.put_gap019_request
        fail, state = _raise_once("synthetic before START_TRANSLATION intent")
        def fail_start_intent(locale, kind, *args, **kwargs):
            if kind == "START_TRANSLATION":
                fail()
            return original_put_request(locale, kind, *args, **kwargs)
        with patch.object(repo, "put_gap019_request", fail_start_intent):
            try:
                start_translation(repo, "en-US", start_request, actor)
            except RuntimeError as exc:
                assert str(exc) == "synthetic before START_TRANSLATION intent"
            else:
                raise AssertionError("START_TRANSLATION pre-intent fault missing")
        assert state["raised"]
        assert repo.get_gap019_request("en-US", "START_TRANSLATION", start_request["request_id"]) is None
        run = start_translation(reopen(), "en-US", start_request, actor)
        assert start_translation(repo, "en-US", start_request, actor) == run
        checkpoints.append("before_START_TRANSLATION_intent")
        import app.llm_provider_openai as transport
        calls = {"translation": 0, "qa": 0}
        def provider_stub(*, prompt, model, temperature, observer, response_schema=None):
            payload = json.loads(prompt)
            observer({"event": "START", "api": "responses", "sent": {"model": model}})
            observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
            if payload["protocol"] == "AGENTPRO_GAP019_TRANSLATE_V1":
                calls["translation"] += 1
                return {"text": "synthetic target unit", "refused": False,
                        "provider_returned_model": model}
            assert payload["protocol"] == "AGENTPRO_GAP019_TRANSLATION_QA_V1"
            calls["qa"] += 1
            return {"text": json.dumps({"unit_id": payload["unit_id"],
                "source_hash": payload["source_hash"], "target_hash": payload["target_hash"],
                "target_locale": payload["target_locale"], "bible_hash": payload["bible_hash"],
                "coverage": [{"criterion": item, "status": "PASS"} for item in QA_CRITERIA],
                "findings": []}), "refused": False, "provider_returned_model": model}
        with patch.object(transport, "call_text", provider_stub):
            import app.p20_core.translation_execution as execution
            original_record_unit = execution.record_translation_unit
            fail, state = _raise_once("synthetic after model receipt")
            def fail_record_unit(*args, **kwargs):
                fail()
                return original_record_unit(*args, **kwargs)
            with patch.object(execution, "record_translation_unit", fail_record_unit):
                try:
                    execute_translation_run(repo, "en-US", run["run_id"],
                        requested_model="stub-model", effective_model="stub-model",
                        routing={"effective_model": "stub-model", "source": "SYNTHETIC_GATE"})
                except RuntimeError as exc:
                    assert str(exc) == "synthetic after model receipt"
                else:
                    raise AssertionError("model receipt fault missing")
            assert state["raised"] and calls["translation"] == 1
            staged = {"done": False}
            def crash_staged_unit(self, point):
                if point == FaultPoint.AFTER_ARTIFACT_PREPARE and not staged["done"]:
                    staged["done"] = True
                    raise InjectedRecoveryCrash("synthetic staged translated UNIT")
            with patch.object(CrossStoreRecoveryService, "_inject", crash_staged_unit):
                try:
                    execute_translation_run(reopen(), "en-US", run["run_id"],
                        requested_model="stub-model", effective_model="stub-model",
                        routing={"effective_model": "stub-model", "source": "SYNTHETIC_GATE"})
                except InjectedRecoveryCrash as exc:
                    assert str(exc) == "synthetic staged translated UNIT"
                else:
                    raise AssertionError("translated UNIT stage fault missing")
            assert staged["done"] and calls["translation"] == 1
            unit_receipt = repo.get_gap019_request("en-US", "TRANSLATION_UNIT",
                run["run_id"] + "-unit-0")
            assert unit_receipt["status"] == "RECORDED"
            assert unit_receipt["prepared"]["kind"] == "UNIT"
            checkpoints.append("after_staged_translated_UNIT")
            version = execute_translation_run(reopen(), "en-US", run["run_id"],
                requested_model="stub-model", effective_model="stub-model",
                routing={"effective_model": "stub-model", "source": "SYNTHETIC_GATE"})
            assert calls["translation"] == 1
            assert execute_translation_run(reopen(), "en-US", run["run_id"],
                requested_model="stub-model", effective_model="stub-model",
                routing={"effective_model": "stub-model", "source": "SYNTHETIC_GATE"}) == version
            checkpoints.append("after_model_execution_receipt")

            original_report = execution.create_translation_qa_report
            fail, state = _raise_once("synthetic during Translation QA")
            def fail_report(*args, **kwargs):
                fail()
                return original_report(*args, **kwargs)
            with patch.object(execution, "create_translation_qa_report", fail_report):
                try:
                    execute_translation_qa(repo, "en-US", version["translation_version_id"],
                        "FAULT-QA", requested_model="stub-model", effective_model="stub-model",
                        routing={"effective_model": "stub-model", "source": "SYNTHETIC_GATE"})
                except RuntimeError as exc:
                    assert str(exc) == "synthetic during Translation QA"
                else:
                    raise AssertionError("QA fault missing")
            assert state["raised"] and calls["qa"] == 1
            qa = execute_translation_qa(reopen(), "en-US", version["translation_version_id"],
                "FAULT-QA", requested_model="stub-model", effective_model="stub-model",
                routing={"effective_model": "stub-model", "source": "SYNTHETIC_GATE"})
            assert calls["qa"] == 1 and qa.quality_decision == "ACCEPT"
            checkpoints.append("during_Translation_QA")

        def candidate(number):
            expected = repo.get_gap019_head("en-US", "CANDIDATE")
            return create_translation_candidate(repo, "en-US", {
                "request_id": f"FAULT-CANDIDATE-{number}", "qa_id": qa.qa_id,
                "qa_hash": qa.record_hash, "expected_head": _head(expected)})
        first = candidate(1)
        assert current_translation_record(repo, "en-US", "MASTER") is None
        checkpoints.append("after_QA_before_user_approval")
        def approval_request(item, number):
            review = issue_target_review(repo, "en-US", item["candidate_id"], actor, ttl_seconds=300)
            return {"request_id": f"FAULT-APPROVAL-{number}",
                "challenge_id": review["challenge_id"], "decision": "APPROVE",
                "candidate_hash": item["record_hash"], "expected_head": review["expected_head"]}
        first_request = approval_request(first, 1)
        original_artifact = translation._artifact_record
        fail, state = _raise_once("synthetic after approval before TargetMaster")
        def fail_master(repository, receipt):
            if receipt.get("prepared", {}).get("kind") == "MASTER":
                fail()
            return original_artifact(repository, receipt)
        with patch.object(translation, "_artifact_record", fail_master):
            try:
                decide_target_candidate(repo, "en-US", first["candidate_id"], first_request, actor)
            except RuntimeError as exc:
                assert str(exc) == "synthetic after approval before TargetMaster"
            else:
                raise AssertionError("approval fault missing")
        assert state["raised"]
        assert repo.get_gap019_request("en-US", "TARGET_DECISION", first_request["request_id"])["status"] == "RECORDED"
        master1 = recover_translation(reopen(), "en-US", "TARGET_DECISION", first_request["request_id"])
        assert decide_target_candidate(reopen(), "en-US", first["candidate_id"], first_request, actor) == master1
        checkpoints.append("after_user_approval_before_TargetMaster")

        second = candidate(2)
        second_request = approval_request(second, 2)
        original_cas = repo.cas_gap019_head
        fail, state = _raise_once("synthetic before CAS current target")
        def fail_cas(locale, kind, expected, value, **kwargs):
            if kind == "MASTER":
                fail()
            return original_cas(locale, kind, expected, value, **kwargs)
        with patch.object(repo, "cas_gap019_head", fail_cas):
            try:
                decide_target_candidate(repo, "en-US", second["candidate_id"], second_request, actor)
            except RuntimeError as exc:
                assert str(exc) == "synthetic before CAS current target"
            else:
                raise AssertionError("CAS fault missing")
        assert state["raised"]
        assert current_translation_record(reopen(), "en-US", "MASTER")["target_master_id"] == master1["target_master_id"]
        master2 = recover_translation(reopen(), "en-US", "TARGET_DECISION", second_request["request_id"])
        assert master2["target_master_id"] != master1["target_master_id"]
        assert decide_target_candidate(reopen(), "en-US", second["candidate_id"], second_request, actor) == master2
        assert current_translation_record(reopen(), "en-US", "MASTER")["target_master_id"] == master2["target_master_id"]
        checkpoints.extend(("before_CAS_current_target", "after_commit_before_response"))
        print(json.dumps({"status": "PASS", "checkpoints": checkpoints,
            "translation_model_calls": calls["translation"], "qa_model_calls": calls["qa"],
            "current_target_master": master2["target_master_id"],
            "reopen": True}, sort_keys=True))


if __name__ == "__main__":
    main()

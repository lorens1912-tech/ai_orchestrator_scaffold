"""Live QA probes for names, terminology, Bible and locale on synthetic prose."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.operator_dpapi import read_secret
from app.p20_core.book_qa_execution import BookQARunRequest, run_book_qa
from app.p20_core.candidate_master import CandidateRequest, create_candidate
from app.p20_core.local_operator import OperatorIdentity, initialize_operator
from app.p20_core.manuscript_version import seal_manuscript
from app.p20_core.project_repository import ensure_system_repository
from app.p20_core.source_promotion import issue_source_review, record_source_decision
from app.p20_core.translation import record_translation_unit, seal_translation_version
from app.p20_core.translation_contract import (
    QA_CRITERIA, TRANSLATION_POLICY_HASH, TRANSLATION_POLICY_VERSION,
)
from tests.test_gap018_manuscript_version import case


SOURCE_TEXT = "Marta zaniosła kobaltowy kompas do portu. Powiedziała cicho: Spotkam cię o świcie."


def _ok(response):
    if response.status_code != 200:
        raise AssertionError(f"HTTP {response.status_code}: {response.text[:400]}")
    return response.json()


def _head():
    return {"record_id": None, "version": None, "head_hash": None}


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("LIVE_PROVIDER_NOT_AVAILABLE")
    with (tempfile.TemporaryDirectory(prefix="agentpro-gap019-qa-cases-") as directory,
          tempfile.TemporaryDirectory(prefix="agentpro-gap019-operator-") as credential_directory):
        os.environ["AGENTPRO_STORAGE_ROOT"] = directory
        repo, _root, chapter, request = case.__wrapped__(Path(directory))
        manuscript = seal_manuscript(repo, request((chapter(1, text=SOURCE_TEXT),)))
        import app.llm_provider_openai as transport
        real_call = transport.call_text

        def source_qa_stub(*, prompt, model, temperature, observer, response_schema=None):
            payload = json.loads(prompt)
            assert payload["protocol"] == "AGENTPRO_GAP018_BOOK_QA_V1"
            observer({"event": "START", "api": "responses", "sent": {"model": model}})
            observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
            coverage = [dict(payload["coverage_contract"], level=level, criterion=criterion)
                        for level in payload["required_levels"]
                        for criterion in payload["required_criteria"]]
            result = {"manuscript_id": payload["manuscript_id"],
                      "content_hash": payload["content_hash"],
                      "manifest_hash": payload["manifest_hash"],
                      "coverage": coverage, "findings": []}
            if "qa_stage" in payload:
                result.update({"protocol": payload["protocol"],
                    "qa_stage": payload["qa_stage"], "unit_id": payload["unit_id"],
                    "input_refs": payload["input_refs"],
                    "summary": "Synthetic source QA fixture."})
            return {"text": json.dumps(result), "refused": False,
                    "provider_returned_model": model}

        transport.call_text = source_qa_stub
        try:
            source_qa = run_book_qa(repo, BookQARunRequest(
                repo.context.project_id, repo.context.book_id,
                manuscript.manuscript_id, manuscript.content_hash,
                manuscript.manifest_hash, "QUALITY-CASES-SOURCE-QA",
                "stub-model", "stub-model"))
        finally:
            transport.call_text = real_call
        assert source_qa.quality_decision == "ACCEPT", source_qa.blockers
        candidate = create_candidate(repo, CandidateRequest(
            repo.context.project_id, repo.context.book_id,
            manuscript.manuscript_id, manuscript.content_hash,
            manuscript.manifest_hash, source_qa.qa_id, source_qa.report_hash,
            1, "QUALITY-CASES-SOURCE-CANDIDATE"))
        synthetic_actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
        review = issue_source_review(repo, candidate.candidate_id, synthetic_actor, ttl_seconds=300)
        source = record_source_decision(repo, candidate.candidate_id, {
            "request_id": "QUALITY-CASES-SOURCE-APPROVAL",
            "challenge_id": review["challenge_id"], "decision": "APPROVE",
            "candidate_hash": candidate.artifact_hash,
            "manuscript_hash": manuscript.content_hash,
            "manifest_hash": manuscript.manifest_hash,
            "qa_report_hash": source_qa.report_hash,
            "expected_head": review["expected_head"],
        }, synthetic_actor)
        system = ensure_system_repository()
        system.bind_project(repo.context.project_id, repo.context.book_id)
        secret = Path(credential_directory) / "credential.dpapi"
        actor = initialize_operator(system, secret)
        auth = {"Authorization": "Bearer " + read_secret(secret)}
        base = f"/operator/projects/{repo.context.project_id}/books/{repo.context.book_id}"
        summary = []
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
            for locale, bad_target in (
                ("en-US", "Mary carried the golden map to a city centre. She shouted: I will never meet you."),
                ("en-GB", "Mary carried the golden map to a city center. She shouted: I will never meet you; I like the color red."),
            ):
                scope = base + "/translations/" + locale
                entries = [{
                    "decision_id": "NAME-" + locale, "kind": "PROPER_NAME",
                    "source_form": "Marta", "target_form": "Marta",
                    "target_locale": locale, "scope": "BOOK", "status": "APPROVED",
                    "provenance": {"authority": "USER", "operator_id": actor.operator_id},
                }, {
                    "decision_id": "TERM-" + locale, "kind": "LOCKED_TERM",
                    "source_form": "kompas", "target_form": "compass",
                    "target_locale": locale, "scope": "BOOK", "status": "APPROVED",
                    "provenance": {"authority": "USER", "operator_id": actor.operator_id},
                }, {
                    "decision_id": "VOICE-" + locale, "kind": "CHARACTER_VOICE",
                    "source_form": "Marta", "target_form": "quiet and restrained",
                    "target_locale": locale, "scope": "BOOK", "status": "APPROVED",
                    "provenance": {"authority": "USER", "operator_id": actor.operator_id},
                }]
                bible = _ok(client.post(base + "/translation-bibles/" + locale + "/versions",
                    headers=auth, json={"request_id": "CASES-BIBLE-" + locale,
                        "source_master_id": source["source_master_id"],
                        "source_master_hash": source["artifact_hash"],
                        "expected_head": _head(), "decision_entries": entries}))
                run = _ok(client.post(scope + "/runs", headers=auth, json={
                    "request_id": "CASES-START-" + locale,
                    "source_master_id": source["source_master_id"],
                    "source_master_hash": source["artifact_hash"],
                    "bible_id": bible["translation_bible_id"],
                    "bible_hash": bible["record_hash"],
                    "policy_version": TRANSLATION_POLICY_VERSION,
                    "policy_hash": TRANSLATION_POLICY_HASH,
                    "expected_head": _head()}))
                unit = record_translation_unit(repo, locale, {
                    "request_id": "CASES-UNIT-" + locale,
                    "run_id": run["run_id"], "chapter_id": "chapter_1",
                    "start_byte": 0, "end_byte": len(SOURCE_TEXT.encode()),
                    "source_span_hash": hashlib.sha256(SOURCE_TEXT.encode()).hexdigest(),
                    "target_text": bad_target, "origin": "USER_EDIT",
                }, identity=actor)
                version = seal_translation_version(repo, locale, {
                    "request_id": "CASES-SEAL-" + locale,
                    "run_id": run["run_id"], "unit_ids": [unit["unit_id"]],
                    "parent_version_id": None,
                    "expected_head": [None, None, None],
                })
                request_id = "CASES-QA-" + locale
                report = _ok(client.post(scope + "/versions/" +
                    version["translation_version_id"] + "/qa",
                    headers=auth, json={"request_id": request_id}))
                audits = repo.list_model_invocations(run_id="QA-" + request_id)
                assert len(audits) == 1
                audit = audits[0]
                criteria = {item["criterion"] for item in report["findings"]}
                summary.append({"locale": locale, "decision": report["quality_decision"],
                    "execution": report["execution_status"],
                    "validation": report["validation_status"],
                    "finding_criteria": sorted(criteria),
                    "coverage_count": len(report["coverage"]),
                    "transport": audit["status"], "invocation_id": audit["invocation_id"],
                    "response_id": audit["attempts"][-1]["provider_reported"]["response_id"]})
                print(json.dumps({"probe": summary[-1]}, sort_keys=True), flush=True)
                if report["execution_status"] != "COMPLETED":
                    raw = audit.get("result", {}).get("text", "")
                    try:
                        parsed = json.loads(raw)
                    except ValueError:
                        parsed = None
                    print(json.dumps({"diagnostic": {
                        "audit_validation": audit.get("validation"),
                        "json_parse": parsed is not None,
                        "bindings_match": None if not isinstance(parsed, dict) else {
                            key: parsed.get(key) == expected for key, expected in {
                                "unit_id": unit["unit_id"],
                                "source_hash": unit["source_span_hash"],
                                "target_hash": unit["target_text_hash"],
                                "target_locale": locale,
                                "bible_hash": bible["record_hash"],
                            }.items()},
                        "coverage": None if not isinstance(parsed, dict) else [
                            (item.get("criterion"), item.get("status"))
                            for item in parsed.get("coverage", [])],
                        "finding_criteria": None if not isinstance(parsed, dict) else [
                            item.get("criterion") for item in parsed.get("findings", [])],
                        "assessments": None if not isinstance(parsed, dict) else [
                            (item.get("criterion"), item.get("status"),
                             bool(str(item.get("evidence") or "").strip()),
                             item.get("severity"), item.get("repairable"),
                             item.get("blocking"))
                            for item in parsed.get("assessments", [])],
                    }}, sort_keys=True), flush=True)
                assert audit["status"] == "TRANSPORT_COMPLETED"
                assert report["execution_status"] == "COMPLETED"
                assert report["validation_status"] == "VALID"
                assert report["quality_decision"] in {"REVISE", "REJECT"}
                assert len(report["coverage"]) == len(QA_CRITERIA)
                assert client.get(scope + "/masters/current", headers=auth).json() is None
        print(json.dumps({"status": "PASS", "qa_cases": summary}, sort_keys=True))


if __name__ == "__main__":
    main()

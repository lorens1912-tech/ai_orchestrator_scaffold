"""Separate 200000-word GAP-019 practical gate through operator API.

The only model substitute is at the existing provider transport boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import traceback
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
from app.p20_core.translation_contract import QA_CRITERIA, TRANSLATION_POLICY_HASH, TRANSLATION_POLICY_VERSION
from app.p20_core.translation import _source_bundle
from app.p20_core.translation_execution import MAX_UNIT_BYTES, _segments
from tests.test_gap018_manuscript_version import case


def _require(response):
    if response.status_code != 200:
        raise AssertionError(f"HTTP {response.status_code}: {response.text[:500]}")
    return response.json()


def main() -> None:
    started = time.monotonic()
    with (tempfile.TemporaryDirectory(prefix="agentpro-gap019-long-") as directory,
          tempfile.TemporaryDirectory(prefix="agentpro-gap019-operator-") as credential_directory):
        root = Path(directory)
        os.environ["AGENTPRO_STORAGE_ROOT"] = str(root)
        repo, _base_root, chapter, request = case.__wrapped__(root)
        chapter_text = "a " * 100000
        assert len(chapter_text.split()) == 100000
        manuscript = seal_manuscript(repo, request((
            chapter(1, text=chapter_text), chapter(2, text=chapter_text))))
        import app.llm_provider_openai as transport
        book_qa_calls = []

        def book_qa_stub(*, prompt, model, temperature, observer, response_schema=None):
            payload = json.loads(prompt)
            book_qa_calls.append(payload)
            coverage = [dict(payload["coverage_contract"], level=level, criterion=criterion)
                        for level in payload["required_levels"]
                        for criterion in payload["required_criteria"]]
            observer({"event": "START", "api": "responses", "sent": {"model": model}})
            observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
            return {"text": json.dumps({"protocol": payload["protocol"],
                "qa_stage": payload["qa_stage"], "unit_id": payload["unit_id"],
                "input_refs": payload["input_refs"],
                "manuscript_id": payload["manuscript_id"],
                "content_hash": payload["content_hash"],
                "manifest_hash": payload["manifest_hash"],
                "coverage": coverage, "findings": [],
                "summary": "Synthetic evidence bound to sealed input."}),
                "refused": False, "provider_returned_model": model}

        transport.call_text = book_qa_stub
        source_qa = run_book_qa(repo, BookQARunRequest(
            repo.context.project_id, repo.context.book_id,
            manuscript.manuscript_id, manuscript.content_hash,
            manuscript.manifest_hash, "LONG-SOURCE-QA", "stub-model", "stub-model"))
        assert source_qa.quality_decision == "ACCEPT", source_qa.blockers
        candidate = create_candidate(repo, CandidateRequest(
            repo.context.project_id, repo.context.book_id,
            manuscript.manuscript_id, manuscript.content_hash,
            manuscript.manifest_hash, source_qa.qa_id, source_qa.report_hash,
            1, "LONG-SOURCE-CANDIDATE"))
        actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
        review = issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
        source = record_source_decision(repo, candidate.candidate_id, {
            "request_id": "LONG-SOURCE-APPROVAL", "challenge_id": review["challenge_id"],
            "decision": "APPROVE", "candidate_hash": candidate.artifact_hash,
            "manuscript_hash": manuscript.content_hash,
            "manifest_hash": manuscript.manifest_hash,
            "qa_report_hash": source_qa.report_hash,
            "expected_head": review["expected_head"],
        }, actor)
        assert source["status"] == "SOURCE_COMMITTED"
        print(json.dumps({"stage": "approved_long_source", "words": 200000,
                          "book_qa_calls": len(book_qa_calls),
                          "elapsed_seconds": round(time.monotonic() - started, 2)}), flush=True)
        system = ensure_system_repository()
        system.bind_project(repo.context.project_id, repo.context.book_id)
        secret = Path(credential_directory) / "credential.dpapi"
        operator_actor = initialize_operator(system, secret)
        auth = {"Authorization": "Bearer " + read_secret(secret)}
        base = f"/operator/projects/{repo.context.project_id}/books/{repo.context.book_id}"
        scope = base + "/translations/en-US"
        measures = {"translation_calls": 0, "qa_calls": 0,
                    "max_prompt_bytes": 0, "max_source_span_bytes": 0,
                    "chapter_ids": set(), "bible_hashes": set(),
                    "context_hashes": set()}
        bible_entries = [
            {"decision_id": "LONG-TERM", "kind": "LOCKED_TERM", "source_form": "a",
             "target_form": "a", "target_locale": "en-US", "scope": "BOOK",
             "status": "APPROVED", "provenance": {"authority": "USER",
                 "operator_id": actor.operator_id}},
            {"decision_id": "LONG-VOICE", "kind": "CHARACTER_VOICE",
             "source_form": "narrator", "target_form": "plain",
             "target_locale": "en-US", "scope": "BOOK", "status": "APPROVED",
             "provenance": {"authority": "USER", "operator_id": actor.operator_id}},
            {"decision_id": "LONG-STYLE", "kind": "LITERARY_STYLE",
             "source_form": "style", "target_form": "neutral",
             "target_locale": "en-US", "scope": "BOOK", "status": "APPROVED",
             "provenance": {"authority": "USER", "operator_id": actor.operator_id}},
        ]
        for entry in bible_entries:
            entry["provenance"]["operator_id"] = operator_actor.operator_id

        def provider_stub(*, prompt, model, temperature, observer, response_schema=None):
            payload = json.loads(prompt)
            measures["max_prompt_bytes"] = max(measures["max_prompt_bytes"],
                                                 len(prompt.encode("utf-8")))
            observer({"event": "START", "api": "responses", "sent": {"model": model}})
            observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
            if payload["protocol"] == "AGENTPRO_GAP019_TRANSLATE_V1":
                measures["translation_calls"] += 1
                measures["max_source_span_bytes"] = max(measures["max_source_span_bytes"],
                    payload["end_byte"] - payload["start_byte"])
                measures["chapter_ids"].add(payload["chapter"]["chapter_id"])
                measures["bible_hashes"].add(payload["bible_hash"])
                assert payload["source_binding"]["source_master_id"] == source["source_master_id"]
                assert payload["target_locale"] == "en-US"
                items = payload["context_package"]["included_items"]
                bible_items = [item for item in items if item["entity_type"] == "TRANSLATION_BIBLE"]
                assert len(bible_items) == 1
                assert json.loads(bible_items[0]["content"]) == bible_entries
                assert any(item["entity_type"] == "BOOK_BIBLE_SNAPSHOT" for item in items)
                measures["context_hashes"].add(payload["context_package"]["context_hash"])
                source_items = [item for item in items if item["entity_type"] == "SOURCE_SPAN"]
                assert len(source_items) == 1
                return {"text": source_items[0]["content"], "refused": False,
                        "provider_returned_model": model}
            assert payload["protocol"] == "AGENTPRO_GAP019_TRANSLATION_QA_V1"
            assert response_schema is not None and response_schema["strict"] is True
            measures["qa_calls"] += 1
            items = payload["context_package"]["included_items"]
            assert any(item["entity_type"] == "TRANSLATION_BIBLE" and
                       json.loads(item["content"]) == bible_entries for item in items)
            source_items = [item for item in items if item["entity_type"] == "SOURCE_SPAN"]
            target_items = [item for item in items if item["entity_type"] == "TARGET_SPAN"]
            assert len(source_items) == len(target_items) == 1
            assert source_items[0]["content"] == target_items[0]["content"]
            output = {"unit_id": payload["unit_id"], "source_hash": payload["source_hash"],
                "target_hash": payload["target_hash"], "target_locale": "en-US",
                "bible_hash": payload["bible_hash"],
                "coverage": [{"criterion": criterion, "status": "PASS"}
                             for criterion in QA_CRITERIA], "findings": []}
            return {"text": json.dumps(output), "refused": False,
                    "provider_returned_model": model}

        transport.call_text = provider_stub
        import app.p20_core.translation_execution as translation_execution
        execute_original = translation_execution.execute_translation_run

        def observed_execution(*args, **kwargs):
            try:
                return execute_original(*args, **kwargs)
            except Exception as exc:
                frames = []
                frame = exc.__traceback__
                while frame:
                    if frame.tb_frame.f_code.co_name == "execute_translation_run":
                        frames.append({"index": frame.tb_frame.f_locals.get("index"),
                                       "segments": len(frame.tb_frame.f_locals.get("segments", []))})
                    if frame.tb_frame.f_code.co_name == "_context":
                        value = frame.tb_frame.f_locals.get("source_text", "")
                        frames.append({"source_text_bytes": len(value.encode("utf-8")),
                                       "source_text_has_content": bool(value.strip())})
                    if frame.tb_frame.f_code.co_name == "__post_init__":
                        candidate = frame.tb_frame.f_locals.get("self")
                        if getattr(candidate, "entity_type", None):
                            frames.append({"candidate_type": candidate.entity_type,
                                "representation_lengths": [len(item) for item in
                                    candidate.representations.values()]})
                    frame = frame.tb_next
                print(json.dumps({"stage": "translation_failure",
                    "error_type": type(exc).__name__,
                    "contract_code": getattr(exc, "code", None),
                    "cause_type": type(exc.__cause__).__name__ if exc.__cause__ else None,
                    "frames": [(Path(item.filename).name, item.lineno, item.name)
                               for item in traceback.extract_tb(exc.__traceback__)[-6:]],
                    "safe_locals": frames}),
                    flush=True)
                raise

        translation_execution.execute_translation_run = observed_execution
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
            bible = _require(client.post(base + "/translation-bibles/en-US/versions",
                headers=auth, json={"request_id": "LONG-BIBLE", "source_master_id": source["source_master_id"],
                    "source_master_hash": source["artifact_hash"], "decision_entries": bible_entries,
                    "expected_head": {"record_id": None, "version": None, "head_hash": None}}))
            run = _require(client.post(scope + "/runs", headers=auth, json={
                "request_id": "LONG-START", "source_master_id": source["source_master_id"],
                "source_master_hash": source["artifact_hash"],
                "bible_id": bible["translation_bible_id"], "bible_hash": bible["record_hash"],
                "policy_version": TRANSLATION_POLICY_VERSION,
                "policy_hash": TRANSLATION_POLICY_HASH,
                "expected_head": {"record_id": None, "version": None, "head_hash": None}}))
            source_bundle, source_chapters = _source_bundle(repo, run["source_binding"])
            source_segments = list(_segments(source_bundle["content"], source_chapters))
            blank_segments = [index for index, item in enumerate(source_segments)
                              if not item[3].strip()]
            print(json.dumps({"stage": "segment_preflight", "segments": len(source_segments),
                              "blank_indices": blank_segments}), flush=True)
            assert not blank_segments
            version = _require(client.post(scope + "/runs/" + run["run_id"] + "/execute",
                                           headers=auth, json={"model": "stub-model"}))
            print(json.dumps({"stage": "translated", "units": len(version["unit_ids"]),
                              "elapsed_seconds": round(time.monotonic() - started, 2)}), flush=True)
            report = _require(client.post(scope + "/versions/" +
                version["translation_version_id"] + "/qa", headers=auth,
                json={"request_id": "LONG-QA", "model": "stub-model"}))
        assert measures["translation_calls"] == len(version["unit_ids"])
        assert measures["qa_calls"] == len(version["unit_ids"])
        assert measures["max_source_span_bytes"] <= MAX_UNIT_BYTES
        assert measures["max_prompt_bytes"] < 50000
        assert measures["chapter_ids"] == {"chapter_1", "chapter_2"}
        assert measures["bible_hashes"] == {bible["record_hash"]}
        assert len(measures["context_hashes"]) == len(version["unit_ids"])
        assert len(report["chapter_results"]) == 2
        assert report["book_result"]["quality_decision"] == "ACCEPT"
        bundle = json.loads((repo.context.project_root / manuscript.artifact_ref).read_text(encoding="utf-8"))
        source_bytes = bundle["content"].encode("utf-8")
        assert len(bundle["content"].split()) >= 200000
        assert manuscript.content_hash == hashlib.sha256(source_bytes).hexdigest()
        cursor = 0
        for unit_id in version["unit_ids"]:
            unit = repo.get_gap019_record("en-US", "UNIT", unit_id)
            assert unit["start_byte"] == cursor
            assert unit["end_byte"] > cursor
            assert unit["source_span_hash"] == hashlib.sha256(
                source_bytes[cursor:unit["end_byte"]]).hexdigest()
            assert unit["source_binding"]["source_master_id"] == source["source_master_id"]
            assert unit["bible_hash"] == bible["record_hash"]
            cursor = unit["end_byte"]
        assert cursor == len(source_bytes)
        print(json.dumps({"status": "PASS", "words": 200000,
            "chapters": len(report["chapter_results"]),
            "units": len(version["unit_ids"]),
            "translation_calls": measures["translation_calls"],
            "qa_calls": measures["qa_calls"],
            "book_qa_calls": len(book_qa_calls),
            "max_source_span_bytes": measures["max_source_span_bytes"],
            "max_prompt_bytes": measures["max_prompt_bytes"],
            "bible_decisions": len(bible_entries),
            "bounded_context_packages": len(measures["context_hashes"]),
            "book_qa": report["book_result"]["quality_decision"],
            "elapsed_seconds": round(time.monotonic() - started, 2)}, sort_keys=True))


if __name__ == "__main__":
    main()

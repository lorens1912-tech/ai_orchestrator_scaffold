from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from dataclasses import asdict
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.operator_dpapi import read_secret

from app.p20_core.book_bible_contract import build_valid_book_bible_payload
from app.p20_core.book_qa import (
    BookQARequest, Coverage, CRITERIA_HASH, CRITERIA_VERSION,
    MANDATORY_CRITERIA, MANDATORY_LEVELS, POLICY_HASH, POLICY_VERSION,
    create_book_qa_report, load_book_qa_report,
)
from app.p20_core.book_qa_execution import (
    BookQARecoveryRequired, BookQARunRequest, run_book_qa,
)
from app.p20_core.candidate_master import (
    CandidateIntegrityError, CandidateRequest, create_candidate, load_candidate,
)
from app.p20_core.canon_service import canonical_proposal_hash
from app.p20_core.context_builder import (
    ContextBuilder, ContextItem, ContextOverflowError, ContextPackage, SelectionSummary,
)
from app.p20_core.cross_store_recovery import (
    ArtifactRoot,
    CrossStoreOperationPlan,
    CrossStoreRecoveryService,
    FaultPoint,
    InjectedRecoveryCrash,
    RecoveryInterventionRequired,
)
from app.p20_core.evaluation import (
    EvaluationBinding,
    finalize_evaluation,
    start_evaluation,
)
from app.p20_core.gap018_receipts import Gap018RequestConflict
from app.p20_core.manuscript_version import (
    ChapterSelection,
    CompositionDeclaration,
    ManuscriptBlocked,
    SealRequest,
    load_manuscript,
    seal_manuscript,
    validate_manuscript_input,
)
from app.p20_core.local_operator import OperatorError, OperatorIdentity, initialize_operator
from app.p20_core.model_provenance import (
    InvocationRecoveryRequired, ModelInvocationAudit, fingerprint,
)
from app.p20_core.source_promotion import (
    current_source_master, issue_source_review, load_source_master,
    record_source_decision, recover_source_promotion,
)
import app.p20_core.source_promotion as source_promotion_module
from app.p20_core.project_repository import ProjectRepository, StorageResolver, ensure_system_repository


def sha(value: bytes | str) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode("utf-8")).hexdigest()


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


@pytest.fixture
def case(isolated_agentpro_storage):
    root = isolated_agentpro_storage
    project_id = "PROJ-GAP018-NEUTRAL"
    book_id = "BOOK-GAP018-NEUTRAL"
    repository = ProjectRepository(StorageResolver(root).resolve_project(project_id, book_id=book_id))
    repository.initialize()
    book_root = root / "books" / book_id
    (book_root / "chapters").mkdir(parents=True)
    (book_root / "artifacts" / "canon").mkdir(parents=True)
    bible = canonical_bytes(build_valid_book_bible_payload(book_id))
    bible_ref = f"books/{book_id}/book_bible.json"
    (book_root / "book_bible.json").write_bytes(bible)
    canon = canonical_bytes({"_meta": {"book_id": book_id}, "facts": []})
    canon_ref = f"books/{book_id}/artifacts/canon/snapshot.json"
    (book_root / "artifacts" / "canon" / "snapshot.json").write_bytes(canon)

    def chapter(number: int, *, decision: str = "ACCEPT", quality_doc: bool = True,
                text: str | None = None):
        chapter_id = f"chapter_{number}"
        text = text if text is not None else f"Neutral chapter {number}."
        run_id = f"RUN-GAP018-{number}"
        operation_id = f"eval-gap018-{number}"
        binding = EvaluationBinding.local_deterministic(
            project_id=project_id, book_id=book_id, series_id=None,
            run_id=run_id, step_id=f"STEP-{number}", operation_id=operation_id,
            artifact_id=f"ARTIFACT-{number}", artifact_version="1",
            artifact_hash=sha(text), criteria_version="TEST-QUALITY-V1",
            criteria={"accept_min": 0.7}, context_package_id=f"CTX-{number}",
            context_hash=sha(f"context {number}"), evaluator_id="synthetic.local",
            evaluator_version="1", configuration={"accept_min": 0.7},
        )
        started = start_evaluation(repository, binding)
        record = finalize_evaluation(
            repository, binding, attempt_token=started.attempt_token,
            decision=decision, reasons=("synthetic",),
        )
        proposal_id = f"proposal-gap018-{number}"
        canonical_operation_id = f"canonical-gap018-{number}"
        proposal = {
            "proposal_id": proposal_id, "project_id": project_id,
            "book_id": book_id, "run_id": run_id, "scope_type": "PROJECT",
            "source_artifact_hash": sha(text), "status": "COMMITTED",
        }
        proposal["proposal_hash"] = canonical_proposal_hash(proposal)
        receipt = {
            "operation_id": canonical_operation_id, "proposal_id": proposal_id,
            "proposal_hash": proposal["proposal_hash"], "project_id": project_id,
            "scope_type": "PROJECT", "scope_id": project_id, "run_id": run_id,
            "status": "COMMITTED", "canonical_commit": True,
        }
        repository.set_metadata(
            "canonical_proposal.v1:" + proposal_id,
            json.dumps({"versions": {"1": {"proposal": proposal, "receipt": receipt}}}),
        )
        repository.set_metadata("canonical_commit.v1:" + canonical_operation_id, json.dumps(receipt))
        quality = {
            "evaluation_id": record.evaluation_id,
            "evaluation_record_hash": record.record_hash,
            "evaluation_operation_id": record.operation_id,
            "evaluated_artifact_id": record.artifact_id,
            "evaluated_artifact_hash": record.artifact_hash,
            "evaluation_execution_status": record.execution_status,
            "evaluation_validation_status": record.validation_status,
            "decision": record.decision,
        } if quality_doc else None
        document = {
            "chapter_id": chapter_id, "project_id": project_id,
            "book_id": book_id, "domain_book_id": book_id,
            "version": 1, "status": "ACCEPTED", "text": text,
            "sha256": sha(text), "run_id": run_id,
            "quality_decision": decision if quality_doc else "NOT_EVALUATED",
            "quality_evaluation": quality,
            "versions": [{"version": 1, "status": "ACCEPTED", "text": text,
                          "artifact_hash": sha(text), "step_id": f"STEP-{number}"}],
        }
        raw = canonical_bytes(document)
        chapter_operation_id = f"chapter-gap018-{number}"
        CrossStoreRecoveryService(repository).execute(CrossStoreOperationPlan(
            operation_id=chapter_operation_id, project_id=project_id, book_id=book_id,
            run_id=run_id, step_id=f"STEP-{number}",
            operation_type="CHAPTER_ARTIFACT_LINEAGE_V2",
            source_ref=f"synthetic/{chapter_id}", artifact_root=ArtifactRoot.BOOKS,
            artifact_relative_path=f"{book_id}/chapters/{chapter_id}.json",
            artifact_bytes=raw, expected_versions={"chapter_version": 1},
            provenance_refs=(f"synthetic/{chapter_id}",),
        ))
        return ChapterSelection(
            chapter_id=chapter_id, version=1,
            artifact_ref=f"books/{book_id}/chapters/{chapter_id}.json",
            artifact_sha256=sha(raw), text_sha256=sha(text),
            evaluation_id=record.evaluation_id,
            evaluation_record_hash=record.record_hash,
            chapter_commit_operation_id=chapter_operation_id,
            canonical_commit_operation_id=canonical_operation_id,
        )

    def request(chapters):
        return SealRequest(
            project_id=project_id, book_id=book_id, version=1,
            chapter_versions=tuple(chapters),
            composition=CompositionDeclaration(
                version="ORDER-V1", project_id=project_id, book_id=book_id,
                ordered_chapter_ids=tuple(item.chapter_id for item in chapters),
            ),
            book_bible_source_ref=bible_ref, book_bible_version="1.0",
            book_bible_hash=sha(bible), canon_source_ref=canon_ref,
            canon_revision="sha256:" + sha(canon), canon_snapshot_hash=sha(canon),
            source_language="pl-PL", request_id="REQ-GAP018-SEAL",
        )

    return repository, root, chapter, request


def codes(repository, request):
    result = validate_manuscript_input(repository, request)
    assert result.status == "BLOCKED"
    return {item.code for item in result.blockers}


def test_valid_seal_is_deterministic_and_reopens(case):
    repo, root, chapter, request = case
    first, second = chapter(1), chapter(2)
    selected = request((first, second))
    assert validate_manuscript_input(repo, selected).status == "VALID"
    sealed = seal_manuscript(repo, selected)
    assert sealed.status == "SEALED"
    assert sealed.content_hash == sha("Neutral chapter 1.\n\nNeutral chapter 2.")
    assert seal_manuscript(repo, selected) == sealed
    reopened = ProjectRepository(StorageResolver(root).resolve_project(repo.context.project_id, book_id=repo.context.book_id))
    assert load_manuscript(reopened, sealed.manuscript_id) == sealed
    assert reopened.get_schema_version() == 7


def test_explicit_order_changes_identity_without_filesystem_order(case):
    repo, _root, chapter, request = case
    second, first = chapter(2), chapter(1)
    a = seal_manuscript(repo, request((first, second)))
    b = seal_manuscript(repo, replace(request((second, first)), version=2,
                                      parent_manuscript_id=a.manuscript_id,
                                      request_id="REQ-GAP018-REORDER"))
    assert a.manifest_hash != b.manifest_hash
    assert a.content_hash != b.content_hash


def test_manuscript_request_id_conflict_is_durable(case):
    repo, root, chapter, request = case
    first, second = chapter(1), chapter(2)
    seal_manuscript(repo, request((first, second)))
    reopened = ProjectRepository(StorageResolver(root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id,
    ))
    with pytest.raises(Gap018RequestConflict):
        seal_manuscript(reopened, request((second, first)))


def test_missing_duplicate_and_wrong_order_block(case):
    repo, _root, chapter, request = case
    first, second = chapter(1), chapter(2)
    original = request((first, second))
    assert "COMPOSITION_ORDER_MISMATCH" in codes(repo, replace(original, chapter_versions=(first,)))
    assert "DUPLICATE_CHAPTER" in codes(repo, request((first, first)))
    assert "COMPOSITION_ORDER_MISMATCH" in codes(repo, replace(original, chapter_versions=(second, first)))


def test_wrong_scope_hash_and_quality_block(case):
    repo, _root, chapter, request = case
    selected = chapter(1)
    original = request((selected,))
    assert "PROJECT_BOOK_SCOPE_MISMATCH" in codes(repo, replace(original, project_id="PROJ-OTHER"))
    assert "PROJECT_BOOK_SCOPE_MISMATCH" in codes(repo, replace(original, book_id="BOOK-OTHER"))
    assert "CHAPTER_TEXT_HASH_MISMATCH" in codes(repo, request((replace(selected, text_sha256=sha("wrong")),)))
    assert "QUALITY_REFERENCE_MISMATCH" in codes(repo, request((replace(selected, evaluation_record_hash=sha("wrong")),)))
    assert "CANONICAL_COMMIT_INCOMPLETE" in codes(repo, request((replace(selected, canonical_commit_operation_id="missing"),)))
    assert "CHAPTER_COMMIT_INCOMPLETE" in codes(repo, request((replace(selected, chapter_commit_operation_id="missing"),)))


def test_malformed_chapter_lineage_blocks_without_crashing(case):
    repo, root, chapter, request = case
    selected = chapter(1)
    path = root / selected.artifact_ref
    document = json.loads(path.read_text(encoding="utf-8"))
    document["versions"] = [None]
    path.write_bytes(canonical_bytes(document))
    assert "CHAPTER_LINEAGE_MISMATCH" in codes(repo, request((selected,)))


@pytest.mark.parametrize("decision", ["REVISE", "REJECT"])
def test_non_accept_quality_blocks(case, decision):
    repo, _root, chapter, request = case
    selected = chapter(1, decision=decision)
    assert "QUALITY_REFERENCE_MISMATCH" in codes(repo, request((selected,)))


def test_not_evaluated_blocks(case):
    repo, _root, chapter, request = case
    selected = chapter(1, quality_doc=False)
    assert "QUALITY_NOT_EVALUATED" in codes(repo, request((selected,)))


def test_snapshot_mismatch_and_mutable_current_do_not_change_sealed(case):
    repo, root, chapter, request = case
    selected = request((chapter(1),))
    assert "BOOK_BIBLE_SNAPSHOT_INVALID" in codes(repo, replace(selected, book_bible_hash=sha("bad")))
    assert "CANON_SNAPSHOT_INVALID" in codes(repo, replace(selected, canon_snapshot_hash=sha("bad")))
    sealed = seal_manuscript(repo, selected)
    bible_path = root / selected.book_bible_source_ref
    canon_path = root / selected.canon_source_ref
    bible_path.write_text("{}", encoding="utf-8")
    canon_path.write_text("{}", encoding="utf-8")
    historical = load_manuscript(repo, sealed.manuscript_id)
    assert historical.book_bible_hash == sealed.book_bible_hash
    assert seal_manuscript(repo, selected) == sealed
    assert historical.canon_snapshot_hash == sealed.canon_snapshot_hash
    bundle = json.loads((repo.context.project_root / sealed.artifact_ref).read_text(encoding="utf-8"))
    assert sha(base64.b64decode(bundle["snapshots"]["book_bible_b64"])) == sealed.book_bible_hash
    assert sha(base64.b64decode(bundle["snapshots"]["canon_b64"])) == sealed.canon_snapshot_hash
    assert "BOOK_BIBLE_SNAPSHOT_INVALID" in codes(repo, selected)


def test_cross_project_reference_blocks(case):
    repo, _root, chapter, request = case
    selected = chapter(1)
    foreign = replace(selected, artifact_ref=selected.artifact_ref.replace(repo.context.book_id, "BOOK-FOREIGN"))
    assert "CHAPTER_MISSING_OR_INVALID" in codes(repo, request((foreign,)))
    with pytest.raises(ManuscriptBlocked):
        seal_manuscript(repo, request((foreign,)))


def make_ready_candidate(case, *, materialize_candidate=True):
    repo, root, chapter, request = case
    manuscript = seal_manuscript(repo, request((chapter(1),)))
    content = "Neutral chapter 1."
    item = ContextItem(
        entity_type="BOOK", entity_id=repo.context.book_id, layer="TASK",
        mandatory=True, reason="Synthetic contract input", score=None,
        score_breakdown=None, representation_type="FULL", token_count=4,
        source_version=1, content_hash=sha(content), content=content,
        source_ref=manuscript.artifact_ref,
    )
    package = ContextPackage.create(
        context_package_id="CONTEXT-GAP018-QA", project_id=repo.context.project_id,
        book_id=repo.context.book_id, series_id=None, run_id="RUN-GAP018-QA",
        step_id="STEP-GAP018-QA", role="CRITIC", mode="QA",
        context_policy_id="GAP018_TEST", context_policy_version=1,
        context_profile_id="GAP018_TEST", context_profile_version=1,
        effective_model="synthetic-test-model", tokenizer_id="synthetic-tokenizer",
        tokenizer_version="1", context_window=4096, reserved_output_tokens=512,
        reserved_system_tokens=128, available_context_tokens=3456,
        canon_version=manuscript.canon_version_or_revision,
        book_bible_version=manuscript.book_bible_version,
        style_version=None, memory_snapshot_id=None, graph_version=None,
        included_items=(item,), selection_summary=SelectionSummary(
            1, 1, 0, None, (), {}, False,
        ), total_tokens=4, created_at="2026-01-01T00:00:00Z",
    )
    repo.save_context_package(package, operation_id="gap018-qa-context")
    coverage = tuple(
        Coverage(level, criterion, manuscript.artifact_ref, manuscript.content_hash,
                 package.context_package_id, package.context_hash, "PASS")
        for level in MANDATORY_LEVELS for criterion in MANDATORY_CRITERIA
    )
    # This is a synthetic contract fixture, not evidence of a provider call.
    with repo.model_invocation_transaction("gap018-qa-invocation") as invocation:
        result = {"text": json.dumps({
            "manuscript_id": manuscript.manuscript_id,
            "content_hash": manuscript.content_hash,
            "manifest_hash": manuscript.manifest_hash,
            "coverage": [item.__dict__ for item in coverage],
            "findings": [],
        }), "refused": False}
        invocation.update({
            "project_id": repo.context.project_id, "book_id": repo.context.book_id,
            "context_package_id": package.context_package_id,
            "context_hash": package.context_hash,
            "status": "TRANSPORT_COMPLETED", "validation": "VALID",
            "result": result, "result_hash": fingerprint(result),
            "requested": {"model": "synthetic-test-model"},
            "effective_model": "synthetic-test-model", "attempts": [{"status": "RECEIVED"}],
        })
    qa_request = BookQARequest(
        repo.context.project_id, repo.context.book_id, manuscript.manuscript_id,
        manuscript.content_hash, manuscript.manifest_hash, "REQ-GAP018-QA",
        coverage, (), ("gap018-qa-invocation",),
    )
    report = create_book_qa_report(repo, qa_request)
    assert (report.execution_status, report.validation_status, report.quality_decision) == (
        "COMPLETED", "VALID", "ACCEPT",
    )
    assert create_book_qa_report(repo, qa_request) == report
    candidate_request = CandidateRequest(
        repo.context.project_id, repo.context.book_id, manuscript.manuscript_id,
        manuscript.content_hash, manuscript.manifest_hash, report.qa_id,
        report.report_hash, 1, "REQ-GAP018-CANDIDATE",
    )
    candidate = create_candidate(repo, candidate_request) if materialize_candidate else None
    if materialize_candidate:
        assert create_candidate(repo, candidate_request) == candidate
    return repo, root, manuscript, report, candidate, candidate_request


def test_book_qa_artifact_and_candidate_contract_reopen(case, monkeypatch):
    repo, root, manuscript, report, candidate, candidate_request = make_ready_candidate(case)
    reopened = ProjectRepository(StorageResolver(root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id,
    ))
    def forbidden_initialize():
        raise AssertionError("GET/read path must not initialize or migrate storage")
    monkeypatch.setattr(reopened, "initialize", forbidden_initialize)
    assert load_manuscript(reopened, manuscript.manuscript_id) == manuscript
    assert load_book_qa_report(reopened, report.qa_id) == report
    assert load_candidate(reopened, candidate.candidate_id) == candidate
    with pytest.raises(CandidateIntegrityError):
        create_candidate(repo, replace(candidate_request, report_hash=sha("wrong")))
    (root / "books" / repo.context.book_id / "book_bible.json").write_text("{}", encoding="utf-8")
    assert create_candidate(repo, candidate_request) == candidate
    with pytest.raises(CandidateIntegrityError, match="stale"):
        create_candidate(repo, replace(candidate_request, request_id="REQ-STALE"))


def test_new_manuscript_head_stales_old_candidate_without_changing_history(case):
    repo, _root, manuscript, report, candidate, old_request = make_ready_candidate(case)
    chapter_two = case[2](2)
    new_request = replace(case[3]((*manuscript.chapter_version_refs, chapter_two)),
                          version=2, parent_manuscript_id=manuscript.manuscript_id,
                          request_id="REQ-GAP018-V2")
    newer = seal_manuscript(repo, new_request)
    assert newer.version == 2
    assert repo.get_gap018_manuscript_head() == (newer.manuscript_id, 2, newer.manifest_hash)
    assert load_manuscript(repo, manuscript.manuscript_id) == manuscript
    assert load_candidate(repo, candidate.candidate_id) == candidate
    with pytest.raises(CandidateIntegrityError, match="stale"):
        create_candidate(repo, replace(old_request, request_id="REQ-OLD-NEW"))
    with pytest.raises(OperatorError, match="CANDIDATE_STALE"):
        issue_source_review(repo, candidate.candidate_id,
                            OperatorIdentity("operator-synthetic", "credential-synthetic", 1),
                            ttl_seconds=300)


def test_candidate_domain_commit_recovers_after_f004(case, monkeypatch):
    repo, _root, manuscript, report, _candidate, candidate_request = make_ready_candidate(
        case, materialize_candidate=False,
    )
    original_put = repo.put_gap018_record

    def crash_candidate(kind, record_id, value, *, connection):
        if kind == "CANDIDATE":
            raise InjectedRecoveryCrash()
        return original_put(kind, record_id, value, connection=connection)

    monkeypatch.setattr(repo, "put_gap018_record", crash_candidate)
    with pytest.raises(InjectedRecoveryCrash):
        create_candidate(repo, candidate_request)
    monkeypatch.setattr(repo, "put_gap018_record", original_put)
    recovered = create_candidate(repo, candidate_request)
    assert load_candidate(repo, recovered.candidate_id) == recovered


def test_book_qa_runs_via_p20_context_and_audited_transport(case, monkeypatch):
    repo, _root, chapter, request = case
    manuscript = seal_manuscript(repo, request((chapter(1),)))
    calls = []

    def fake_call_text(*, prompt, model, temperature, observer, response_schema=None):
        payload = json.loads(prompt)
        calls.append(model)
        coverage = [dict(payload["coverage_contract"], level=level, criterion=criterion)
                    for level in payload["required_levels"]
                    for criterion in payload["required_criteria"]]
        observer({"event": "START", "api": "responses", "sent": {"model": model}})
        observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
        return {"text": json.dumps({"manuscript_id": manuscript.manuscript_id,
                                    "content_hash": manuscript.content_hash,
                                    "manifest_hash": manuscript.manifest_hash,
                                    "coverage": coverage, "findings": []}),
                "refused": False, "provider_returned_model": model}

    monkeypatch.setattr("app.llm_provider_openai.call_text", fake_call_text)
    run_request = BookQARunRequest(repo.context.project_id, repo.context.book_id,
                                   manuscript.manuscript_id, manuscript.content_hash,
                                   manuscript.manifest_hash, "REQ-QA-RUN", "stub-model", "stub-model")
    report = run_book_qa(repo, run_request)
    assert report.quality_decision == "ACCEPT"
    assert run_book_qa(repo, run_request) == report
    assert calls == ["stub-model"]
    assert repo.get_context_package(report.coverage[0].context_ref) is not None
    with pytest.raises(Gap018RequestConflict):
        run_book_qa(repo, replace(run_request, effective_model="other-model"))


def test_200k_word_manuscript_uses_bounded_hierarchical_qa(case, monkeypatch):
    repo, _root, chapter, request = case
    text = "neutral " * 200000 + "ending"
    manuscript = seal_manuscript(repo, request((chapter(1, text=text),)))
    calls = []

    def fake_call_text(*, prompt, model, temperature, observer, response_schema=None):
        payload = json.loads(prompt)
        calls.append(payload)
        coverage = [dict(payload["coverage_contract"], level=level, criterion=criterion)
                    for level in payload["required_levels"]
                    for criterion in payload["required_criteria"]]
        observer({"event": "START", "api": "responses", "sent": {"model": model}})
        observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
        return {"text": json.dumps({
            "protocol": payload["protocol"], "qa_stage": payload["qa_stage"],
            "unit_id": payload["unit_id"], "input_refs": payload["input_refs"],
            "manuscript_id": payload["manuscript_id"],
            "content_hash": payload["content_hash"],
            "manifest_hash": payload["manifest_hash"],
            "coverage": coverage, "findings": [],
            "summary": "Synthetic chapter evidence bound to sealed input.",
        }), "refused": False, "provider_returned_model": model}

    monkeypatch.setattr("app.llm_provider_openai.call_text", fake_call_text)
    run_request = BookQARunRequest(repo.context.project_id, repo.context.book_id,
                                   manuscript.manuscript_id, manuscript.content_hash,
                                   manuscript.manifest_hash, "REQ-QA-200K", "stub-model", "stub-model")
    report = run_book_qa(repo, run_request)
    assert (report.execution_status, report.validation_status, report.quality_decision) == (
        "COMPLETED", "VALID", "ACCEPT",
    ), report.blockers
    assert len(report.coverage) == len(MANDATORY_LEVELS) * len(MANDATORY_CRITERIA)
    assert len(report.model_invocation_refs) == len(calls) > 50
    assert any(item["qa_stage"] == "SYNTH" for item in calls)
    assert all(item["context_package"]["total_tokens"] <=
               item["context_package"]["available_context_tokens"] for item in calls)
    assert all(not any(context_item["content_hash"] == manuscript.content_hash
                       for context_item in item["context_package"]["included_items"])
               for item in calls)
    assert run_book_qa(repo, run_request) == report
    assert len(calls) == len(report.model_invocation_refs)
    candidate = create_candidate(repo, CandidateRequest(
        repo.context.project_id, repo.context.book_id, manuscript.manuscript_id,
        manuscript.content_hash, manuscript.manifest_hash, report.qa_id,
        report.report_hash, 1, "REQ-200K-CANDIDATE",
    ))
    assert current_source_master(repo) is None
    actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
    review = issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
    assert current_source_master(repo) is None
    decision = {
        "request_id": "REQ-200K-SOURCE", "challenge_id": review["challenge_id"],
        "decision": "APPROVE", "candidate_hash": candidate.artifact_hash,
        "manuscript_hash": manuscript.content_hash,
        "manifest_hash": manuscript.manifest_hash,
        "qa_report_hash": report.report_hash,
        "expected_head": review["expected_head"],
    }
    source_result = record_source_decision(repo, candidate.candidate_id, decision, actor)
    assert source_result["status"] == "SOURCE_COMMITTED"
    reopened = ProjectRepository(StorageResolver(_root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id,
    ))
    assert current_source_master(reopened).source_master_id == source_result["source_master_id"]
    assert record_source_decision(reopened, candidate.candidate_id, decision, actor) == source_result


@pytest.mark.parametrize("issue_call", [2, 3])
def test_hierarchical_book_qa_never_averages_a_fundamental_issue(case, monkeypatch,
                                                                 issue_call):
    repo, _root, chapter, request = case
    manuscript = seal_manuscript(repo, request((chapter(1, text="neutral " * 4001),)))
    count = 0

    def fake_call_text(*, prompt, model, temperature, observer, response_schema=None):
        nonlocal count
        payload = json.loads(prompt)
        count += 1
        coverage = [dict(payload["coverage_contract"], level=level, criterion=criterion)
                    for level in payload["required_levels"]
                    for criterion in payload["required_criteria"]]
        findings = []
        if count == issue_call:
            next(item for item in coverage if item["level"] == "BOOK"
                 and item["criterion"] == "CANON")["result"] = "ISSUE"
            findings = [dict(payload["finding_contract"],
                             finding_id="fundamental-synthetic", level="BOOK",
                             criterion="CANON", location="chapter_1/word_4001",
                             classification="fundamental", must_fix=True)]
        observer({"event": "START", "api": "responses", "sent": {"model": model}})
        observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
        return {"text": json.dumps({
            "protocol": payload["protocol"], "qa_stage": payload["qa_stage"],
            "unit_id": payload["unit_id"], "input_refs": payload["input_refs"],
            "manuscript_id": payload["manuscript_id"],
            "content_hash": payload["content_hash"],
            "manifest_hash": payload["manifest_hash"],
            "coverage": coverage, "findings": findings,
            "summary": "Synthetic source-bound assessment.",
        }), "refused": False, "provider_returned_model": model}

    monkeypatch.setattr("app.llm_provider_openai.call_text", fake_call_text)
    report = run_book_qa(repo, BookQARunRequest(
        repo.context.project_id, repo.context.book_id, manuscript.manuscript_id,
        manuscript.content_hash, manuscript.manifest_hash, "REQ-QA-HIER-ISSUE",
        "stub-model", "stub-model",
    ))
    assert count == 3
    assert (report.execution_status, report.validation_status,
            report.quality_decision) == ("COMPLETED", "VALID", "REJECT"), report.blockers
    assert len(report.findings) == 1
    producing_operation = next(
        ref for ref in report.model_invocation_refs
        if report.findings[0].finding_id.startswith(sha(ref)[:12] + ":")
    )
    producing_invocation = json.loads(repo.get_metadata_readonly(
        "model_invocation.v1:" + producing_operation))
    assert (report.findings[0].context_ref, report.findings[0].context_hash) == (
        producing_invocation["context_package_id"], producing_invocation["context_hash"],
    )
    assert (report.findings[0].source_ref,
            report.findings[0].source_hash) == (
                manuscript.chapter_version_refs[0].artifact_ref,
                manuscript.chapter_version_refs[0].artifact_sha256,
            )
    issue_coverage = next(item for item in report.coverage if item.level == "BOOK"
                          and item.criterion == "CANON")
    assert issue_coverage.result == "ISSUE"
    issue_invocation = json.loads(repo.get_metadata_readonly(
        "model_invocation.v1:" + report.model_invocation_refs[issue_call - 1],
    ))
    assert issue_coverage.context_ref == issue_invocation["context_package_id"]


def test_book_qa_invalid_model_output_is_blocked_and_replay_does_not_recall(case, monkeypatch):
    repo, _root, chapter, request = case
    manuscript = seal_manuscript(repo, request((chapter(1),)))
    calls = []

    def invalid_call(*, prompt, model, temperature, observer, response_schema=None):
        calls.append(model)
        observer({"event": "START", "api": "responses", "sent": {"model": model}})
        observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
        return {"text": "{}", "refused": False, "provider_returned_model": model}

    monkeypatch.setattr("app.llm_provider_openai.call_text", invalid_call)
    run_request = BookQARunRequest(repo.context.project_id, repo.context.book_id,
                                   manuscript.manuscript_id, manuscript.content_hash,
                                   manuscript.manifest_hash, "REQ-QA-INVALID", "stub-model", "stub-model")
    blocked = run_book_qa(repo, run_request)
    assert blocked.execution_status == "BLOCKED"
    assert blocked.validation_status == "INVALID"
    assert blocked.quality_decision is None
    assert run_book_qa(repo, run_request) == blocked
    assert calls == ["stub-model"]


def test_book_qa_context_overflow_is_explicit_blocked_without_model_call(case, monkeypatch):
    repo, _root, chapter, request = case
    manuscript = seal_manuscript(repo, request((chapter(1),)))

    def overflow(*args, **kwargs):
        raise ContextOverflowError(available_tokens=1, mandatory_tokens=2)

    monkeypatch.setattr(ContextBuilder, "build", overflow)
    run_request = BookQARunRequest(repo.context.project_id, repo.context.book_id,
                                   manuscript.manuscript_id, manuscript.content_hash,
                                   manuscript.manifest_hash, "REQ-QA-OVERFLOW", "stub-model", "stub-model")
    report = run_book_qa(repo, run_request)
    assert (report.execution_status, report.validation_status, report.quality_decision) == (
        "BLOCKED", "INVALID", None,
    )
    assert "CONTEXT_OVERFLOW" in report.blockers
    assert run_book_qa(repo, run_request) == report


def test_book_qa_unknown_policy_is_blocked_without_model_call(case, monkeypatch):
    repo, _root, chapter, request = case
    manuscript = seal_manuscript(repo, request((chapter(1),)))

    def forbidden_call(**kwargs):
        raise AssertionError("unknown policy must not invoke a model")

    monkeypatch.setattr("app.llm_provider_openai.call_text", forbidden_call)
    run_request = BookQARunRequest(repo.context.project_id, repo.context.book_id,
                                   manuscript.manuscript_id, manuscript.content_hash,
                                   manuscript.manifest_hash, "REQ-QA-UNKNOWN-POLICY",
                                   "stub-model", "stub-model", policy_version="UNKNOWN")
    report = run_book_qa(repo, run_request)
    assert report.execution_status == "BLOCKED"
    assert report.quality_decision is None
    assert "POLICY_UNKNOWN" in report.blockers
    assert run_book_qa(repo, run_request) == report


def test_book_qa_uncertain_model_attempt_requires_explicit_recovery(case, monkeypatch):
    repo, _root, chapter, request = case
    manuscript = seal_manuscript(repo, request((chapter(1),)))

    def uncertain(*args, **kwargs):
        raise InvocationRecoveryRequired("remote outcome unknown")

    monkeypatch.setattr(ModelInvocationAudit, "call", uncertain)
    run_request = BookQARunRequest(repo.context.project_id, repo.context.book_id,
                                   manuscript.manuscript_id, manuscript.content_hash,
                                   manuscript.manifest_hash, "REQ-QA-UNCERTAIN",
                                   "stub-model", "stub-model")
    with pytest.raises(BookQARecoveryRequired) as exc:
        run_book_qa(repo, run_request)
    assert exc.value.operation_id.startswith("context:")


def test_author_challenge_approval_source_commit_and_replay(case):
    repo, root, manuscript, report, candidate, _candidate_request = make_ready_candidate(case)
    actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
    review = issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
    assert current_source_master(repo) is None
    request = {
        "request_id": "REQ-SOURCE-1", "challenge_id": review["challenge_id"],
        "decision": "APPROVE", "candidate_hash": candidate.artifact_hash,
        "manuscript_hash": manuscript.content_hash,
        "manifest_hash": manuscript.manifest_hash,
        "qa_report_hash": report.report_hash,
        "expected_head": review["expected_head"],
    }
    result = record_source_decision(repo, candidate.candidate_id, request, actor)
    assert result["status"] == "SOURCE_COMMITTED"
    assert result["source_master_id"].startswith("SM-")
    promotion = repo.get_gap018_record("PROMOTION", result["operation_id"])
    assert promotion["execution_status"] == "SOURCE_COMMITTED"
    assert (promotion["source_master_id"], promotion["artifact_hash"]) == (
        result["source_master_id"], result["artifact_hash"])
    assert record_source_decision(repo, candidate.candidate_id, request, actor) == result
    assert recover_source_promotion(repo, result["operation_id"]) == result
    source = load_source_master(repo, result["source_master_id"])
    assert source.approved_by_user is True
    assert source.artifact_hash == result["artifact_hash"]
    source_events, _ = repo.list_memory_events(
        operation=("CROSS_STORE_OPERATION", result["operation_id"]),
    )
    assert {event.event_type for event in source_events} >= {
        "ARTIFACT_WRITE_INTENDED", "ARTIFACT_WRITE_CONFIRMED",
    }
    reopened = ProjectRepository(StorageResolver(root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id,
    ))
    assert current_source_master(reopened) == source
    other = ProjectRepository(StorageResolver(root).resolve_project(
        "PROJ-GAP018-OTHER", book_id="BOOK-GAP018-OTHER",
    ))
    other.initialize()
    assert other.get_gap018_source_head() == (None, None, None)
    assert other.get_gap018_record("SOURCE", source.source_master_id) is None
    assert other.get_gap018_record("APPROVAL", result["approval_id"]) is None
    assert other.get_gap018_record("CHALLENGE", review["challenge_id"]) is None
    assert current_source_master(other) is None
    with pytest.raises(OperatorError):
        load_source_master(other, source.source_master_id)
    with pytest.raises((OperatorError, CandidateIntegrityError)):
        issue_source_review(other, candidate.candidate_id, actor, ttl_seconds=300)
    with pytest.raises((OperatorError, CandidateIntegrityError)):
        record_source_decision(other, candidate.candidate_id, request, actor)
    with pytest.raises(OperatorError, match="SOURCE_OPERATION_NOT_FOUND"):
        recover_source_promotion(other, result["operation_id"])
    with pytest.raises(OperatorError) as conflict:
        record_source_decision(repo, candidate.candidate_id,
                               {**request, "decision": "REJECT"}, actor)
    assert conflict.value.status == 409


def test_author_reject_never_creates_source(case):
    repo, root, manuscript, report, candidate, _candidate_request = make_ready_candidate(case)
    actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
    review = issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
    request = {
        "request_id": "REQ-SOURCE-REJECT", "challenge_id": review["challenge_id"],
        "decision": "REJECT", "candidate_hash": candidate.artifact_hash,
        "manuscript_hash": manuscript.content_hash,
        "manifest_hash": manuscript.manifest_hash,
        "qa_report_hash": report.report_hash,
        "expected_head": review["expected_head"],
    }
    assert record_source_decision(repo, candidate.candidate_id, request, actor)["status"] == "APPROVAL_RECORDED"
    assert current_source_master(repo) is None
    reopened = ProjectRepository(StorageResolver(root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id,
    ))
    assert current_source_master(reopened) is None


def test_rejected_candidate_cannot_be_approved_with_an_existing_or_new_challenge(case):
    repo, _root, manuscript, report, candidate, _ = make_ready_candidate(case)
    actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
    rejected_review = issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
    pending_review = issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)

    def decision(review, request_id, choice):
        return {
            "request_id": request_id, "challenge_id": review["challenge_id"],
            "decision": choice, "candidate_hash": candidate.artifact_hash,
            "manuscript_hash": manuscript.content_hash,
            "manifest_hash": manuscript.manifest_hash,
            "qa_report_hash": report.report_hash,
            "expected_head": review["expected_head"],
        }

    rejected = decision(rejected_review, "REQ-REJECT-TERMINAL", "REJECT")
    assert record_source_decision(repo, candidate.candidate_id, rejected, actor)["decision"] == "REJECT"
    with pytest.raises(OperatorError, match="CANDIDATE_REJECTED"):
        issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
    with pytest.raises(OperatorError, match="CANDIDATE_REJECTED"):
        record_source_decision(repo, candidate.candidate_id,
                               decision(pending_review, "REQ-APPROVE-AFTER-REJECT", "APPROVE"), actor)
    assert current_source_master(repo) is None


def test_source_challenge_fences_actor_hash_expiry_and_reuse(case, monkeypatch):
    repo, _root, manuscript, report, candidate, _ = make_ready_candidate(case)
    actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
    review = issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
    decision = {
        "request_id": "REQ-CHALLENGE-NEGATIVE", "challenge_id": review["challenge_id"],
        "decision": "REJECT", "candidate_hash": candidate.artifact_hash,
        "manuscript_hash": manuscript.content_hash,
        "manifest_hash": manuscript.manifest_hash,
        "qa_report_hash": report.report_hash,
        "expected_head": review["expected_head"],
    }
    with pytest.raises(OperatorError, match="INVALID_SOURCE_CHALLENGE"):
        record_source_decision(repo, candidate.candidate_id,
                               {**decision, "challenge_id": "challenge-missing"}, actor)
    with pytest.raises(OperatorError, match="SOURCE_DECISION_BINDING_MISMATCH"):
        record_source_decision(repo, candidate.candidate_id,
                               {**decision, "candidate_hash": sha("wrong")}, actor)
    with pytest.raises(OperatorError, match="SOURCE_CHALLENGE_ACTOR_MISMATCH"):
        record_source_decision(repo, candidate.candidate_id, decision,
                               OperatorIdentity("operator-other", "credential-other", 1))
    expiry = issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=1)
    original_time = source_promotion_module.time.time
    now = original_time()
    monkeypatch.setattr(source_promotion_module.time, "time", lambda: now + 2)
    with pytest.raises(OperatorError, match="SOURCE_CHALLENGE_EXPIRED"):
        record_source_decision(repo, candidate.candidate_id,
                               {**decision, "challenge_id": expiry["challenge_id"],
                                "request_id": "REQ-EXPIRED"}, actor)
    monkeypatch.setattr(source_promotion_module.time, "time", original_time)
    assert record_source_decision(repo, candidate.candidate_id, decision, actor)["decision"] == "REJECT"
    with pytest.raises(OperatorError, match="INVALID_SOURCE_CHALLENGE"):
        record_source_decision(repo, candidate.candidate_id,
                               {**decision, "request_id": "REQ-REUSE"}, actor)
    assert current_source_master(repo) is None


def test_source_recovery_intervention_is_not_reported_as_retryable(case, monkeypatch):
    repo, _root, manuscript, report, candidate, _ = make_ready_candidate(case)
    actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
    review = issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
    decision = {
        "request_id": "REQ-SOURCE-INTERVENTION", "challenge_id": review["challenge_id"],
        "decision": "APPROVE", "candidate_hash": candidate.artifact_hash,
        "manuscript_hash": manuscript.content_hash,
        "manifest_hash": manuscript.manifest_hash,
        "qa_report_hash": report.report_hash,
        "expected_head": review["expected_head"],
    }

    class Intervention:
        def __init__(self, repository):
            self.repository = repository

        def execute(self, plan):
            raise RecoveryInterventionRequired("verified F-004 evidence is insufficient")

    monkeypatch.setattr(source_promotion_module, "CrossStoreRecoveryService", Intervention)
    result = record_source_decision(repo, candidate.candidate_id, decision, actor)
    assert result["status"] == "NEEDS_INTERVENTION"
    assert repo.get_gap018_record("PROMOTION", result["operation_id"])["execution_status"] == "NEEDS_INTERVENTION"
    assert repo.get_gap018_record("APPROVAL", result["approval_id"])["decision"] == "APPROVE"
    assert current_source_master(repo) is None


@pytest.mark.parametrize("point", [FaultPoint.AFTER_INTENT,
                                   FaultPoint.AFTER_PROJECT_WRITE,
                                   FaultPoint.AFTER_ARTIFACT_PREPARE,
                                   FaultPoint.AFTER_ARTIFACT_COMMIT,
                                   FaultPoint.AFTER_LOGICAL_COMMIT])
def test_source_recovery_after_f004_crash_preserves_approval(case, monkeypatch, point):
    repo, root, manuscript, report, candidate, _ = make_ready_candidate(case)
    actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
    review = issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
    decision = {
        "request_id": "REQ-SOURCE-CRASH", "challenge_id": review["challenge_id"],
        "decision": "APPROVE", "candidate_hash": candidate.artifact_hash,
        "manuscript_hash": manuscript.content_hash,
        "manifest_hash": manuscript.manifest_hash,
        "qa_report_hash": report.report_hash,
        "expected_head": review["expected_head"],
    }

    def crash(selected):
        if selected == point:
            raise InjectedRecoveryCrash()

    monkeypatch.setattr(source_promotion_module, "CrossStoreRecoveryService",
                        lambda repository: CrossStoreRecoveryService(repository, fault_hook=crash))
    with pytest.raises(InjectedRecoveryCrash):
        record_source_decision(repo, candidate.candidate_id, decision, actor)
    monkeypatch.setattr(source_promotion_module, "CrossStoreRecoveryService",
                        CrossStoreRecoveryService)
    reopened = ProjectRepository(StorageResolver(root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id,
    ))
    assert current_source_master(reopened) is None
    receipt_id = "decision-" + sha(decision["request_id"])
    receipt = reopened.get_gap018_record("REQUEST", receipt_id)
    assert receipt is not None
    assert reopened.get_gap018_record("APPROVAL", receipt["approval_id"])["decision"] == "APPROVE"
    assert reopened.get_gap018_record("PROMOTION", receipt["operation_id"])["execution_status"] == "SOURCE_PENDING"
    result = recover_source_promotion(reopened, receipt["operation_id"])
    assert result["status"] == "SOURCE_COMMITTED"
    assert reopened.get_gap018_record("PROMOTION", receipt["operation_id"])["execution_status"] == "SOURCE_COMMITTED"
    assert current_source_master(reopened).source_master_id == result["source_master_id"]


def test_source_recovery_after_f004_commit_before_domain_cas(case, monkeypatch):
    repo, root, manuscript, report, candidate, _ = make_ready_candidate(case)
    actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
    review = issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
    decision = {
        "request_id": "REQ-SOURCE-DOMAIN-CRASH", "challenge_id": review["challenge_id"],
        "decision": "APPROVE", "candidate_hash": candidate.artifact_hash,
        "manuscript_hash": manuscript.content_hash,
        "manifest_hash": manuscript.manifest_hash,
        "qa_report_hash": report.report_hash,
        "expected_head": review["expected_head"],
    }
    original_cas = repo.cas_gap018_source_head

    def crash_cas(*args, **kwargs):
        raise InjectedRecoveryCrash()

    monkeypatch.setattr(repo, "cas_gap018_source_head", crash_cas)
    with pytest.raises(InjectedRecoveryCrash):
        record_source_decision(repo, candidate.candidate_id, decision, actor)
    monkeypatch.setattr(repo, "cas_gap018_source_head", original_cas)
    reopened = ProjectRepository(StorageResolver(root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id,
    ))
    assert current_source_master(reopened) is None
    receipt = reopened.get_gap018_record("REQUEST", "decision-" + sha(decision["request_id"]))
    assert reopened.get_cross_store_operation_readonly(receipt["operation_id"])["status"] == "COMMITTED"
    result = recover_source_promotion(reopened, receipt["operation_id"])
    assert result["status"] == "SOURCE_COMMITTED"
    assert current_source_master(reopened).source_master_id == result["source_master_id"]


@pytest.mark.parametrize("point", ["BEFORE_APPROVAL", "BEFORE_INTENT",
                                   "BEFORE_SOURCE_RECORD", "AFTER_DOMAIN_COMMIT"])
def test_source_fault_boundaries_reopen_to_one_result(case, monkeypatch, point):
    repo, root, manuscript, report, candidate, _ = make_ready_candidate(case)
    actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
    review = issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
    decision = {
        "request_id": "REQ-SOURCE-BOUNDARY-" + point,
        "challenge_id": review["challenge_id"], "decision": "APPROVE",
        "candidate_hash": candidate.artifact_hash,
        "manuscript_hash": manuscript.content_hash,
        "manifest_hash": manuscript.manifest_hash,
        "qa_report_hash": report.report_hash,
        "expected_head": review["expected_head"],
    }
    original_put = repo.put_gap018_record
    original_service = source_promotion_module.CrossStoreRecoveryService
    original_recover = source_promotion_module.recover_source_promotion

    def fail_put(kind, record_id, value, *, connection=None):
        if ((point == "BEFORE_APPROVAL" and kind == "APPROVAL")
                or (point == "BEFORE_SOURCE_RECORD" and kind == "SOURCE")):
            raise InjectedRecoveryCrash()
        return original_put(kind, record_id, value, connection=connection)

    class FailBeforeIntent:
        def __init__(self, repository):
            pass

        def execute(self, plan):
            raise InjectedRecoveryCrash()

    def fail_after_commit(repository, operation_id):
        original_recover(repository, operation_id)
        raise InjectedRecoveryCrash()

    if point in {"BEFORE_APPROVAL", "BEFORE_SOURCE_RECORD"}:
        monkeypatch.setattr(repo, "put_gap018_record", fail_put)
    elif point == "BEFORE_INTENT":
        monkeypatch.setattr(source_promotion_module, "CrossStoreRecoveryService", FailBeforeIntent)
    else:
        monkeypatch.setattr(source_promotion_module, "recover_source_promotion", fail_after_commit)
    with pytest.raises(InjectedRecoveryCrash):
        record_source_decision(repo, candidate.candidate_id, decision, actor)
    monkeypatch.setattr(repo, "put_gap018_record", original_put)
    monkeypatch.setattr(source_promotion_module, "CrossStoreRecoveryService", original_service)
    monkeypatch.setattr(source_promotion_module, "recover_source_promotion", original_recover)

    reopened = ProjectRepository(StorageResolver(root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id,
    ))
    receipt = reopened.get_gap018_record("REQUEST", "decision-" + sha(decision["request_id"]))
    if point == "BEFORE_APPROVAL":
        assert receipt is None
        assert current_source_master(reopened) is None
    else:
        assert receipt is not None
        assert reopened.get_gap018_record("APPROVAL", receipt["approval_id"])["decision"] == "APPROVE"
    result = record_source_decision(reopened, candidate.candidate_id, decision, actor)
    assert result["status"] == "SOURCE_COMMITTED"
    assert record_source_decision(reopened, candidate.candidate_id, decision, actor) == result
    assert current_source_master(reopened).source_master_id == result["source_master_id"]
    with sqlite3.connect(reopened.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM gap018_records WHERE record_kind='SOURCE'").fetchone()[0] == 1


def test_parallel_approvals_use_cas_and_keep_one_current_source(case):
    repo, root, manuscript, report, candidate, _ = make_ready_candidate(case)
    actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
    reviews = [issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
               for _ in range(2)]
    decisions = [{
        "request_id": f"REQ-PARALLEL-{index}",
        "challenge_id": review["challenge_id"], "decision": "APPROVE",
        "candidate_hash": candidate.artifact_hash,
        "manuscript_hash": manuscript.content_hash,
        "manifest_hash": manuscript.manifest_hash,
        "qa_report_hash": report.report_hash,
        "expected_head": review["expected_head"],
    } for index, review in enumerate(reviews)]
    barrier = Barrier(2)

    def approve(decision):
        opened = ProjectRepository(StorageResolver(root).resolve_project(
            repo.context.project_id, book_id=repo.context.book_id,
        ))
        barrier.wait(timeout=10)
        try:
            return record_source_decision(opened, candidate.candidate_id, decision, actor)
        except OperatorError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(approve, decisions))
    committed = [item for item in results if isinstance(item, dict)
                 and item.get("status") == "SOURCE_COMMITTED"]
    assert len(committed) == 1, results
    assert any((item in {"SOURCE_HEAD_CHANGED", "SOURCE_CONFLICT"}
                if isinstance(item, str) else item.get("status") == "SOURCE_CONFLICT")
               for item in results), results
    reopened = ProjectRepository(StorageResolver(root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id,
    ))
    assert current_source_master(reopened).source_master_id == committed[0]["source_master_id"]
    with sqlite3.connect(reopened.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM gap018_records WHERE record_kind='SOURCE'").fetchone()[0] == 1


def test_competing_approved_promotions_preserve_winning_source_head(case, monkeypatch):
    repo, root, manuscript, report, candidate, _ = make_ready_candidate(case)
    actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
    reviews = [issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
               for _ in range(2)]

    def decision(review, request_id):
        return {
            "request_id": request_id, "challenge_id": review["challenge_id"],
            "decision": "APPROVE", "candidate_hash": candidate.artifact_hash,
            "manuscript_hash": manuscript.content_hash,
            "manifest_hash": manuscript.manifest_hash,
            "qa_report_hash": report.report_hash,
            "expected_head": review["expected_head"],
        }

    first, second = decision(reviews[0], "REQ-CONFLICT-A"), decision(reviews[1], "REQ-CONFLICT-B")
    original_cas = repo.cas_gap018_source_head

    def stop_first(*args, **kwargs):
        raise InjectedRecoveryCrash()

    monkeypatch.setattr(repo, "cas_gap018_source_head", stop_first)
    with pytest.raises(InjectedRecoveryCrash):
        record_source_decision(repo, candidate.candidate_id, first, actor)
    monkeypatch.setattr(repo, "cas_gap018_source_head", original_cas)
    winner = record_source_decision(repo, candidate.candidate_id, second, actor)
    assert winner["status"] == "SOURCE_COMMITTED"
    reopened = ProjectRepository(StorageResolver(root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id,
    ))
    first_receipt = reopened.get_gap018_record("REQUEST", "decision-" + sha(first["request_id"]))
    loser = recover_source_promotion(reopened, first_receipt["operation_id"])
    assert loser["status"] == "SOURCE_CONFLICT"
    assert reopened.get_gap018_record("PROMOTION", first_receipt["operation_id"])["execution_status"] == "SOURCE_CONFLICT"
    assert recover_source_promotion(reopened, first_receipt["operation_id"]) == loser
    assert current_source_master(reopened).source_master_id == winner["source_master_id"]


def test_separate_approvals_create_source_v2_and_preserve_v1_history(case):
    repo, root, manuscript, report, candidate, _ = make_ready_candidate(case)
    actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)

    def approve(target, request_id):
        review = issue_source_review(repo, target.candidate_id, actor, ttl_seconds=300)
        decision = {
            "request_id": request_id, "challenge_id": review["challenge_id"],
            "decision": "APPROVE", "candidate_hash": target.artifact_hash,
            "manuscript_hash": manuscript.content_hash,
            "manifest_hash": manuscript.manifest_hash,
            "qa_report_hash": target.report_hash,
            "expected_head": review["expected_head"],
        }
        return record_source_decision(repo, target.candidate_id, decision, actor), decision

    first, first_decision = approve(candidate, "REQ-SOURCE-V1")
    qa2 = create_book_qa_report(repo, BookQARequest(
        repo.context.project_id, repo.context.book_id, manuscript.manuscript_id,
        manuscript.content_hash, manuscript.manifest_hash, "REQ-QA-REEVALUATION",
        report.coverage, report.findings, report.model_invocation_refs,
        reevaluation_of=report.qa_id,
    ))
    candidate2 = create_candidate(repo, CandidateRequest(
        repo.context.project_id, repo.context.book_id, manuscript.manuscript_id,
        manuscript.content_hash, manuscript.manifest_hash, qa2.qa_id,
        qa2.report_hash, 2, "REQ-CANDIDATE-V2", candidate.candidate_id,
    ))
    second, _ = approve(candidate2, "REQ-SOURCE-V2")
    assert second["status"] == "SOURCE_COMMITTED"
    reopened = ProjectRepository(StorageResolver(root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id,
    ))
    source1 = load_source_master(reopened, first["source_master_id"])
    source2 = load_source_master(reopened, second["source_master_id"])
    assert (source1.version, source2.version) == (1, 2)
    assert source2.parent_source_master_id == source1.source_master_id
    assert current_source_master(reopened) == source2
    assert record_source_decision(reopened, candidate.candidate_id, first_decision, actor) == first
    assert current_source_master(reopened) == source2


def test_operator_http_full_gap018_flow_and_project_boundary(case, monkeypatch, tmp_path):
    if os.name != "nt":
        pytest.skip("local operator uses Windows DPAPI")
    repo, root, chapter, request = case
    system = ensure_system_repository()
    system.bind_project(repo.context.project_id, repo.context.book_id)
    secret = tmp_path / "operator-security" / "operator-gap018.dpapi"
    initialize_operator(system, secret)
    auth = {"Authorization": "Bearer " + read_secret(secret)}
    selected = request((chapter(1),))
    calls = []

    def fake_call_text(*, prompt, model, temperature, observer, response_schema=None):
        payload = json.loads(prompt)
        calls.append(model)
        coverage = [dict(payload["coverage_contract"], level=level, criterion=criterion)
                    for level in payload["required_levels"]
                    for criterion in payload["required_criteria"]]
        observer({"event": "START", "api": "responses", "sent": {"model": model}})
        observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
        return {"text": json.dumps({"manuscript_id": payload["manuscript_id"],
                                    "content_hash": payload["content_hash"],
                                    "manifest_hash": payload["manifest_hash"],
                                    "coverage": coverage, "findings": []}),
                "refused": False, "provider_returned_model": model}

    monkeypatch.setattr("app.llm_provider_openai.call_text", fake_call_text)
    base = f"/operator/projects/{repo.context.project_id}/books/{repo.context.book_id}"
    seal_body = asdict(selected)
    seal_body.pop("project_id")
    seal_body.pop("book_id")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
        assert client.get(base + "/source-masters/current").status_code == 401
        assert client.get(base + "/source-masters/current", headers=auth).json() is None
        assert client.post(base.replace(repo.context.book_id, "BOOK-FOREIGN") + "/manuscripts",
                           headers=auth, json=seal_body).status_code == 403
        sealed_response = client.post(base + "/manuscripts", headers=auth, json=seal_body)
        assert sealed_response.status_code == 200, sealed_response.text
        sealed = sealed_response.json()
        assert client.get(base + "/manuscripts/" + sealed["manuscript_id"],
                          headers=auth).status_code == 200
        qa_body = {"manuscript_id": sealed["manuscript_id"], "content_hash": sealed["content_hash"],
                   "manifest_hash": sealed["manifest_hash"], "request_id": "REQ-HTTP-QA",
                   "model": "stub-model", "policy_version": POLICY_VERSION,
                   "policy_hash": POLICY_HASH, "criteria_version": CRITERIA_VERSION,
                   "criteria_hash": CRITERIA_HASH}
        qa_response = client.post(base + "/book-qa", headers=auth, json=qa_body)
        assert qa_response.status_code == 200, qa_response.text
        qa = qa_response.json()
        assert qa["quality_decision"] == "ACCEPT"
        assert client.post(base + "/book-qa", headers=auth, json=qa_body).json() == qa
        assert calls == ["stub-model"]
        candidate_response = client.post(base + "/candidate-masters", headers=auth, json={
            "manuscript_id": sealed["manuscript_id"], "content_hash": sealed["content_hash"],
            "manifest_hash": sealed["manifest_hash"], "qa_id": qa["qa_id"],
            "report_hash": qa["report_hash"], "candidate_version": 1,
            "request_id": "REQ-HTTP-CANDIDATE",
        })
        assert candidate_response.status_code == 200, candidate_response.text
        candidate = candidate_response.json()
        assert client.get(base + "/source-masters/current", headers=auth).json() is None
        review_response = client.post(base + "/candidate-masters/" + candidate["candidate_id"] + "/review",
                                      headers=auth)
        assert review_response.status_code == 200, review_response.text
        review = review_response.json()
        decision = {"request_id": "REQ-HTTP-SOURCE", "challenge_id": review["challenge_id"],
                    "decision": "APPROVE", "candidate_hash": review["candidate_hash"],
                    "manuscript_hash": review["manuscript_hash"],
                    "manifest_hash": review["manifest_hash"],
                    "qa_report_hash": review["qa_report_hash"],
                    "expected_head": review["expected_head"]}
        source_response = client.post(base + "/candidate-masters/" + candidate["candidate_id"] + "/decision",
                                      headers=auth, json=decision)
        assert source_response.status_code == 200, source_response.text
        source = source_response.json()
        assert source["status"] == "SOURCE_COMMITTED"
        assert client.post(base + "/candidate-masters/" + candidate["candidate_id"] + "/decision",
                           headers=auth, json=decision).json() == source
        assert client.get(base + "/source-masters/current", headers=auth).json()["source_master_id"] == source["source_master_id"]


def test_http_response_loss_after_source_commit_replays_one_receipt(case, monkeypatch, tmp_path):
    if os.name != "nt":
        pytest.skip("local operator uses Windows DPAPI")
    repo, root, manuscript, report, candidate, _ = make_ready_candidate(case)
    system = ensure_system_repository()
    system.bind_project(repo.context.project_id, repo.context.book_id)
    secret = tmp_path / "operator-security" / "operator-gap018-lost-response.dpapi"
    initialize_operator(system, secret)
    auth = {"Authorization": "Bearer " + read_secret(secret)}
    base = f"/operator/projects/{repo.context.project_id}/books/{repo.context.book_id}"
    original_recover = source_promotion_module.recover_source_promotion

    def lose_response(repository, operation_id):
        original_recover(repository, operation_id)
        raise RuntimeError("SIMULATED_HTTP_RESPONSE_LOSS")

    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1),
                    raise_server_exceptions=False) as client:
        review_response = client.post(base + "/candidate-masters/" + candidate.candidate_id
                                      + "/review", headers=auth)
        assert review_response.status_code == 200, review_response.text
        review = review_response.json()
        decision = {
            "request_id": "REQ-HTTP-LOST-RESPONSE",
            "challenge_id": review["challenge_id"], "decision": "APPROVE",
            "candidate_hash": candidate.artifact_hash,
            "manuscript_hash": manuscript.content_hash,
            "manifest_hash": manuscript.manifest_hash,
            "qa_report_hash": report.report_hash,
            "expected_head": review["expected_head"],
        }
        monkeypatch.setattr(source_promotion_module, "recover_source_promotion", lose_response)
        first = client.post(base + "/candidate-masters/" + candidate.candidate_id
                            + "/decision", headers=auth, json=decision)
        assert first.status_code == 500
        monkeypatch.setattr(source_promotion_module, "recover_source_promotion", original_recover)
        reopened = ProjectRepository(StorageResolver(root).resolve_project(
            repo.context.project_id, book_id=repo.context.book_id,
        ))
        source = current_source_master(reopened)
        assert source is not None
        replay = client.post(base + "/candidate-masters/" + candidate.candidate_id
                             + "/decision", headers=auth, json=decision)
        assert replay.status_code == 200, replay.text
        assert replay.json()["source_master_id"] == source.source_master_id
        assert client.get(base + "/source-masters/current", headers=auth).json()[
            "source_master_id"] == source.source_master_id
        with sqlite3.connect(reopened.db_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM gap018_records WHERE record_kind='SOURCE'").fetchone()[0] == 1


def test_operator_http_book_qa_revise_reject_never_create_candidate_or_source(case, monkeypatch, tmp_path):
    if os.name != "nt":
        pytest.skip("local operator uses Windows DPAPI")
    repo, _root, chapter, request = case
    system = ensure_system_repository()
    system.bind_project(repo.context.project_id, repo.context.book_id)
    secret = tmp_path / "operator-security" / "operator-gap018-qa.dpapi"
    initialize_operator(system, secret)
    auth = {"Authorization": "Bearer " + read_secret(secret)}
    manuscript = seal_manuscript(repo, request((chapter(1),)))
    classifications = iter(("repairable", "fundamental"))

    def fake_call_text(*, prompt, model, temperature, observer, response_schema=None):
        payload = json.loads(prompt)
        classification = next(classifications)
        coverage = [dict(payload["coverage_contract"], level=level, criterion=criterion)
                    for level in payload["required_levels"]
                    for criterion in payload["required_criteria"]]
        issue = next(item for item in coverage if item["level"] == "BOOK" and item["criterion"] == "CANON")
        issue["result"] = "ISSUE"
        finding = dict(payload["finding_contract"], level="BOOK", criterion="CANON",
                       finding_id="F-" + classification, classification=classification)
        observer({"event": "START", "api": "responses", "sent": {"model": model}})
        observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED"})
        return {"text": json.dumps({"manuscript_id": manuscript.manuscript_id,
                                    "content_hash": manuscript.content_hash,
                                    "manifest_hash": manuscript.manifest_hash,
                                    "coverage": coverage, "findings": [finding]}),
                "refused": False, "provider_returned_model": model}

    monkeypatch.setattr("app.llm_provider_openai.call_text", fake_call_text)
    base = f"/operator/projects/{repo.context.project_id}/books/{repo.context.book_id}"
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
        for decision in ("REVISE", "REJECT"):
            qa_response = client.post(base + "/book-qa", headers=auth, json={
                "manuscript_id": manuscript.manuscript_id,
                "content_hash": manuscript.content_hash,
                "manifest_hash": manuscript.manifest_hash,
                "request_id": "REQ-HTTP-QA-" + decision,
                "model": "stub-model", "policy_version": POLICY_VERSION,
                "policy_hash": POLICY_HASH, "criteria_version": CRITERIA_VERSION,
                "criteria_hash": CRITERIA_HASH,
            })
            assert qa_response.status_code == 200, qa_response.text
            qa = qa_response.json()
            assert (qa["execution_status"], qa["validation_status"],
                    qa["quality_decision"]) == ("COMPLETED", "VALID", decision)
            candidate_response = client.post(base + "/candidate-masters", headers=auth, json={
                "manuscript_id": manuscript.manuscript_id,
                "content_hash": manuscript.content_hash,
                "manifest_hash": manuscript.manifest_hash,
                "qa_id": qa["qa_id"], "report_hash": qa["report_hash"],
                "candidate_version": 1, "request_id": "REQ-NO-CANDIDATE-" + decision,
            })
            assert candidate_response.status_code == 409
        blocked_response = client.post(base + "/book-qa", headers=auth, json={
            "manuscript_id": manuscript.manuscript_id,
            "content_hash": manuscript.content_hash,
            "manifest_hash": manuscript.manifest_hash,
            "request_id": "REQ-HTTP-QA-BLOCKED", "model": "stub-model",
            "policy_version": "UNKNOWN", "policy_hash": POLICY_HASH,
            "criteria_version": CRITERIA_VERSION, "criteria_hash": CRITERIA_HASH,
        })
        assert blocked_response.status_code == 200, blocked_response.text
        blocked = blocked_response.json()
        assert blocked["execution_status"] == "BLOCKED"
        assert blocked["quality_decision"] is None
        assert client.post(base + "/candidate-masters", headers=auth, json={
            "manuscript_id": manuscript.manuscript_id,
            "content_hash": manuscript.content_hash,
            "manifest_hash": manuscript.manifest_hash,
            "qa_id": blocked["qa_id"], "report_hash": blocked["report_hash"],
            "candidate_version": 1, "request_id": "REQ-NO-CANDIDATE-BLOCKED",
        }).status_code == 409
        assert client.get(base + "/source-masters/current", headers=auth).json() is None

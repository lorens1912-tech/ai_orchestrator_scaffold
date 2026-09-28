"""GAP-019 Phase 2 source, Bible, locale, lineage and schema contracts."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.p20_core.local_operator import OperatorIdentity
from app.p20_core.book_qa import BookQARequest, create_book_qa_report
from app.p20_core.candidate_master import CandidateRequest, create_candidate
from app.p20_core.project_repository import ProjectRepository, StorageResolver
from app.p20_core.source_promotion import (
    current_source_master, issue_source_review, record_source_decision,
)
from app.p20_core.translation import (
    create_translation_bible, current_translation_record, load_translation_record,
    record_translation_unit, seal_translation_version, start_translation,
    translation_staleness, recover_translation,
)
from app.p20_core.translation_contract import (
    QA_CRITERIA, TRANSLATION_POLICY_HASH, TRANSLATION_POLICY_VERSION,
    TranslationContractError, reduce_translation_qa, reduce_translation_hierarchy,
)
from tests.test_gap018_manuscript_version import case, make_ready_candidate


def _source(case, prepared=None):
    repo, root, manuscript, report, candidate, _ = prepared or make_ready_candidate(case)
    actor = OperatorIdentity("operator-synthetic", "credential-synthetic", 1)
    review = issue_source_review(repo, candidate.candidate_id, actor, ttl_seconds=300)
    result = record_source_decision(repo, candidate.candidate_id, {
        "request_id": "REQ-GAP019-SOURCE", "challenge_id": review["challenge_id"],
        "decision": "APPROVE", "candidate_hash": candidate.artifact_hash,
        "manuscript_hash": manuscript.content_hash,
        "manifest_hash": manuscript.manifest_hash,
        "qa_report_hash": report.report_hash,
        "expected_head": review["expected_head"],
    }, actor)
    assert result["status"] == "SOURCE_COMMITTED"
    return repo, root, actor, result


def _bible(repo, actor, source, locale):
    return create_translation_bible(repo, locale, {
        "request_id": "REQ-BIBLE-" + locale,
        "source_master_id": source["source_master_id"],
        "source_master_hash": source["artifact_hash"],
        "expected_head": [None, None, None],
        "decision_entries": [],
    }, actor)


def _run(repo, actor, source, bible, locale):
    return start_translation(repo, locale, {
        "request_id": "REQ-START-" + locale,
        "source_master_id": source["source_master_id"],
        "source_master_hash": source["artifact_hash"],
        "bible_id": bible["translation_bible_id"],
        "bible_hash": bible["record_hash"],
        "policy_version": TRANSLATION_POLICY_VERSION,
        "policy_hash": TRANSLATION_POLICY_HASH,
        "expected_head": [None, None, None],
    }, actor)


def test_source_required_and_exact_hash(case):
    repo, _root, actor, source = _source(case)
    wrong = {"request_id": "REQ-NO-SOURCE", "source_master_id": "CM-not-source",
             "source_master_hash": source["artifact_hash"],
             "expected_head": [None, None, None], "decision_entries": []}
    with pytest.raises(TranslationContractError, match="SOURCE_MASTER_REQUIRED"):
        create_translation_bible(repo, "en-US", wrong, actor)
    with pytest.raises(TranslationContractError, match="SOURCE_MASTER_MISMATCH"):
        create_translation_bible(repo, "en-US", {**wrong,
            "source_master_id": source["source_master_id"],
            "source_master_hash": "0" * 64}, actor)


def test_bible_locale_replay_and_translation_lineage_reopen(case):
    repo, root, actor, source = _source(case)
    us = _bible(repo, actor, source, "en-US")
    gb = _bible(repo, actor, source, "en-GB")
    assert us["record_hash"] != gb["record_hash"]
    assert current_translation_record(repo, "en-US", "BIBLE")["translation_bible_id"] == us["translation_bible_id"]
    assert current_translation_record(repo, "en-GB", "BIBLE")["translation_bible_id"] == gb["translation_bible_id"]
    assert create_translation_bible(repo, "en-US", {
        "request_id": "REQ-BIBLE-en-US", "source_master_id": source["source_master_id"],
        "source_master_hash": source["artifact_hash"],
        "expected_head": [None, None, None], "decision_entries": [],
    }, actor) == us
    with pytest.raises(TranslationContractError, match="REQUEST_ID_CONFLICT"):
        create_translation_bible(repo, "en-US", {
            "request_id": "REQ-BIBLE-en-US", "source_master_id": source["source_master_id"],
            "source_master_hash": source["artifact_hash"],
            "expected_head": [None, None, None],
            "decision_entries": [{"decision_id": "changed"}],
        }, actor)
    us_run = _run(repo, actor, source, us, "en-US")
    gb_run = _run(repo, actor, source, gb, "en-GB")
    assert us_run["run_id"] != gb_run["run_id"]
    assert us_run["source_binding"]["source_master_hash"] == source["artifact_hash"]
    source_text = "Neutral chapter 1."
    span_hash = hashlib.sha256(source_text.encode()).hexdigest()
    unit = record_translation_unit(repo, "en-US", {
        "request_id": "REQ-UNIT-US", "run_id": us_run["run_id"],
        "chapter_id": "chapter_1", "start_byte": 0,
        "end_byte": len(source_text.encode()), "source_span_hash": span_hash,
        "target_text": "Neutral translated chapter 1.", "origin": "USER_EDIT",
    }, identity=actor)
    version = seal_translation_version(repo, "en-US", {
        "request_id": "REQ-SEAL-US", "run_id": us_run["run_id"],
        "unit_ids": [unit["unit_id"]], "parent_version_id": None,
        "expected_head": [None, None, None],
    })
    assert version["unit_hashes"] == [unit["record_hash"]]
    assert translation_staleness(repo, version) == "CURRENT"
    assert current_translation_record(repo, "en-GB", "VERSION") is None
    reopened = ProjectRepository(StorageResolver(root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id))
    assert load_translation_record(reopened, "en-US", "VERSION", version["translation_version_id"]) == version
    assert reopened.get_gap019_head("en-US", "VERSION") == (
        version["translation_version_id"], 1, version["record_hash"])
    with reopened.connect(read_only=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_qa_reducer_keeps_blocking_findings():
    unit = "TU-neutral"
    coverage = tuple({"unit_id": unit, "criterion": criterion, "status": "PASS"}
                     for criterion in QA_CRITERIA)
    assert reduce_translation_qa((unit,), coverage, ()) == ("COMPLETED", "VALID", "ACCEPT")
    assert reduce_translation_qa((unit,), coverage[:-1], ()) == ("BLOCKED", "INVALID", None)
    issue = tuple({**item, "status": "ISSUE"} if item["criterion"] == "OMISSIONS" else item
                  for item in coverage)
    finding = {"unit_id": unit, "criterion": "OMISSIONS", "evidence": "Missing sentence",
               "severity": "MAJOR", "repairable": True, "blocking": True}
    assert reduce_translation_qa((unit,), issue, (finding,)) == ("COMPLETED", "VALID", "REVISE")
    assert reduce_translation_qa((unit,), issue, ({**finding, "severity": "CRITICAL",
                                              "repairable": False},)) == ("COMPLETED", "VALID", "REJECT")


def test_qa_hierarchy_preserves_chapter_failure_at_book_level():
    units = {"CH-1": ("U1",), "CH-2": ("U2",)}
    coverage = [{"unit_id": unit, "criterion": criterion, "status": "PASS"}
                for unit in ("U1", "U2") for criterion in QA_CRITERIA]
    coverage[-1]["status"] = "ISSUE"
    findings = [{"unit_id": "U2", "criterion": QA_CRITERIA[-1],
                 "evidence": "Synthetic critical mismatch", "severity": "CRITICAL",
                 "repairable": False, "blocking": True}]
    chapters, book = reduce_translation_hierarchy(units, tuple(coverage), tuple(findings))
    assert [item["quality_decision"] for item in chapters] == ["ACCEPT", "REJECT"]
    assert book["quality_decision"] == "REJECT"
    assert book["blocking_count"] == 1
    incomplete, blocked = reduce_translation_hierarchy(units, tuple(coverage[:-1]), tuple(findings))
    assert incomplete[1]["execution_status"] == "BLOCKED"
    assert blocked["execution_status"] == "BLOCKED"
    assert blocked["quality_decision"] is None


def test_controlled_project_schema_7_to_8_preserves_ledger(isolated_agentpro_storage):
    root = isolated_agentpro_storage
    repo = ProjectRepository(StorageResolver(root).resolve_project(
        "PROJ-GAP019-MIGRATION", book_id="BOOK-GAP019-MIGRATION"))
    repo.initialize()
    repo.set_metadata("gap019-preexisting", "preserve-me")
    with sqlite3.connect(repo.db_path) as connection:
        before = connection.execute("SELECT * FROM memory_events ORDER BY rowid").fetchall()
        for table in ("translation_records", "translation_heads", "translation_request_receipts"):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("UPDATE schema_version SET version=7 WHERE id=1")
        connection.execute("UPDATE project_identity SET schema_version=7 WHERE id=1")
    assert repo.inspect_schema().current_version == 7
    assert repo.migrate_schema().current_version == 8
    reopened = ProjectRepository(StorageResolver(root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id))
    assert reopened.get_metadata_readonly("gap019-preexisting") == "preserve-me"
    with reopened.connect(read_only=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert [tuple(row) for row in connection.execute(
            "SELECT * FROM memory_events ORDER BY rowid")] == before
        assert connection.execute("SELECT COUNT(*) FROM translation_records").fetchone()[0] == 0


def test_new_source_marks_old_translation_stale_without_mutating_history(case):
    prepared = make_ready_candidate(case)
    repo, _root, actor, source1 = _source(case, prepared)
    bible = _bible(repo, actor, source1, "en-US")
    run = _run(repo, actor, source1, bible, "en-US")
    _repo, _root, manuscript, report, candidate, _ = prepared
    qa2 = create_book_qa_report(repo, BookQARequest(
        repo.context.project_id, repo.context.book_id, manuscript.manuscript_id,
        manuscript.content_hash, manuscript.manifest_hash, "REQ-GAP019-QA2",
        report.coverage, report.findings, report.model_invocation_refs,
        reevaluation_of=report.qa_id,
    ))
    candidate2 = create_candidate(repo, CandidateRequest(
        repo.context.project_id, repo.context.book_id, manuscript.manuscript_id,
        manuscript.content_hash, manuscript.manifest_hash, qa2.qa_id,
        qa2.report_hash, 2, "REQ-GAP019-CANDIDATE2", candidate.candidate_id,
    ))
    review = issue_source_review(repo, candidate2.candidate_id, actor, ttl_seconds=300)
    source2 = record_source_decision(repo, candidate2.candidate_id, {
        "request_id": "REQ-GAP019-SOURCE2", "challenge_id": review["challenge_id"],
        "decision": "APPROVE", "candidate_hash": candidate2.artifact_hash,
        "manuscript_hash": manuscript.content_hash,
        "manifest_hash": manuscript.manifest_hash,
        "qa_report_hash": qa2.report_hash, "expected_head": review["expected_head"],
    }, actor)
    assert source2["status"] == "SOURCE_COMMITTED"
    source_head = current_source_master(repo)
    assert source_head is not None
    assert translation_staleness(repo, run) == "STALE_AGAINST_SOURCE"
    assert current_translation_record(repo, "en-US", "BIBLE")["effective_status"] == "STALE_AGAINST_SOURCE"
    with repo.connect(read_only=True) as connection:
        observations = [json.loads(row[0])["value"] for row in connection.execute(
            "SELECT payload_json FROM translation_records WHERE target_locale=? "
            "AND record_kind='STATUS'", ("en-US",))]
    assert any(item.get("translation_record_hash") == run["record_hash"]
               and item.get("effective_status") == "STALE_AGAINST_SOURCE"
               and item.get("source_head") == [source_head.source_master_id,
                                                source_head.version, source_head.artifact_hash]
               for item in observations)
    assert translation_staleness(repo, run) == "STALE_AGAINST_SOURCE"
    with repo.connect(read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM translation_records "
            "WHERE target_locale=? AND record_kind='STATUS'", ("en-US",)).fetchone()[0] == len(observations)
    assert load_translation_record(repo, "en-US", "RUN", run["run_id"]) == run
    with pytest.raises(TranslationContractError, match="SOURCE_MASTER_MISMATCH"):
        start_translation(repo, "en-US", {
            "request_id": "REQ-OLD-SOURCE", "source_master_id": source1["source_master_id"],
            "source_master_hash": source1["artifact_hash"],
            "bible_id": bible["translation_bible_id"], "bible_hash": bible["record_hash"],
            "policy_version": TRANSLATION_POLICY_VERSION,
            "policy_hash": TRANSLATION_POLICY_HASH,
            "expected_head": [None, None, None],
        }, actor)


def test_p20_translate_requires_project_context(isolated_agentpro_storage):
    from app.p20_core.executor import execute_p20

    with pytest.raises(TranslationContractError, match="TRANSLATION_PROJECT_CONTEXT_REQUIRED"):
        execute_p20(
            "RUN-GAP019-NO-CONTEXT", "BOOK-GAP019-NO-CONTEXT", ["TRANSLATE"],
            {"gap019_run_id": "TR-RUN-SYNTHETIC", "target_locale": "en-US"},
        )


def test_bible_change_stales_only_its_locale(case):
    repo, _root, actor, source = _source(case)
    us = _bible(repo, actor, source, "en-US")
    gb = _bible(repo, actor, source, "en-GB")
    us_run = _run(repo, actor, source, us, "en-US")
    gb_run = _run(repo, actor, source, gb, "en-GB")
    newer = create_translation_bible(repo, "en-US", {
        "request_id": "REQ-BIBLE-US-V2",
        "source_master_id": source["source_master_id"],
        "source_master_hash": source["artifact_hash"],
        "expected_head": [us["translation_bible_id"], us["version"], us["record_hash"]],
        "decision_entries": [],
    }, actor)
    assert newer["parent_bible_id"] == us["translation_bible_id"]
    assert translation_staleness(repo, us_run) == "STALE_AGAINST_BIBLE"
    assert translation_staleness(repo, gb_run) == "CURRENT"
    with repo.connect(read_only=True) as connection:
        us_observations = [json.loads(row[0])["value"] for row in connection.execute(
            "SELECT payload_json FROM translation_records WHERE target_locale=? "
            "AND record_kind='STATUS'", ("en-US",))]
        gb_count = connection.execute("SELECT COUNT(*) FROM translation_records "
            "WHERE target_locale=? AND record_kind='STATUS'", ("en-GB",)).fetchone()[0]
    assert any(item.get("translation_record_hash") == us_run["record_hash"]
               and item.get("effective_status") == "STALE_AGAINST_BIBLE"
               and item.get("bible_head") == [newer["translation_bible_id"],
                                               newer["version"], newer["record_hash"]]
               for item in us_observations)
    assert gb_count == 0
    assert load_translation_record(repo, "en-US", "BIBLE", us["translation_bible_id"]) == us


def test_f004_committed_bible_recovers_after_domain_gap(case, monkeypatch):
    from app.p20_core.cross_store_recovery import CrossStoreRecoveryService

    repo, root, actor, source = _source(case)
    request = {
        "request_id": "REQ-BIBLE-F004-CRASH",
        "source_master_id": source["source_master_id"],
        "source_master_hash": source["artifact_hash"],
        "expected_head": [None, None, None], "decision_entries": [],
    }
    original = CrossStoreRecoveryService.execute
    fired = False

    def crash_after_commit(self, plan):
        nonlocal fired
        result = original(self, plan)
        if not fired and plan.operation_type == "TRANSLATION_BIBLE_V1":
            fired = True
            raise RuntimeError("synthetic crash after F004 commit")
        return result

    monkeypatch.setattr(CrossStoreRecoveryService, "execute", crash_after_commit)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        create_translation_bible(repo, "en-US", request, actor)
    assert repo.get_gap019_request("en-US", "BIBLE_VERSION", request["request_id"])["status"] == "RECORDED"
    monkeypatch.setattr(CrossStoreRecoveryService, "execute", original)
    reopened = ProjectRepository(StorageResolver(root).resolve_project(
        repo.context.project_id, book_id=repo.context.book_id))
    recovered = recover_translation(reopened, "en-US", "BIBLE_VERSION", request["request_id"])
    assert current_translation_record(reopened, "en-US", "BIBLE")["record_hash"] == recovered["record_hash"]
    assert create_translation_bible(reopened, "en-US", request, actor) == recovered
    with reopened.connect(read_only=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM memory_events WHERE operation_id=?",
            ("gap019-bible-" + recovered["translation_bible_id"],)).fetchone()[0] > 0


def test_competing_bible_cas_has_one_current_and_durable_conflict(case, monkeypatch):
    import app.p20_core.translation as translation

    repo, _root, actor, source = _source(case)
    barrier = Barrier(2)
    original = translation._artifact_record

    def concurrent_artifact(repository, receipt):
        if receipt["operation_kind"] == "BIBLE_VERSION":
            barrier.wait(timeout=30)
        return original(repository, receipt)

    monkeypatch.setattr(translation, "_artifact_record", concurrent_artifact)

    def attempt(suffix):
        request_id = "REQ-BIBLE-RACE-" + suffix
        try:
            record = create_translation_bible(repo, "en-US", {
                "request_id": request_id,
                "source_master_id": source["source_master_id"],
                "source_master_hash": source["artifact_hash"],
                "expected_head": [None, None, None], "decision_entries": [],
            }, actor)
            return request_id, record, None
        except TranslationContractError as exc:
            return request_id, None, exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("A", "B")))
    winners = [(key, value) for key, value, error in results if error is None]
    losers = [(key, error) for key, value, error in results if error is not None]
    assert len(winners) == len(losers) == 1
    assert losers[0][1] == "HEAD_CONFLICT"
    assert repo.get_gap019_request("en-US", "BIBLE_VERSION", losers[0][0])["status"] == "HEAD_CONFLICT"
    assert current_translation_record(repo, "en-US", "BIBLE")["record_hash"] == winners[0][1]["record_hash"]
    assert current_translation_record(repo, "en-GB", "BIBLE") is None


def test_translation_record_cannot_cross_project(case):
    repo, root, actor, source = _source(case)
    bible = _bible(repo, actor, source, "en-US")
    other = ProjectRepository(StorageResolver(root).resolve_project(
        "PROJ-GAP019-OTHER", book_id="BOOK-GAP019-OTHER"))
    other.initialize()
    with pytest.raises(TranslationContractError, match="TRANSLATION_RECORD_NOT_FOUND"):
        load_translation_record(other, "en-US", "BIBLE", bible["translation_bible_id"])
    assert other.get_gap019_head("en-US", "BIBLE") == (None, None, None)

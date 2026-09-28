"""Read-only physical inspection of synthetic GAP-018 gate artifacts.

Run after the GAP-018 pytest cases. No fixture is created by this script.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.p20_core.book_qa import MANDATORY_CRITERIA, MANDATORY_LEVELS, load_book_qa_report
from app.p20_core.manuscript_version import load_manuscript
from app.p20_core.project_repository import ProjectRepository, StorageResolver
from app.p20_core.source_promotion import current_source_master, load_source_master


SESSIONS = Path(__file__).resolve().parents[1] / ".test_storage" / "sessions"
PROJECT = "PROJ-GAP018-NEUTRAL"
BOOK = "BOOK-GAP018-NEUTRAL"


def _cases(fragment: str) -> list[Path]:
    matches = [path for path in SESSIONS.glob("*/isolated/*")
               if fragment in path.name and (path / "projects").exists()]
    return sorted(matches, key=lambda path: path.stat().st_mtime, reverse=True)


def _case(fragment: str) -> Path:
    matches = _cases(fragment)
    if not matches:
        raise AssertionError("missing synthetic fixture: " + fragment)
    return matches[0]


def _repo(root: Path, project: str = PROJECT, book: str = BOOK) -> ProjectRepository:
    return ProjectRepository(StorageResolver(root).resolve_project(project, book_id=book))


def _connect(path: Path) -> sqlite3.Connection:
    value = str(path.resolve())
    return sqlite3.connect("\\\\?\\" + value if len(value) >= 240 else value)


def _count(repository: ProjectRepository, kind: str) -> int:
    with _connect(repository.db_path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM gap018_records WHERE record_kind=?", (kind,),
        ).fetchone()[0]


def inspect() -> dict[str, object]:
    positive_root = _case("test_operator_http_full_gap018_flow")
    positive = _repo(positive_root)
    source = current_source_master(positive)
    assert source is not None and source.approved_by_user
    assert _count(positive, "SOURCE") == 1
    assert _count(positive, "APPROVAL") == 1
    assert _count(positive, "CANDIDATE") == 1
    assert _count(positive, "QA_RUN_RESULT") == 1
    assert load_source_master(positive, source.source_master_id) == source
    artifact = (positive.context.project_root / source.artifact_ref).read_bytes()
    assert hashlib.sha256(artifact).hexdigest() == source.artifact_hash
    assert positive.get_gap018_source_head() == (
        source.source_master_id, source.version, source.artifact_hash,
    )
    assert positive.get_gap018_record(
        "PROMOTION", "gap018-source-" + source.approval_hash,
    )["execution_status"] == "SOURCE_COMMITTED"
    isolation_root = _case("test_author_challenge_approval_source")
    other = _repo(isolation_root, "PROJ-GAP018-OTHER", "BOOK-GAP018-OTHER")
    assert other.db_path.exists()
    assert current_source_master(other) is None
    assert _count(other, "SOURCE") == 0
    isolated_primary = _repo(isolation_root)
    isolated_source = current_source_master(isolated_primary)
    assert isolated_source is not None
    assert other.get_gap018_record("SOURCE", isolated_source.source_master_id) is None
    assert other.get_gap018_record("APPROVAL", isolated_source.approval_id) is None
    assert other.get_cross_store_operation_readonly(
        "gap018-source-" + isolated_source.approval_hash) is None

    negative = _repo(_case("test_operator_http_book_qa_revise"))
    assert current_source_master(negative) is None
    assert _count(negative, "SOURCE") == 0
    assert _count(negative, "CANDIDATE") == 0
    reports = [json.loads(path.read_text(encoding="utf-8"))
               for path in (negative.context.project_root / "book_qa" / "v1").glob("*.json")]
    assert {item["quality_decision"] for item in reports} == {"REVISE", "REJECT", None}

    for fragment in ("test_author_reject_never_creates_source",
                     "test_rejected_candidate",
                     "test_source_challenge_fences"):
        rejected = _repo(_case(fragment))
        assert current_source_master(rejected) is None
        assert _count(rejected, "SOURCE") == 0
        assert _count(rejected, "APPROVAL") >= 1
    rejected_candidate = _repo(_case("test_rejected_candidate"))
    assert _count(rejected_candidate, "CANDIDATE_STATE") == 1

    stale = _repo(_case("test_new_manuscript_head"))
    assert stale.get_gap018_manuscript_head()[1] == 2
    assert _count(stale, "CANDIDATE") == 1
    assert _count(stale, "SOURCE") == 0

    immutable = _repo(_case("test_snapshot_mismatch"))
    sealed_bundle = next((immutable.context.project_root / "manuscripts" / "v1").glob("*.json"))
    sealed = load_manuscript(immutable, json.loads(sealed_bundle.read_text(encoding="utf-8"))
                             ["record"]["manuscript_id"])
    assert hashlib.sha256((immutable.context.book_root / "book_bible.json").read_bytes()).hexdigest() != sealed.book_bible_hash
    assert hashlib.sha256((immutable.context.book_root / "artifacts" / "canon" / "snapshot.json").read_bytes()).hexdigest() != sealed.canon_snapshot_hash

    long = _repo(_case("test_200k_word_manuscript_uses_bounded"))
    manuscript_files = list((long.context.project_root / "manuscripts" / "v1").glob("*.json"))
    assert len(manuscript_files) == 1
    manuscript_bundle = json.loads(manuscript_files[0].read_text(encoding="utf-8"))
    manuscript = load_manuscript(long, manuscript_bundle["record"]["manuscript_id"])
    assert len(manuscript_bundle["content"].split()) >= 200000
    qa_files = list((long.context.project_root / "book_qa" / "v1").glob("*.json"))
    assert len(qa_files) == 1
    qa = load_book_qa_report(long, json.loads(qa_files[0].read_text(encoding="utf-8"))["qa_id"])
    assert qa.quality_decision == "ACCEPT"
    assert len(qa.coverage) == len(MANDATORY_LEVELS) * len(MANDATORY_CRITERIA)
    assert len(qa.model_invocation_refs) > 50
    long_source = current_source_master(long)
    assert long_source is not None and long_source.version == 1
    assert _count(long, "CANDIDATE") == 1
    assert _count(long, "APPROVAL") == 1
    assert _count(long, "SOURCE") == 1
    with _connect(long.db_path) as connection:
        for operation_id in qa.model_invocation_refs:
            invocation = json.loads(long.get_metadata_readonly("model_invocation.v1:" + operation_id))
            row = connection.execute(
                "SELECT payload_json FROM context_packages WHERE context_package_id=?",
                (invocation["context_package_id"],),
            ).fetchone()
            assert row is not None
            package = json.loads(row[0])
            assert package["total_tokens"] <= package["available_context_tokens"]
            assert all(item["content_hash"] != manuscript.content_hash
                       for item in package["included_items"])

    finding_case = _repo(_case("test_hierarchical_book_qa"))
    finding_files = list((finding_case.context.project_root / "book_qa" / "v1").glob("*.json"))
    assert len(finding_files) == 1
    finding_report = load_book_qa_report(
        finding_case, json.loads(finding_files[0].read_text(encoding="utf-8"))["qa_id"])
    assert finding_report.quality_decision == "REJECT" and len(finding_report.findings) == 1
    finding = finding_report.findings[0]
    producing = next(ref for ref in finding_report.model_invocation_refs
                     if finding.finding_id.startswith(hashlib.sha256(ref.encode()).hexdigest()[:12] + ":"))
    provenance = json.loads(finding_case.get_metadata_readonly("model_invocation.v1:" + producing))
    assert (finding.context_ref, finding.context_hash) == (
        provenance["context_package_id"], provenance["context_hash"])

    fault_roots = _cases("test_source_fault_boundaries_reopen")
    fault_sessions: dict[Path, list[Path]] = {}
    for root in fault_roots:
        fault_sessions.setdefault(root.parents[1], []).append(root)
    complete_fault_sets = [roots for roots in fault_sessions.values() if len(roots) == 4]
    assert complete_fault_sets
    for root in complete_fault_sets[0]:
        repository = _repo(root)
        assert _count(repository, "SOURCE") == 1
        assert current_source_master(repository) is not None
    lost_response = _repo(_case("test_http_response_loss_after_source"))
    assert _count(lost_response, "SOURCE") == 1
    assert current_source_master(lost_response) is not None
    concurrent = _repo(_case("test_parallel_approvals_use_cas"))
    assert _count(concurrent, "SOURCE") == 1
    assert current_source_master(concurrent) is not None
    conflict = _repo(_case("test_competing_approved_promotions"))
    assert _count(conflict, "PROMOTION_TERMINAL") == 2
    assert _count(conflict, "SOURCE") == 1
    with _connect(conflict.db_path) as connection:
        states = [json.loads(row[0])["value"]["execution_status"] for row in connection.execute(
            "SELECT payload_json FROM gap018_records WHERE record_kind='PROMOTION'",
        ).fetchall()]
    assert sorted(states) == ["SOURCE_COMMITTED", "SOURCE_CONFLICT"]
    intervention = _repo(_case("test_source_recovery_intervention"))
    assert _count(intervention, "APPROVAL") == 1
    assert _count(intervention, "SOURCE") == 0
    with _connect(intervention.db_path) as connection:
        state = connection.execute(
            "SELECT payload_json FROM gap018_records WHERE record_kind='PROMOTION'",
        ).fetchone()
    assert json.loads(state[0])["value"]["execution_status"] == "NEEDS_INTERVENTION"
    history = _repo(_case("test_separate_approvals_create_source_v2"))
    assert _count(history, "SOURCE") == 2
    assert current_source_master(history).version == 2

    migration_root = _case("test_controlled_p6_to_p7_preserves")
    migrated = _repo(migration_root, "PROJ-GAP018-MIGRATION", "BOOK-GAP018-MIGRATION")
    assert migrated.get_schema_version() == 8
    with _connect(migrated.db_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        ledger_count = connection.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]
        assert ledger_count > 0
    series = migration_root / "series" / "SERIES-GAP018-MIGRATION-UNRELATED" / "series.db"
    assert series.exists()
    with _connect(series) as connection:
        assert connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == 4

    return {
        "positive": {"source_id": source.source_master_id, "version": source.version,
                     "artifact_hash": source.artifact_hash},
        "negative": sorted(str(item["quality_decision"]) for item in reports),
        "long_manuscript": {"words": len(manuscript_bundle["content"].split()),
                            "model_invocations": len(qa.model_invocation_refs),
                            "coverage_pairs": len(qa.coverage),
                            "source_id": long_source.source_master_id},
        "fault_cases": 5, "concurrent_current_sources": _count(concurrent, "SOURCE"),
        "user_reject_and_challenge": "PASS", "stale_candidate": "PASS",
        "snapshot_immutability": "PASS", "finding_context_binding": "PASS",
        "source_v1_v2_history": "PASS", "intervention": "PASS",
        "migration": {"schema": 7, "ledger_events": ledger_count, "series_schema": 4},
        "isolation": "PASS", "reopen_integrity": "PASS",
    }


if __name__ == "__main__":
    print(json.dumps(inspect(), ensure_ascii=False, sort_keys=True))

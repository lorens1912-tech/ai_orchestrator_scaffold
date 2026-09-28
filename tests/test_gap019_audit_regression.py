"""Phase 5 regression proofs for exact translation and QA provenance."""
from __future__ import annotations

import hashlib
import json

import pytest

from app.p20_core.translation import (
    create_translation_bible, create_translation_qa_report, record_translation_unit,
    seal_translation_version, start_translation,
)
from app.p20_core.translation_contract import (
    QA_CRITERIA, TRANSLATION_POLICY_HASH, TRANSLATION_POLICY_VERSION,
    TranslationContractError,
)
from app.p20_core.translation_execution import (
    _local_quality_issues, _qa_wire_coverage_and_findings,
)
from tests.test_gap018_manuscript_version import case
from tests.test_gap019_phase2 import _bible, _run, _source


def test_run_unit_and_qa_finding_provenance(case):
    repo, _root, actor, source = _source(case)
    bible = _bible(repo, actor, source, "en-US")
    run_a = _run(repo, actor, source, bible, "en-US")
    run_b = start_translation(repo, "en-US", {
        "request_id": "AUDIT-RUN-B", "source_master_id": source["source_master_id"],
        "source_master_hash": source["artifact_hash"],
        "bible_id": bible["translation_bible_id"], "bible_hash": bible["record_hash"],
        "policy_version": TRANSLATION_POLICY_VERSION,
        "policy_hash": TRANSLATION_POLICY_HASH,
        "expected_head": [None, None, None],
    }, actor)
    source_text = "Neutral chapter 1."
    target_text = "Translated neutral chapter 1."
    unit_request = {
        "request_id": "AUDIT-UNIT-A", "run_id": run_a["run_id"],
        "chapter_id": "chapter_1", "start_byte": 0,
        "end_byte": len(source_text.encode()),
        "source_span_hash": hashlib.sha256(source_text.encode()).hexdigest(),
        "target_text": target_text, "origin": "USER_EDIT",
    }
    unit = record_translation_unit(repo, "en-US", unit_request, identity=actor)
    with pytest.raises(TranslationContractError, match="TRANSLATION_UNIT_RUN_MISMATCH"):
        seal_translation_version(repo, "en-US", {
            "request_id": "AUDIT-SEAL-B", "run_id": run_b["run_id"],
            "unit_ids": [unit["unit_id"]], "parent_version_id": None,
            "expected_head": [None, None, None],
        })

    operation_id = "AUDIT-WRONG-MODE"
    context_id = "AUDIT-CONTEXT"
    repo.set_metadata("model_invocation.v1:" + operation_id, json.dumps({
        "operation_id": operation_id, "validation": "VALID",
        "project_id": repo.context.project_id, "book_id": repo.context.book_id,
        "mode": "TRANSLATION_QA", "run_id": run_b["run_id"],
        "context_package_id": context_id, "context_hash": "AUDIT-HASH",
        "output_hash": hashlib.sha256(target_text.encode()).hexdigest(),
        "artifact_refs": [source["source_master_id"],
                          bible["translation_bible_id"], "locale:en-US"],
    }))
    with pytest.raises(TranslationContractError, match="TRANSLATION_MODEL_PROVENANCE_INVALID"):
        record_translation_unit(repo, "en-US", {
            **unit_request, "request_id": "AUDIT-WRONG-MODE-UNIT",
            "run_id": run_b["run_id"], "origin": "MODEL",
            "context_ref": context_id, "context_hash": "AUDIT-HASH",
            "model_invocation_refs": [operation_id],
        })

    version = seal_translation_version(repo, "en-US", {
        "request_id": "AUDIT-SEAL-A", "run_id": run_a["run_id"],
        "unit_ids": [unit["unit_id"]], "parent_version_id": None,
        "expected_head": [None, None, None],
    })
    qa_operation = "AUDIT-QA-INVOKE"
    repo.set_metadata("model_invocation.v1:" + qa_operation, json.dumps({
        "operation_id": qa_operation, "validation": "VALID",
        "project_id": repo.context.project_id, "book_id": repo.context.book_id,
        "mode": "TRANSLATION_QA", "context_package_id": context_id,
        "context_hash": "AUDIT-HASH",
        "artifact_refs": [source["source_master_id"],
                          bible["translation_bible_id"], "locale:en-US",
                          version["translation_version_id"], unit["unit_id"]],
    }))
    coverage = [{
        "unit_id": unit["unit_id"], "criterion": criterion,
        "status": "ISSUE" if criterion == "OMISSIONS" else "PASS",
        "source_hash": unit["source_span_hash"],
        "target_hash": unit["target_text_hash"],
        "context_ref": context_id, "context_hash": "AUDIT-HASH",
        "invocation_ref": qa_operation,
    } for criterion in QA_CRITERIA]
    forged_finding = {
        "unit_id": unit["unit_id"], "criterion": "OMISSIONS",
        "evidence": "Missing detail", "severity": "MAJOR",
        "repairable": True, "blocking": True,
        "source_hash": "0" * 64, "target_hash": "0" * 64,
        "context_ref": "FORGED", "context_hash": "FORGED",
        "invocation_ref": "FORGED",
    }
    report = create_translation_qa_report(repo, "en-US", {
        "request_id": "AUDIT-FORGED-FINDING",
        "translation_version_id": version["translation_version_id"],
        "translation_hash": version["record_hash"],
        "coverage": coverage, "findings": [forged_finding],
    })
    assert report.execution_status == "BLOCKED"
    assert report.validation_status == "INVALID"
    assert report.quality_decision is None

    artifact = repo.context.project_root / bible["artifact_ref"]
    artifact.write_bytes(b"corrupted")
    with pytest.raises(TranslationContractError, match="TRANSLATION_ARTIFACT_HASH_MISMATCH"):
        create_translation_bible(repo, "en-US", {
            "request_id": "REQ-BIBLE-en-US",
            "source_master_id": source["source_master_id"],
            "source_master_hash": source["artifact_hash"],
            "expected_head": [None, None, None],
            "decision_entries": [],
        }, actor)


def test_literal_bible_and_locale_checks_bind_to_scope():
    entries = [
        {"decision_id": "NAME", "kind": "PROPER_NAME", "scope": "BOOK",
         "source_form": "Marta", "target_form": "Marta"},
        {"decision_id": "TERM", "kind": "LOCKED_TERM", "scope": "BOOK",
         "source_form": "kompas", "target_form": "compass"},
        {"decision_id": "OTHER", "kind": "LOCKED_TERM", "scope": "CHAPTER:other",
         "source_form": "Marta", "target_form": "irrelevant"},
    ]
    issues = _local_quality_issues("Marta ma kompas.",
        "Mary has a map in the city center.", entries, "en-GB", "chapter_1")
    assert set(issues) == {"NAMES_ENTITIES", "TERMINOLOGY",
                           "BIBLE_COMPLIANCE", "LOCALE_CORRECTNESS"}
    assert "OTHER" not in issues["BIBLE_COMPLIANCE"]
    assert _local_quality_issues("Marta ma kompas.",
        "Marta has a compass in the city centre.", entries,
        "en-GB", "chapter_1") == {}


def test_qa_wire_assessments_keep_findings_bound_to_issues():
    coverage, findings = _qa_wire_coverage_and_findings({"assessments": [
        {"criterion": "OMISSIONS", "status": "ISSUE",
         "evidence": "source phrase is missing", "severity": "MAJOR",
         "repairable": True, "blocking": True},
        {"criterion": "ADDITIONS", "status": "PASS", "evidence": "",
         "severity": "NONE", "repairable": False, "blocking": False},
    ]})
    assert coverage == [{"criterion": "OMISSIONS", "status": "ISSUE"},
                        {"criterion": "ADDITIONS", "status": "PASS", "evidence": ""}]
    assert len(findings) == 1 and findings[0]["criterion"] == "OMISSIONS"
    with pytest.raises(ValueError, match="PASS has issue evidence"):
        _qa_wire_coverage_and_findings({"assessments": [{
            "criterion": "OMISSIONS", "status": "PASS",
            "evidence": "missing phrase", "severity": "MAJOR",
            "repairable": True, "blocking": True,
        }]})


def test_qa_wire_pass_rationale_is_not_a_finding():
    coverage, findings = _qa_wire_coverage_and_findings({"assessments": [{
        "criterion": "LOCALE_CORRECTNESS", "status": "PASS",
        "evidence": "No locale marker identified.", "severity": "NONE",
        "repairable": False, "blocking": False,
    }]})
    assert coverage[0]["evidence"] == "No locale marker identified."
    assert findings == []


def test_approved_name_spelling_overrides_generic_locale_rule():
    entries = [{"decision_id": "VENUE", "kind": "PROPER_NAME",
                "scope": "BOOK", "source_form": "Teatr", "target_form": "Theatre"}]
    assert _local_quality_issues("Teatr jest otwarty.",
        "Theatre is open.", entries, "en-US", "chapter_1") == {}
    case_error = _local_quality_issues("Marta jest tutaj.",
        "marta is here.", [{"decision_id": "NAME", "kind": "PROPER_NAME",
                            "scope": "BOOK", "source_form": "Marta",
                            "target_form": "Marta"}], "en-US", "chapter_1")
    assert "NAMES_ENTITIES" in case_error

from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from app.p20_core.book_qa import (
    BookQARequest, Coverage, Finding, CRITERIA_HASH, CRITERIA_VERSION,
    MANDATORY_CRITERIA, MANDATORY_LEVELS, POLICY_HASH, POLICY_VERSION,
    reduce_book_qa,
)
from app.p20_core.source_master import (
    AuthorApproval, SourceContractError, SourceHead, SourceMaster, _hash,
)


def request() -> BookQARequest:
    coverage = tuple(
        Coverage(level, criterion, "manuscripts/v1/synthetic.json", "a" * 64,
                 "CONTEXT-SYNTHETIC", "b" * 64, "PASS")
        for level in MANDATORY_LEVELS for criterion in MANDATORY_CRITERIA
    )
    return BookQARequest(
        "PROJECT-SYNTHETIC", "BOOK-SYNTHETIC", "MV-synthetic", "a" * 64,
        "c" * 64, "REQUEST-SYNTHETIC", coverage, (), ("invocation-synthetic",),
        policy_version=POLICY_VERSION, policy_hash=POLICY_HASH,
        criteria_version=CRITERIA_VERSION, criteria_hash=CRITERIA_HASH,
    )


def finding(*, classification: str | None = "repairable", must_fix: bool = True) -> Finding:
    return Finding("F-1", "BOOK", "CANON", "manuscripts/v1/synthetic.json", "a" * 64,
                   "chapter 1", "CRITICAL", "Synthetic evidence", classification,
                   must_fix, False, "CONTEXT-SYNTHETIC", "b" * 64)


def test_book_qa_reducer_has_separate_blocked_reject_revise_accept():
    valid = request()
    assert reduce_book_qa(valid) == ("COMPLETED", "VALID", "ACCEPT", ())
    assert reduce_book_qa(replace(valid, coverage=valid.coverage[:-1]))[0:3] == (
        "BLOCKED", "INVALID", None,
    )
    assert reduce_book_qa(replace(valid, model_invocation_refs=()))[0:3] == (
        "BLOCKED", "INVALID", None,
    )
    assert reduce_book_qa(replace(valid, coverage=(replace(valid.coverage[0], result="ISSUE"),
                                                      *valid.coverage[1:])))[0:3] == (
        "BLOCKED", "INVALID", None,
    )
    assert reduce_book_qa(replace(valid, findings=(finding(classification=None),)))[0:3] == (
        "BLOCKED", "INVALID", None,
    )
    assert reduce_book_qa(replace(valid, findings=(finding(),)))[0:3] == (
        "COMPLETED", "VALID", "REVISE",
    )
    assert reduce_book_qa(replace(valid, findings=(finding(classification="fundamental"),)))[0:3] == (
        "COMPLETED", "VALID", "REJECT",
    )


def test_source_contract_requires_exact_durable_approval_binding():
    absent = SourceHead(None, None, None)
    absent.validate()
    with pytest.raises(SourceContractError):
        SourceHead("SM-1", None, "a" * 64).validate()
    fields = dict(
        schema_version=1, approval_id="AP-1", project_id="PROJECT-SYNTHETIC",
        book_id="BOOK-SYNTHETIC", candidate_id="CM-1", candidate_hash="a" * 64,
        manuscript_hash="b" * 64, qa_report_hash="c" * 64, decision="APPROVE",
        operator_id="OP-1", credential_id="CRED-1", credential_version="1",
        challenge_id="CH-1", authorization_ref="AUTH-1", request_id="REQ-1",
        expected_head=absent, decided_at="2026-01-01T00:00:00Z", approval_hash="",
    )
    approval = AuthorApproval(**fields)
    approval = replace(approval, approval_hash=_hash(approval.unsigned_dict()))
    approval.validate()
    source_id = "SM-" + _hash({
        "approval_hash": approval.approval_hash,
        "candidate_hash": approval.candidate_hash,
        "expected_head": asdict(absent),
    })
    source = SourceMaster(
        1, source_id, approval.project_id, approval.book_id, 1, None,
        approval.candidate_id, approval.candidate_hash, "MV-1", 1,
        f"source_masters/v1/{source_id}.json", "d" * 64,
        approval.approval_id, approval.approval_hash, True,
        approval.decided_at, "SOURCE_COMMITTED", approval.decided_at,
    )
    source.validate(approval)
    with pytest.raises(SourceContractError):
        source.validate(replace(approval, decision="REJECT"))
    with pytest.raises(SourceContractError):
        replace(source, candidate_hash="e" * 64).validate(approval)

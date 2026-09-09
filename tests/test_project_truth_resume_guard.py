from pathlib import Path

import pytest

from app.p20_core.project_truth import (
    build_project_truth_binding,
    assert_resume_project_truth_consistency,
    ProjectTruthMismatch,
)


def test_assert_resume_project_truth_consistency_accepts_matching_run_state():
    binding = build_project_truth_binding()
    persisted_run_state = {
        "project_truth": dict(binding),
        "status": "ACCEPT",
    }

    assert_resume_project_truth_consistency(binding, persisted_run_state)


def test_assert_resume_project_truth_consistency_rejects_mismatched_sha():
    binding = build_project_truth_binding()
    bad_binding = dict(binding)
    bad_binding["sha256"] = "E" * 64

    persisted_run_state = {
        "project_truth": bad_binding,
        "status": "ACCEPT",
    }

    with pytest.raises(ProjectTruthMismatch) as exc:
        assert_resume_project_truth_consistency(binding, persisted_run_state)

    assert "resume run_state sha256 mismatch" in str(exc.value)


def test_runtime_contains_resume_project_truth_guard():
    runtime_text = Path("app/p20_core/runtime.py").read_text(encoding="utf-8")

    assert "assert_resume_project_truth_consistency" in runtime_text
    assert "_resume_run_state = locals().get(\"run_state\")" in runtime_text
    assert "assert_resume_project_truth_consistency(project_truth_binding, _resume_run_state)" in runtime_text

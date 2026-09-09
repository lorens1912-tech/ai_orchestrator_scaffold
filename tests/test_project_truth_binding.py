from app.p20_core.project_truth import (
    PROJECT_TRUTH_CONTRACT,
    build_project_truth_binding,
    attach_project_truth,
    assert_project_truth_consistency,
    ProjectTruthMismatch,
)


def test_build_project_truth_binding_returns_master_canon_contract():
    binding = build_project_truth_binding()
    assert binding["contract"] == PROJECT_TRUTH_CONTRACT
    assert binding["path"].endswith("MASTER_CANON_AGENTPRO.md")
    assert len(binding["sha256"]) == 64
    assert binding["source"] == "repo_file"


def test_assert_project_truth_consistency_accepts_identical_bindings():
    binding = build_project_truth_binding()

    response_doc = attach_project_truth({}, binding)
    audit_doc = attach_project_truth({}, binding)
    chapter_doc = attach_project_truth({}, binding)
    canon_snapshot_doc = attach_project_truth({}, binding)
    run_state_doc = attach_project_truth({}, binding)

    assert_project_truth_consistency(
        binding,
        response_doc=response_doc,
        audit_doc=audit_doc,
        chapter_doc=chapter_doc,
        canon_snapshot_doc=canon_snapshot_doc,
        run_state_doc=run_state_doc,
    )


def test_assert_project_truth_consistency_rejects_sha_mismatch():
    binding = build_project_truth_binding()

    bad = dict(binding)
    bad["sha256"] = "F" * 64

    response_doc = attach_project_truth({}, binding)
    audit_doc = attach_project_truth({}, bad)

    try:
        assert_project_truth_consistency(
            binding,
            response_doc=response_doc,
            audit_doc=audit_doc,
        )
        assert False, "Expected ProjectTruthMismatch"
    except ProjectTruthMismatch as e:
        assert "sha256 mismatch" in str(e)

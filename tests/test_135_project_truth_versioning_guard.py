from __future__ import annotations

from app.p20_core.project_truth import (
    PROJECT_TRUTH_CONTRACT_FIELDS,
    PROJECT_TRUTH_CONTRACT_GUARD_FROZEN_REQUIRED_FINGERPRINT,
    PROJECT_TRUTH_CONTRACT_GUARD_FROZEN_VERSION,
    PROJECT_TRUTH_CONTRACT_VERSION,
    PROJECT_TRUTH_VERSIONING_POLICY,
    build_project_truth_contract_field_payload,
    compute_project_truth_contract_field_fingerprint,
    extract_project_truth,
)


def test_project_truth_versioning_policy_is_declared():
    assert PROJECT_TRUTH_VERSIONING_POLICY
    joined = " ".join(PROJECT_TRUTH_VERSIONING_POLICY).lower()
    assert "bump" in joined
    assert "fingerprint" in joined
    assert "contract_version" in joined


def test_project_truth_contract_field_fingerprint_guard_is_current():
    assert PROJECT_TRUTH_CONTRACT_VERSION == PROJECT_TRUTH_CONTRACT_GUARD_FROZEN_VERSION
    assert build_project_truth_contract_field_payload() == {
        "contract_fields": list(PROJECT_TRUTH_CONTRACT_FIELDS),
    }
    assert compute_project_truth_contract_field_fingerprint() == PROJECT_TRUTH_CONTRACT_GUARD_FROZEN_REQUIRED_FINGERPRINT


def test_extract_project_truth_adds_contract_version_to_binding():
    binding = extract_project_truth({
        "project_truth": {
            "contract": "PROJECT_TRUTH/v1",
            "path": "books/x/artifacts/project_truth.json",
            "sha256": "abc123",
        }
    })

    assert binding["contract"] == "PROJECT_TRUTH/v1"
    assert binding["path"] == "books/x/artifacts/project_truth.json"
    assert binding["sha256"] == "abc123"
    assert binding["contract_version"] == PROJECT_TRUTH_CONTRACT_VERSION

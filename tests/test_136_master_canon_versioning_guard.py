from __future__ import annotations

from app.p20_core.runtime import (
    MASTER_CANON_CONTRACT_FIELDS,
    MASTER_CANON_CONTRACT_GUARD_FROZEN_REQUIRED_FINGERPRINT,
    MASTER_CANON_CONTRACT_GUARD_FROZEN_VERSION,
    MASTER_CANON_CONTRACT_VERSION,
    MASTER_CANON_VERSIONING_POLICY,
    build_master_canon_contract_field_payload,
    compute_master_canon_contract_field_fingerprint,
    resolve_master_canon,
)


def test_master_canon_versioning_policy_is_declared():
    assert MASTER_CANON_VERSIONING_POLICY
    joined = " ".join(MASTER_CANON_VERSIONING_POLICY).lower()
    assert "bump" in joined
    assert "fingerprint" in joined
    assert "contract_version" in joined


def test_master_canon_contract_field_fingerprint_guard_is_current():
    assert MASTER_CANON_CONTRACT_VERSION == MASTER_CANON_CONTRACT_GUARD_FROZEN_VERSION
    assert build_master_canon_contract_field_payload() == {
        "contract_fields": list(MASTER_CANON_CONTRACT_FIELDS),
    }
    assert compute_master_canon_contract_field_fingerprint() == MASTER_CANON_CONTRACT_GUARD_FROZEN_REQUIRED_FINGERPRINT


def test_resolve_master_canon_adds_contract_version_to_binding():
    binding = resolve_master_canon()

    assert binding["contract"] == "MASTER_CANON_AGENTPRO/v1"
    assert binding["path"].endswith("MASTER_CANON_AGENTPRO.md")
    assert binding["sha256"]
    assert binding["contract_version"] == MASTER_CANON_CONTRACT_VERSION

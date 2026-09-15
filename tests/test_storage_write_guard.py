from __future__ import annotations

import pytest

from tests import conftest


def test_real_storage_guard_includes_top_level_audit_directory(tmp_path):
    audit_root = (conftest.REPO_ROOT / "audit").resolve()

    assert audit_root in conftest.REAL_STORAGE_ROOTS
    with pytest.raises(AssertionError, match="real AgentPRO storage path"):
        conftest._assert_not_real_storage(audit_root / "neutral-probe.json", "synthetic write")

    conftest._assert_not_real_storage(tmp_path / "audit" / "neutral-probe.json", "synthetic write")

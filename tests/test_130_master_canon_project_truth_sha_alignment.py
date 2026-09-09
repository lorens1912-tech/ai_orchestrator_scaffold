from __future__ import annotations

from app.p20_core.master_canon import resolve_master_canon
from app.p20_core.project_truth import build_project_truth_binding


def test_master_canon_sha_matches_project_truth_sha_for_same_file():
    master_canon = resolve_master_canon()
    project_truth = build_project_truth_binding()

    assert master_canon["path"].replace("\\", "/").endswith("MASTER_CANON_AGENTPRO.md")
    assert project_truth["path"].replace("\\", "/").endswith("MASTER_CANON_AGENTPRO.md")
    assert master_canon["sha256"] == project_truth["sha256"]

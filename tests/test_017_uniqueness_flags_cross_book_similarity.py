import json
from pathlib import Path
from uuid import uuid4

from app.orchestrator_stub import execute_stub
from app.p20_core.storage_paths import get_runs_root, get_storage_root


REPO_ROOT = Path(__file__).resolve().parents[1]


def _artifact_path(public_path: str) -> Path:
    path = Path(public_path)
    assert not path.is_absolute(), public_path
    return get_storage_root() / path


def test_uniqueness_flags_second_book(monkeypatch, isolated_agentpro_storage):
    monkeypatch.setenv("AGENT_TEST_MODE", "1")
    token = uuid4().hex[:8]
    run_a = f"test_run_017_a_{token}"
    run_b = f"test_run_017_b_{token}"
    book_a = f"bookA_{token}"
    book_b = f"bookB_{token}"
    registry_path = get_runs_root() / "_tmp" / f"test_uniqueness_017_{token}.jsonl"
    repo_registry_path = REPO_ROOT / "runs" / "_tmp" / registry_path.name
    monkeypatch.setenv("UNIQUENESS_REGISTRY_PATH", str(registry_path))

    for path in (
        REPO_ROOT / "runs" / run_a,
        REPO_ROOT / "runs" / run_b,
        REPO_ROOT / "books" / book_a,
        REPO_ROOT / "books" / book_b,
        repo_registry_path,
    ):
        assert not path.exists(), path

    a_paths = execute_stub(
        run_id=run_a,
        book_id=book_a,
        modes=["WRITE", "UNIQUENESS"],
        payload={"input": "x"},
    )
    p_a = _artifact_path(a_paths[-1])
    step_a = json.loads(p_a.read_text(encoding="utf-8"))
    pl_a = ((step_a.get("result") or {}).get("payload") or {})
    assert pl_a.get("UNIQ_DECISION") in {"ACCEPT", "REVISE"}, pl_a

    b_paths = execute_stub(
        run_id=run_b,
        book_id=book_b,
        modes=["WRITE", "UNIQUENESS"],
        payload={"input": "x"},
    )
    p_b = _artifact_path(b_paths[-1])
    step_b = json.loads(p_b.read_text(encoding="utf-8"))
    pl_b = ((step_b.get("result") or {}).get("payload") or {})

    assert pl_b.get("UNIQ_DECISION") == "REVISE", pl_b
    assert (pl_b.get("UNIQ_SCORE") or 0) >= 0.90, pl_b
    assert pl_b.get("UNIQ_MATCH") is not None, pl_b
    assert registry_path.exists()

    for path in (
        REPO_ROOT / "runs" / run_a,
        REPO_ROOT / "runs" / run_b,
        REPO_ROOT / "books" / book_a,
        REPO_ROOT / "books" / book_b,
        repo_registry_path,
    ):
        assert not path.exists(), path

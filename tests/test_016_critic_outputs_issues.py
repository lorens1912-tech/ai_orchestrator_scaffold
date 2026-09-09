import json
from pathlib import Path
from uuid import uuid4

from app.orchestrator_stub import execute_stub
from app.p20_core.storage_paths import get_storage_root


REPO_ROOT = Path(__file__).resolve().parents[1]


def _artifact_path(public_path: str) -> Path:
    path = Path(public_path)
    assert not path.is_absolute(), public_path
    return get_storage_root() / path


def test_critic_returns_issues(monkeypatch, isolated_agentpro_storage):
    monkeypatch.setenv("AGENT_TEST_MODE", "1")
    run_id = f"test_run_016_{uuid4().hex[:8]}"
    book_id = f"test_book_016_{uuid4().hex[:8]}"
    assert not (REPO_ROOT / "runs" / run_id).exists()
    assert not (REPO_ROOT / "books" / book_id).exists()

    artifact_paths = execute_stub(
        run_id=run_id,
        book_id=book_id,
        modes=["WRITE", "CRITIC"],
        payload={"input": "Napisz akapit o samotności po rozwodzie."},
    )

    p = _artifact_path(artifact_paths[-1])
    assert p.exists(), f"CRITIC artifact missing: {p}"

    step = json.loads(p.read_text(encoding="utf-8"))
    assert step.get("mode") == "CRITIC", step

    pl = ((step.get("result") or {}).get("payload") or {})
    issues = pl.get("ISSUES") or []

    assert isinstance(issues, list), f"ISSUES not list: {pl}"
    assert len(issues) >= 3, f"Too few issues: {issues}"
    assert not (REPO_ROOT / "runs" / run_id).exists()
    assert not (REPO_ROOT / "books" / book_id).exists()

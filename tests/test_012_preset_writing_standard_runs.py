import json
from pathlib import Path
from uuid import uuid4

from app.orchestrator_stub import resolve_modes, execute_stub
from app.p20_core.storage_paths import get_storage_root


REPO_ROOT = Path(__file__).resolve().parents[1]


def _artifact_path(public_path: str) -> Path:
    path = Path(public_path)
    assert not path.is_absolute(), public_path
    return get_storage_root() / path


def test_preset_runs_and_creates_4_steps(monkeypatch, isolated_agentpro_storage):
    monkeypatch.setenv("AGENT_TEST_MODE", "1")
    run_id = f"test_run_012_{uuid4().hex[:8]}"
    book_id = f"test_book_012_{uuid4().hex[:8]}"
    assert not (REPO_ROOT / "runs" / run_id).exists()
    assert not (REPO_ROOT / "books" / book_id).exists()

    modes, _, _ = resolve_modes(None, "WRITING_STANDARD")
    assert modes == ["PLAN", "WRITE", "UNIQUENESS", "QUALITY"], modes

    paths = execute_stub(
        run_id=run_id,
        book_id=book_id,
        modes=modes,
        payload={"input": "x"},
    )

    assert len(paths) == 4, paths

    p_last = _artifact_path(paths[-1])
    data = json.loads(p_last.read_text(encoding="utf-8"))
    assert data.get("mode") == "QUALITY", data
    assert not (REPO_ROOT / "runs" / run_id).exists()
    assert not (REPO_ROOT / "books" / book_id).exists()

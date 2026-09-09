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


def test_quality_is_not_reject_on_short_input(monkeypatch, isolated_agentpro_storage):
    monkeypatch.setenv("AGENT_TEST_MODE", "1")
    run_id = f"test_run_014_{uuid4().hex[:8]}"
    book_id = f"test_book_014_{uuid4().hex[:8]}"
    assert not (REPO_ROOT / "runs" / run_id).exists()
    assert not (REPO_ROOT / "books" / book_id).exists()

    artifact_paths = execute_stub(
        run_id=run_id,
        book_id=book_id,
        modes=["WRITE", "QUALITY"],
        payload={"input": "x"},
    )

    p_q = _artifact_path(artifact_paths[-1])
    assert p_q.exists(), f"Missing QUALITY artifact: {p_q}"

    data = json.loads(p_q.read_text(encoding="utf-8"))
    assert data.get("mode") == "QUALITY", data

    pl = ((data.get("result") or {}).get("payload") or {})
    decision = pl.get("DECISION")
    assert decision in {"ACCEPT", "REVISE", "REJECT"}, pl

    assert decision != "REJECT", (
        f"QUALITY wygląda jakby oceniało input zamiast output WRITE. payload={pl}"
    )
    assert not (REPO_ROOT / "runs" / run_id).exists()
    assert not (REPO_ROOT / "books" / book_id).exists()

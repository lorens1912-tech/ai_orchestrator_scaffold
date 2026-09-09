import json
from pathlib import Path
from uuid import uuid4

from app.p20_core.storage_paths import get_storage_root


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_write_model_force_sets_requested_model_in_artifact(
    monkeypatch,
    isolated_agentpro_storage,
):
    monkeypatch.setenv("WRITE_MODEL_FORCE", "gpt-5.1")
    monkeypatch.setenv("AGENT_TEST_MODE", "0")
    from app.orchestrator_stub import execute_stub

    token = uuid4().hex[:8]
    run_id = f"run_test_model_force_telemetry_{token}"
    book_id = f"model_force_book_{token}"
    assert not (REPO_ROOT / "runs" / run_id).exists()
    assert not (REPO_ROOT / "books" / book_id).exists()

    artifacts = execute_stub(
        run_id=run_id,
        book_id=book_id,
        modes=["WRITE"],
        payload={"input": "x"},
        steps=None,
    )
    assert artifacts, "no artifacts returned"

    p = get_storage_root() / Path(artifacts[0])
    doc = json.loads(p.read_text(encoding="utf-8"))

    assert doc["input"]["_requested_model"] == "gpt-5.1"
    meta = doc["result"]["payload"]["meta"]
    assert meta["requested_model"] == "gpt-5.1"
    assert not (REPO_ROOT / "runs" / run_id).exists()
    assert not (REPO_ROOT / "books" / book_id).exists()

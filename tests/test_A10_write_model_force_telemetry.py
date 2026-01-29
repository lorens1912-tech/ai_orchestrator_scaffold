import os
import json
from pathlib import Path

def test_write_model_force_sets_requested_model_in_artifact():
    os.environ["WRITE_MODEL_FORCE"] = "gpt-5.1"
    os.environ["AGENT_TEST_MODE"] = "0"

    from app.orchestrator_stub import execute_stub

    run_id = "run_test_model_force_telemetry"
    artifacts = execute_stub(run_id=run_id, book_id="default", modes=["WRITE"], payload={"input": "x"}, steps=None)
    assert artifacts, "no artifacts returned"

    p = Path(artifacts[0])
    doc = json.loads(p.read_text(encoding="utf-8"))

    assert doc["input"]["_requested_model"] == "gpt-5.1"
    meta = doc["result"]["payload"]["meta"]
    assert meta["requested_model"] == "gpt-5.1"

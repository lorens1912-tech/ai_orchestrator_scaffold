import json
import uuid
from pathlib import Path

from app.orchestrator_stub import execute_stub, resolve_modes

def _read_artifact(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def test_preset_quality_thresholds_are_injected_and_affect_decision():
    # text length ~250, no paragraphs -> SCORE ~0.558 (between 0.55 and 0.60)
    t = "a" * 250

    # control: no preset context => default revise_min=0.55 => REVISE
    run1 = "run_test_c4_no_preset_" + uuid.uuid4().hex[:8]
    arts1 = execute_stub(run_id=run1, book_id="default", modes=["QUALITY"], payload={"text": t, "input": "x"}, steps=None)
    doc1 = _read_artifact(arts1[0])
    assert doc1["result"]["payload"]["DECISION"] == "REVISE"

    # preset path: resolve_modes(None, preset_id) must carry _preset_id
    seq, preset_id, payload = resolve_modes(None, "WRITING_STANDARD")
    assert payload.get("_preset_id") == "WRITING_STANDARD"

    # with preset context => WRITING_STANDARD revise_min=0.60 => REJECT for same SCORE
    run2 = "run_test_c4_with_preset_" + uuid.uuid4().hex[:8]
    payload2 = dict(payload)
    payload2["text"] = t
    payload2["input"] = "x"

    arts2 = execute_stub(run_id=run2, book_id="default", modes=["QUALITY"], payload=payload2, steps=None)
    doc2 = _read_artifact(arts2[0])

    # prove context injection
    ctx = doc2["input"].get("context") or {}
    preset = ctx.get("preset") or {}
    th = preset.get("quality_thresholds") or {}
    assert th.get("revise_min") == 0.60

    assert doc2["result"]["payload"]["DECISION"] == "REJECT"

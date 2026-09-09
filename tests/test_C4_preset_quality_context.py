import json
import uuid
from pathlib import Path

from app.orchestrator_stub import execute_stub, resolve_modes
from app.p20_core.storage_paths import get_storage_root


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_artifact(path: str) -> dict:
    public_path = Path(path)
    assert not public_path.is_absolute(), path
    return json.loads((get_storage_root() / public_path).read_text(encoding="utf-8"))


def test_preset_quality_thresholds_are_injected_and_affect_decision(
    isolated_agentpro_storage,
):
    # text length ~250, no paragraphs -> SCORE ~0.558 (between 0.55 and 0.60)
    t = "a" * 250

    # control: no preset context => default revise_min=0.55 => REVISE
    run1 = "run_test_c4_no_preset_" + uuid.uuid4().hex[:8]
    book1 = "book_test_c4_no_preset_" + uuid.uuid4().hex[:8]
    assert not (REPO_ROOT / "runs" / run1).exists()
    assert not (REPO_ROOT / "books" / book1).exists()
    arts1 = execute_stub(
        run_id=run1,
        book_id=book1,
        modes=["QUALITY"],
        payload={"text": t, "input": "x"},
        steps=None,
    )
    doc1 = _read_artifact(arts1[0])
    assert doc1["result"]["payload"]["DECISION"] == "REVISE"

    # preset path: resolve_modes(None, preset_id) must carry _preset_id
    seq, preset_id, payload = resolve_modes(None, "WRITING_STANDARD")
    assert payload.get("_preset_id") == "WRITING_STANDARD"

    # with preset context => WRITING_STANDARD revise_min=0.60 => REJECT for same SCORE
    run2 = "run_test_c4_with_preset_" + uuid.uuid4().hex[:8]
    book2 = "book_test_c4_with_preset_" + uuid.uuid4().hex[:8]
    assert not (REPO_ROOT / "runs" / run2).exists()
    assert not (REPO_ROOT / "books" / book2).exists()
    payload2 = dict(payload)
    payload2["text"] = t
    payload2["input"] = "x"

    arts2 = execute_stub(
        run_id=run2,
        book_id=book2,
        modes=["QUALITY"],
        payload=payload2,
        steps=None,
    )
    doc2 = _read_artifact(arts2[0])

    # prove context injection
    ctx = doc2["input"].get("context") or {}
    preset = ctx.get("preset") or {}
    th = preset.get("quality_thresholds") or {}
    assert th.get("revise_min") == 0.60

    assert doc2["result"]["payload"]["DECISION"] == "REJECT"
    assert not (REPO_ROOT / "runs" / run1).exists()
    assert not (REPO_ROOT / "runs" / run2).exists()
    assert not (REPO_ROOT / "books" / book1).exists()
    assert not (REPO_ROOT / "books" / book2).exists()

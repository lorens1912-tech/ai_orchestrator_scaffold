import json
from pathlib import Path

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def _run_pipeline(topic: str, min_words: int):
    body = {
        "preset": "ORCH_STANDARD",
        "payload": {
            "topic": topic,
            "min_words": min_words
        }
    }
    r = client.post("/agent/step", json=body)
    assert r.status_code == 200, r.text

    data = r.json()
    run_id = data["run_id"]

    steps_dir = Path("runs") / run_id / "steps"
    assert steps_dir.exists(), f"Brak steps_dir dla run_id={run_id}"

    q_files = sorted(steps_dir.glob("*_QUALITY.json"))
    assert q_files, f"Brak *_QUALITY.json dla run_id={run_id}"
    qf = q_files[-1]

    raw = qf.read_text(encoding="utf-8")
    j = json.loads(raw)
    payload = j["result"]["payload"]
    return run_id, payload, raw

def _get(payload: dict, key: str, default=None):
    if key in payload:
        return payload[key]
    lk = key.lower()
    if lk in payload:
        return payload[lk]
    return default

def test_p17_orch_standard_short_fail_and_block():
    run_id, p, raw = _run_pipeline("krotki test p17", 120)

    dec = str(_get(p, "DECISION", "")).upper()
    block = _get(p, "BLOCK_PIPELINE", None)
    stats = _get(p, "STATS", {}) or {}
    words = int(stats.get("words", 0))
    reasons = _get(p, "REASONS", []) or []
    if not isinstance(reasons, list):
        reasons = [reasons]
    reasons_txt = " | ".join(str(x) for x in reasons).upper()

    assert dec == "FAIL", f"run={run_id}, DECISION={dec}"
    assert block is True, f"run={run_id}, BLOCK_PIPELINE={block}"
    assert words < 120, f"run={run_id}, WORDS={words}"
    assert "MIN_WORDS" in reasons_txt, f"run={run_id}, REASONS={reasons}"

    # Kontrakt JSON: brak lower-case duplikatu
    assert '"block_pipeline"' not in raw, f"run={run_id} ma niedozwolony key block_pipeline"

def test_p17_orch_standard_long_accept_no_block():
    long_topic = " ".join([f"slowo{i}" for i in range(1, 161)])  # 160 słów
    run_id, p, raw = _run_pipeline(long_topic, 120)

    dec = str(_get(p, "DECISION", "")).upper()
    block = _get(p, "BLOCK_PIPELINE", None)
    stats = _get(p, "STATS", {}) or {}
    words = int(stats.get("words", 0))
    reasons = _get(p, "REASONS", []) or []
    if not isinstance(reasons, list):
        reasons = [reasons]
    reasons_txt = " | ".join(str(x) for x in reasons).upper()

    assert dec == "ACCEPT", f"run={run_id}, DECISION={dec}"
    assert block is False, f"run={run_id}, BLOCK_PIPELINE={block}"
    assert words >= 120, f"run={run_id}, WORDS={words}"
    assert "MIN_WORDS" not in reasons_txt, f"run={run_id}, REASONS={reasons}"

    # Kontrakt JSON: brak lower-case duplikatu
    assert '"block_pipeline"' not in raw, f"run={run_id} ma niedozwolony key block_pipeline"

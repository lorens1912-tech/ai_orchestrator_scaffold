import json
import re
from pathlib import Path

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def _run_quality(text: str, min_words: int):
    body = {
        "mode": "QUALITY",
        "payload": {
            "text": text,
            "min_words": min_words
        }
    }
    r = client.post("/agent/step", json=body, timeout=120)
    assert r.status_code == 200, r.text
    data = r.json()
    run_id = data["run_id"]

    steps_dir = Path("runs") / run_id / "steps"
    q_files = sorted(steps_dir.glob("*_QUALITY.json"))
    assert q_files, f"Brak *_QUALITY.json dla run_id={run_id}"

    qf = q_files[-1]
    raw = qf.read_text(encoding="utf-8")
    j = json.loads(raw)
    payload = j["result"]["payload"]
    return run_id, payload, raw

def _get(payload, key, default=None):
    if key in payload:
        return payload[key]
    lk = key.lower()
    if lk in payload:
        return payload[lk]
    return default

def test_p16_quality_short_fail_and_block():
    run_id, p, raw = _run_quality("krotki test", 120)

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

    # JSON kontrakt: nie dopuszczamy zdublowanego lower-case key
    assert re.search(r'"block_pipeline"\s*:', raw) is None, f"run={run_id} ma niedozwolony key block_pipeline"

def test_p16_quality_long_accept_and_no_block():
    long_text = " ".join([f"slowo{i}" for i in range(1, 141)])
    run_id, p, raw = _run_quality(long_text, 120)

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

    # JSON kontrakt: nie dopuszczamy zdublowanego lower-case key
    assert re.search(r'"block_pipeline"\s*:', raw) is None, f"run={run_id} ma niedozwolony key block_pipeline"

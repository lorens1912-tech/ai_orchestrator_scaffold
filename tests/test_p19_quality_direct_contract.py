import json
import time
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def _get_ci(d, key, default=None):
    if not isinstance(d, dict):
        return default
    for k in (key, key.lower(), key.upper()):
        if k in d:
            return d[k]
    return default

def _read_quality_payload(run_id: str, timeout_sec: float = 6.0):
    steps_dir = Path("runs") / run_id / "steps"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        q_files = sorted(steps_dir.glob("*_QUALITY.json"))
        if q_files:
            raw = q_files[-1].read_text(encoding="utf-8")
            j = json.loads(raw)
            return (j["result"]["payload"], raw)
        time.sleep(0.05)
    raise AssertionError(f"run={run_id}, brak *_QUALITY.json")

def _run_quality_direct(text: str, min_words: int):
    body = {
        "mode": "QUALITY",
        "payload": {
            "text": text,
            "min_words": min_words
        }
    }
    time.sleep(1.05)  # anty-kolizja run_id (sekundy)
    r = client.post("/agent/step", json=body)
    assert r.status_code == 200, f"status={r.status_code}, body={r.text}"
    run_id = r.json()["run_id"]
    payload, raw = _read_quality_payload(run_id)
    return run_id, payload, raw

def test_p19_quality_direct_empty_fails_and_blocks():
    run_id, p, raw = _run_quality_direct("", 120)

    dec = str(_get_ci(p, "DECISION", "")).upper()
    block = _get_ci(p, "BLOCK_PIPELINE", None)
    stats = _get_ci(p, "STATS", {}) or {}
    words = int((stats or {}).get("words", 0))
    reasons = _get_ci(p, "REASONS", []) or []
    if not isinstance(reasons, list):
        reasons = [reasons]
    reasons_txt = " | ".join(str(x) for x in reasons).upper()

    assert dec == "FAIL", f"run={run_id}, DECISION={dec}"
    assert block is True, f"run={run_id}, BLOCK_PIPELINE={block}"
    assert words < 120, f"run={run_id}, WORDS={words}"
    assert "MIN_WORDS" in reasons_txt, f"run={run_id}, REASONS={reasons}"
    assert '"block_pipeline"' not in raw, f"run={run_id} ma niedozwolony key block_pipeline"

def test_p19_quality_direct_long_accepts_and_not_blocks():
    long_text = " ".join([f"slowo{i}" for i in range(1, 161)])  # 160 słów
    run_id, p, raw = _run_quality_direct(long_text, 120)

    dec = str(_get_ci(p, "DECISION", "")).upper()
    block = _get_ci(p, "BLOCK_PIPELINE", None)
    stats = _get_ci(p, "STATS", {}) or {}
    words = int((stats or {}).get("words", 0))
    reasons = _get_ci(p, "REASONS", []) or []
    if not isinstance(reasons, list):
        reasons = [reasons]
    reasons_txt = " | ".join(str(x) for x in reasons).upper()

    assert dec == "ACCEPT", f"run={run_id}, DECISION={dec}"
    assert block is False, f"run={run_id}, BLOCK_PIPELINE={block}"
    assert words >= 120, f"run={run_id}, WORDS={words}"
    assert "MIN_WORDS" not in reasons_txt, f"run={run_id}, REASONS={reasons}"
    assert '"block_pipeline"' not in raw, f"run={run_id} ma niedozwolony key block_pipeline"

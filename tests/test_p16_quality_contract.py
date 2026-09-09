import json
import time
from pathlib import Path
from uuid import uuid4
from starlette.testclient import TestClient
from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_runs_root

client = TestClient(app)
REPO_ROOT = Path(__file__).resolve().parents[1]

def _get(d, key, default=None):
    if not isinstance(d, dict):
        return default
    if key in d:
        return d[key]
    k1 = key.lower()
    if k1 in d:
        return d[k1]
    k2 = key.upper()
    if k2 in d:
        return d[k2]
    return default

def _read_quality_payload(run_id: str, timeout_sec: float = 6.0):
    steps_dir = get_runs_root() / run_id / "steps"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        q_files = sorted(steps_dir.glob("*_QUALITY.json"))
        if q_files:
            raw = q_files[-1].read_text(encoding="utf-8")
            j = json.loads(raw)
            return j["result"]["payload"], raw
        time.sleep(0.05)
    raise AssertionError(f"run={run_id}, brak *_QUALITY.json")

def _run_pipeline(topic: str, min_words: int, book_id: str):
    body = {
        "preset": "WRITING_STANDARD",
        "mode": "QUALITY",
        "book_id": book_id,
        "payload": {
            "topic": topic,
            "text": topic,
            "min_words": min_words
        }
    }
    time.sleep(1.05)  # anty-kolizja run_id
    r = client.post("/agent/step", json=body)
    assert r.status_code == 200, f"status={r.status_code}, body={r.text}"
    run_id = r.json()["run_id"]
    payload, raw = _read_quality_payload(run_id)
    return run_id, payload, raw

def test_p16_quality_short_fail_and_block(isolated_agentpro_storage):
    book_id = f"p16_quality_short_{uuid4().hex[:8]}"
    assert not (REPO_ROOT / "books" / book_id).exists()
    ensure_test_book_bible(book_id)
    run_id, p, raw = _run_pipeline("krotki test", 120, book_id)

    dec = str(_get(p, "DECISION", "")).upper()
    block = _get(p, "BLOCK_PIPELINE", None)
    stats = _get(p, "STATS", {}) or {}
    words = int(stats.get("words", 0))
    reasons = _get(p, "REASONS", []) or []
    if not isinstance(reasons, list):
        reasons = [reasons]
    reasons_txt = " | ".join(str(x) for x in reasons).upper()

    assert dec == "REVISE", f"run={run_id}, DECISION={dec}"
    assert block is False, f"run={run_id}, BLOCK_PIPELINE={block}"
    assert words < 120, f"run={run_id}, WORDS={words}"
    assert "MIN_WORDS" in reasons_txt, f"run={run_id}, REASONS={reasons}"
    assert '"block_pipeline"' not in raw, f"run={run_id} ma niedozwolony key block_pipeline"
    assert not (REPO_ROOT / "books" / book_id).exists()
    assert not (REPO_ROOT / "runs" / run_id).exists()

def test_p16_quality_long_accept_and_no_block(isolated_agentpro_storage):
    book_id = f"p16_quality_long_{uuid4().hex[:8]}"
    assert not (REPO_ROOT / "books" / book_id).exists()
    ensure_test_book_bible(book_id)
    long_topic = " ".join([f"slowo{i}" for i in range(1, 141)])  # 140
    run_id, p, raw = _run_pipeline(long_topic, 120, book_id)

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
    assert '"block_pipeline"' not in raw, f"run={run_id} ma niedozwolony key block_pipeline"
    assert not (REPO_ROOT / "books" / book_id).exists()
    assert not (REPO_ROOT / "runs" / run_id).exists()

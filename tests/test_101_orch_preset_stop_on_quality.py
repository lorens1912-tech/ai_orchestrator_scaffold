import requests
from pathlib import Path

BASE = "http://127.0.0.1:8000"

def test_orch_stop_test_stops_on_quality_non_accept():
    bad = "As an AI language model, I cannot comply with that request."

    r = requests.post(f"{BASE}/agent/step", json={
        "book_id": "default",
        "preset": "ORCH_STOP_TEST",
        "input": bad,
        "resume": False
    }, timeout=30)

    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("ok") is True, j

    artifacts = j.get("artifacts") or []
    assert len(artifacts) == 1, j

    assert j.get("stopped") is True, j
    stop = j.get("stop") or {}
    assert stop.get("mode") == "QUALITY", stop
    assert stop.get("decision") in ("REJECT","REVISE"), stop

    p = Path(artifacts[0])
    assert p.exists(), str(p)

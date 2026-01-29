import json
import requests
from pathlib import Path

BASE = "http://127.0.0.1:8000"

def test_orch_standard_runs_5_steps():
    r = requests.post(f"{BASE}/agent/step", json={
        "book_id": "default",
        "preset": "ORCH_STANDARD",
        "payload": {"text": "orch standard smoke", "team": "WRITER"},
        "resume": False
    }, timeout=30)

    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("ok") is True, j

    artifacts = j.get("artifacts") or []
    assert len(artifacts) == 5, j

    modes = []
    for ap in artifacts:
        p = Path(ap)
        assert p.exists(), str(p)
        step = json.loads(p.read_text(encoding="utf-8"))
        modes.append((step.get("mode") or "").upper())

    assert modes == ["PLAN","WRITE","CRITIC","EDIT","QUALITY"], modes

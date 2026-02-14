import requests

BASE = "http://127.0.0.1:8001"

def test_agent_step_rejects_invalid_team_id():
    r = requests.post(f"{BASE}/agent/step", json={
        "book_id": "default",
        "mode": "WRITE",
        "payload": {"text": "x", "team_id": "NO_SUCH_TEAM"},
        "resume": False
    }, timeout=30)
    assert r.status_code == 400


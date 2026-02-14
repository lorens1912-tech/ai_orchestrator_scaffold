import requests

BASE = "http://127.0.0.1:8001"

def test_team_cannot_run_wrong_mode():
    body = {
        "mode": "CRITIC",
        "book_id": "default",
        "payload": {"team_id": "AUTHOR", "text": "Test"},
        "resume": False
    }
    r = requests.post(f"{BASE}/agent/step", json=body, timeout=30)
    assert r.status_code in (400, 422), r.text


from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)

def test_team_cannot_run_wrong_mode():
    body = {
        "mode": "CRITIC",
        "book_id": "default",
        "payload": {"team_id": "AUTHOR", "text": "Test"},
        "resume": False
    }
    r = client.post("/agent/step", json=body)
    assert r.status_code in (400, 422), r.text

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible

client = TestClient(app, raise_server_exceptions=False)

def test_agent_step_rejects_invalid_team_id():
    ensure_test_book_bible("default")
    r = client.post("/agent/step", json={
        "book_id": "default",
        "mode": "WRITE",
        "payload": {"text": "x", "team_id": "NO_SUCH_TEAM"},
        "resume": False
    })
    assert r.status_code == 400

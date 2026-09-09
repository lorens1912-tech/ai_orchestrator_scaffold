from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_openapi_exposes_only_p20_agent_step_surface():
    data = client.get("/openapi.json").json()
    paths = data.get("paths") or {}

    assert "/agent/step" in paths
    assert "/books/agent/step" not in paths


def test_legacy_books_agent_step_is_not_available():
    resp = client.post("/books/agent/step", json={"book": "legacy_test"})
    assert resp.status_code == 404

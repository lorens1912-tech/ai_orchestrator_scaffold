import json

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_storage_root

client = TestClient(app, raise_server_exceptions=False)

TEST_PROJECT_ID = "TEST_PROJECT_A"
TEST_BOOK_ID = "TEST_BOOK_A"

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True

def test_validate():
    r = client.get("/config/validate")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["modes_count"] == len(d["mode_ids"])
    assert d["presets_count"] == len(d["preset_ids"])

def test_pipeline_tools():
    ensure_test_book_bible(TEST_BOOK_ID)
    payload = {
        "project_id": TEST_PROJECT_ID,
        "book_id": TEST_BOOK_ID,
        "preset": "PIPELINE_DRAFT",
        "payload": {"title": TEST_BOOK_ID},
    }
    r = client.post("/agent/step", json=payload)
    assert r.status_code == 200
    d = r.json()
    run_id = d["run_id"]
    p = get_storage_root() / "runs" / run_id / "steps" / "003_WRITE.json"
    assert p.exists()
    txt = p.read_text(encoding="utf-8")
    assert '"tool": "WRITE"' in txt


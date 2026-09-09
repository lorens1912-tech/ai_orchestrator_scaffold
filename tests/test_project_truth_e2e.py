import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible


def _read_json(path_like):
    path = Path(path_like)
    return json.loads(path.read_text(encoding="utf-8"))


def test_agent_step_writes_same_project_truth_sha_everywhere():
    client = TestClient(app)
    book_id = "novel_project_truth_e2e"
    ensure_test_book_bible(book_id)

    payload = {
        "mode": "WRITE",
        "payload": {
            "book_id": book_id,
            "text": "Scena testowa. Bohater odkrywa, że fałszywy trop był częścią kontroli dostępu do archiwum.",
        },
    }

    response = client.post("/agent/step", json=payload)
    assert response.status_code == 200, response.text

    data = response.json()
    assert data["ok"] is True
    assert "project_truth" in data

    project_truth = data["project_truth"]
    assert project_truth["contract"] == "MASTER_CANON_AGENTPRO"
    assert project_truth["path"].endswith("MASTER_CANON_AGENTPRO.md")
    assert len(project_truth["sha256"]) == 64

    response_sha = project_truth["sha256"]
    run_id = data["run_id"]

    chapter = _read_json(data["chapter_path"])
    audit = _read_json(Path("runs") / run_id / "audit.json")
    snapshot = _read_json(data["canon_snapshot_path"])

    assert chapter["project_truth"]["sha256"] == response_sha
    assert audit["project_truth"]["sha256"] == response_sha
    assert snapshot["project_truth"]["sha256"] == response_sha

    assert chapter["project_truth"]["path"] == project_truth["path"]
    assert audit["project_truth"]["path"] == project_truth["path"]
    assert snapshot["project_truth"]["path"] == project_truth["path"]

    assert chapter["project_truth"]["contract"] == "MASTER_CANON_AGENTPRO"
    assert audit["project_truth"]["contract"] == "MASTER_CANON_AGENTPRO"
    assert snapshot["project_truth"]["contract"] == "MASTER_CANON_AGENTPRO"

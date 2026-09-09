import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_runs_root, get_storage_root


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_json(path_like):
    path = Path(path_like)
    return json.loads(path.read_text(encoding="utf-8"))


def _storage_path(public_path: str) -> Path:
    return get_storage_root() / public_path


def _repo_book_dir(book_id: str) -> Path:
    return REPO_ROOT / "books" / book_id


def _repo_run_dir(run_id: str) -> Path:
    return REPO_ROOT / "runs" / run_id


def test_agent_step_writes_same_project_truth_sha_everywhere(isolated_agentpro_storage):
    client = TestClient(app)
    book_id = f"novel_project_truth_e2e_{uuid4().hex[:8]}"
    assert not _repo_book_dir(book_id).exists()
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

    assert data["chapter_path"].startswith("books/"), data
    assert data["canon_snapshot_path"].startswith("books/"), data
    chapter = _read_json(_storage_path(data["chapter_path"]))
    audit = _read_json(get_runs_root() / run_id / "audit.json")
    snapshot = _read_json(_storage_path(data["canon_snapshot_path"]))

    assert chapter["project_truth"]["sha256"] == response_sha
    assert audit["project_truth"]["sha256"] == response_sha
    assert snapshot["project_truth"]["sha256"] == response_sha

    assert chapter["project_truth"]["path"] == project_truth["path"]
    assert audit["project_truth"]["path"] == project_truth["path"]
    assert snapshot["project_truth"]["path"] == project_truth["path"]

    assert chapter["project_truth"]["contract"] == "MASTER_CANON_AGENTPRO"
    assert audit["project_truth"]["contract"] == "MASTER_CANON_AGENTPRO"
    assert snapshot["project_truth"]["contract"] == "MASTER_CANON_AGENTPRO"
    assert not _repo_book_dir(book_id).exists()
    assert not _repo_run_dir(run_id).exists()

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.master_canon import resolve_master_canon
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_storage_root


REPO_ROOT = Path(__file__).resolve().parents[1]


def _storage_path(public_path: str) -> Path:
    return get_storage_root() / public_path


def _repo_book_dir(book_id: str) -> Path:
    return REPO_ROOT / "books" / book_id


def _repo_run_dir(run_id: str) -> Path:
    return REPO_ROOT / "runs" / run_id


def test_master_canon_file_is_resolved() -> None:
    mc = resolve_master_canon()

    assert mc["scope"] == "MASTER_CANON_AGENTPRO"
    assert mc["path"] == "MASTER_CANON_AGENTPRO.md"
    assert isinstance(mc["sha256"], str) and len(mc["sha256"]) == 64
    assert isinstance(mc["text"], str) and len(mc["text"]) > 100


def test_agent_step_returns_master_canon_binding(isolated_agentpro_storage) -> None:
    client = TestClient(app)
    book_id = f"novel_runtime_test_{uuid4().hex[:8]}"
    assert not _repo_book_dir(book_id).exists()
    ensure_test_book_bible(book_id)

    response = client.post(
        "/agent/step",
        json={
            "mode": "WRITE",
            "payload": {
                "book_id": book_id,
                "text": "Regresyjny test spięcia master canonu z runtime.",
            },
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()

    assert data.get("ok") is True, data

    mc = data.get("master_canon") or {}
    assert mc.get("scope") == "MASTER_CANON_AGENTPRO", data
    assert mc.get("path") == "MASTER_CANON_AGENTPRO.md", data
    assert isinstance(mc.get("sha256"), str) and len(mc["sha256"]) == 64, data

    assert data["chapter_path"].startswith("books/"), data
    chapter_path = _storage_path(data["chapter_path"])
    assert chapter_path.exists(), chapter_path

    chapter_json = json.loads(chapter_path.read_text(encoding="utf-8"))
    chapter_mc = chapter_json.get("master_canon") or {}

    assert chapter_mc.get("scope") == "MASTER_CANON_AGENTPRO", chapter_json
    assert chapter_mc.get("path") == "MASTER_CANON_AGENTPRO.md", chapter_json
    assert chapter_mc.get("sha256") == mc.get("sha256"), chapter_json
    assert not _repo_book_dir(book_id).exists()
    assert not _repo_run_dir(data["run_id"]).exists()

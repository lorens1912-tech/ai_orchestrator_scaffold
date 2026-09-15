from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.canon_service import json_write
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_books_root, get_runs_root, get_storage_root


REPO_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def _controlled_write(payload: dict) -> dict:
    return {
        "tool": "WRITE",
        "payload": {"text": str(payload.get("text") or payload.get("input") or "")},
    }


def _book_dir(book_id: str) -> Path:
    return get_books_root() / book_id


def _run_dir(run_id: str) -> Path:
    return get_runs_root() / run_id


def _storage_path(public_path: str) -> Path:
    return get_storage_root() / public_path


def _repo_book_dir(book_id: str) -> Path:
    return REPO_ROOT / "books" / book_id


def _repo_run_dir(run_id: str) -> Path:
    return REPO_ROOT / "runs" / run_id


def _assert_repo_book_absent(book_id: str) -> None:
    assert not _repo_book_dir(book_id).exists(), _repo_book_dir(book_id)


def _assert_repo_run_absent(run_id: str) -> None:
    assert not _repo_run_dir(run_id).exists(), _repo_run_dir(run_id)


def test_agent_step_master_canon_sha_matches_project_truth_everywhere(isolated_agentpro_storage):
    book_id = f"p20_agent_sha_align_{uuid4().hex[:8]}"
    run_id = f"run_agent_sha_align_{uuid4().hex[:8]}"
    _assert_repo_book_absent(book_id)
    _assert_repo_run_absent(run_id)

    try:
        ensure_test_book_bible(book_id)
        book_dir = _book_dir(book_id)
        (book_dir / "chapters").mkdir(parents=True, exist_ok=True)
        (book_dir / "memory").mkdir(parents=True, exist_ok=True)

        json_write(book_dir / "memory" / "canon.json", {
            "timeline": [],
            "decisions": {},
            "facts": {},
            "approved_chapters": [],
        })

        with patch.dict(
            "app.p20_core.executor.TOOLS",
            {"WRITE": _controlled_write},
        ), patch(
            "app.p20_core.runtime.run_canon_check",
            side_effect=[
                {"ok": True, "issues": [], "scene_ref": ""},
                {"ok": True, "issues": [], "scene_ref": ""},
            ],
        ):
            resp = client.post("/agent/step", json={
                "mode": "WRITE",
                "payload": {
                    "book_id": book_id,
                    "run_id": run_id,
                    "text": "Scena wejściowa.",
                },
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert "master_canon" in data, data
        assert "project_truth" in data, data

        response_master_sha = data["master_canon"]["sha256"]
        response_truth_sha = data["project_truth"]["sha256"]
        assert response_master_sha == response_truth_sha

        assert data["chapter_path"].startswith("books/"), data
        assert data["canon_snapshot_path"].startswith("books/"), data
        chapter_path = _storage_path(data["chapter_path"])
        audit_path = _run_dir(run_id) / "audit.json"
        snapshot_path = _storage_path(data["canon_snapshot_path"])

        chapter = json.loads(chapter_path.read_text(encoding="utf-8"))
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

        assert "master_canon" in chapter, chapter
        assert "project_truth" in chapter, chapter
        assert "master_canon" in audit, audit
        assert "project_truth" in audit, audit
        assert "project_truth" in snapshot, snapshot

        if "master_canon" in snapshot:
            snapshot_master_sha = snapshot["master_canon"]["sha256"]
        else:
            snapshot_master_sha = response_master_sha

        chapter_master_sha = chapter["master_canon"]["sha256"]
        chapter_truth_sha = chapter["project_truth"]["sha256"]
        audit_master_sha = audit["master_canon"]["sha256"]
        audit_truth_sha = audit["project_truth"]["sha256"]
        snapshot_truth_sha = snapshot["project_truth"]["sha256"]

        assert chapter_master_sha == chapter_truth_sha
        assert audit_master_sha == audit_truth_sha
        assert snapshot_master_sha == snapshot_truth_sha

        assert chapter_master_sha == response_master_sha
        assert audit_master_sha == response_master_sha
        assert snapshot_master_sha == response_master_sha
    finally:
        _assert_repo_book_absent(book_id)
        _assert_repo_run_absent(run_id)

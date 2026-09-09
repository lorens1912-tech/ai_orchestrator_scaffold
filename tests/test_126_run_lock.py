from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.lock_service import acquire_run_lock, release_run_lock, run_lock_path
from app.p20_core.storage_paths import get_books_root, get_runs_root


REPO_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def _book_dir(book_id: str) -> Path:
    return get_books_root() / book_id


def _run_dir(run_id: str) -> Path:
    return get_runs_root() / run_id


def _repo_book_dir(book_id: str) -> Path:
    return REPO_ROOT / "books" / book_id


def _repo_run_dir(run_id: str) -> Path:
    return REPO_ROOT / "runs" / run_id


def _assert_repo_book_absent(book_id: str) -> None:
    assert not _repo_book_dir(book_id).exists(), _repo_book_dir(book_id)


def _assert_repo_run_absent(run_id: str) -> None:
    assert not _repo_run_dir(run_id).exists(), _repo_run_dir(run_id)


def test_run_lock_exists_during_execute_and_is_released_after(isolated_agentpro_storage):
    book_id = f"p20_lock_book_{uuid4().hex[:8]}"
    run_id = f"run_lock_{uuid4().hex[:8]}"
    _assert_repo_book_absent(book_id)
    _assert_repo_run_absent(run_id)

    try:
        ensure_test_book_bible(book_id)

        def _exec_guard(*args, **kwargs):
            assert run_lock_path(run_id).exists()
            return {"artifact_paths": ["runs/fake_lock/001_WRITE.json"]}

        with patch(
            "app.p20_core.runtime.execute_stub",
            side_effect=_exec_guard,
        ), patch(
            "app.p20_core.runtime.read_artifact_text",
            return_value="Scena dla testu lock ACCEPT.",
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
                    "text": "Scena z lockiem.",
                },
            })

        assert resp.status_code == 200
        assert not run_lock_path(run_id).exists()
    finally:
        _assert_repo_book_absent(book_id)
        _assert_repo_run_absent(run_id)


def test_existing_run_lock_returns_409(isolated_agentpro_storage):
    book_id = f"p20_lock_book_{uuid4().hex[:8]}"
    run_id = f"run_lock_{uuid4().hex[:8]}"
    _assert_repo_book_absent(book_id)
    _assert_repo_run_absent(run_id)

    try:
        ensure_test_book_bible(book_id)
        acquire_run_lock(run_id, book_id)

        resp = client.post("/agent/step", json={
            "mode": "WRITE",
            "payload": {
                "book_id": book_id,
                "run_id": run_id,
                "text": "Scena z konfliktem locka.",
            },
        })

        assert resp.status_code == 409
        data = resp.json()
        assert data["detail"]["code"] == "RUN_LOCKED"
        assert data["detail"]["run_id"] == run_id
        assert data["detail"]["book_id"] == book_id
    finally:
        release_run_lock(run_id)
        _assert_repo_book_absent(book_id)
        _assert_repo_run_absent(run_id)

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.lock_service import acquire_book_lock, book_lock_path, release_book_lock
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


def test_book_lock_exists_during_execute_and_is_released_after(isolated_agentpro_storage):
    book_id = f"p20_book_lock_{uuid4().hex[:8]}"
    run_id = f"run_book_lock_{uuid4().hex[:8]}"
    _assert_repo_book_absent(book_id)
    _assert_repo_run_absent(run_id)

    try:
        ensure_test_book_bible(book_id)

        def _write_guard(payload):
            assert book_lock_path(book_id).exists()
            return {
                "tool": "WRITE",
                "payload": {
                    "text": str(payload.get("text") or payload.get("input") or "")
                },
            }

        with patch.dict(
            "app.p20_core.executor.TOOLS",
            {"WRITE": _write_guard},
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
                    "text": "Scena z book lock.",
                },
            })

        assert resp.status_code == 200
        assert not book_lock_path(book_id).exists()
    finally:
        _assert_repo_book_absent(book_id)
        _assert_repo_run_absent(run_id)


def test_existing_book_lock_returns_409(isolated_agentpro_storage):
    book_id = f"p20_book_lock_{uuid4().hex[:8]}"
    run_id = f"run_book_lock_{uuid4().hex[:8]}"
    other_run_id = f"{run_id}_other"
    _assert_repo_book_absent(book_id)
    _assert_repo_run_absent(run_id)
    _assert_repo_run_absent(other_run_id)

    try:
        ensure_test_book_bible(book_id)
        acquire_book_lock(book_id, run_id)

        resp = client.post("/agent/step", json={
            "mode": "WRITE",
            "payload": {
                "book_id": book_id,
                "run_id": other_run_id,
                "text": "Scena z konfliktem book locka.",
            },
        })

        assert resp.status_code == 409
        data = resp.json()
        assert data["detail"]["code"] == "BOOK_LOCKED"
        assert data["detail"]["book_id"] == book_id
    finally:
        release_book_lock(book_id)
        _assert_repo_book_absent(book_id)
        _assert_repo_run_absent(run_id)
        _assert_repo_run_absent(other_run_id)

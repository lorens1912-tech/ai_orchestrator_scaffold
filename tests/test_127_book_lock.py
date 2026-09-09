from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.lock_service import acquire_book_lock, book_lock_path, release_book_lock


REPO_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def _book_dir(book_id: str) -> Path:
    return REPO_ROOT / "books" / book_id


def _run_dir(run_id: str) -> Path:
    return REPO_ROOT / "runs" / run_id


def _clean_book(book_id: str) -> None:
    p = _book_dir(book_id)
    if p.exists():
        shutil.rmtree(p)


def _clean_run(run_id: str) -> None:
    p = _run_dir(run_id)
    if p.exists():
        shutil.rmtree(p)


def test_book_lock_exists_during_execute_and_is_released_after():
    book_id = f"p20_book_lock_{uuid4().hex[:8]}"
    run_id = f"run_book_lock_{uuid4().hex[:8]}"

    try:
        def _exec_guard(*args, **kwargs):
            assert book_lock_path(book_id).exists()
            return {"artifact_paths": ["runs/fake_book_lock/001_WRITE.json"]}

        with patch(
            "app.p20_core.runtime.execute_stub",
            side_effect=_exec_guard,
        ), patch(
            "app.p20_core.runtime.read_artifact_text",
            return_value="Scena dla testu BOOK LOCK ACCEPT.",
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
        _clean_book(book_id)
        _clean_run(run_id)


def test_existing_book_lock_returns_409():
    book_id = f"p20_book_lock_{uuid4().hex[:8]}"
    run_id = f"run_book_lock_{uuid4().hex[:8]}"

    try:
        acquire_book_lock(book_id, run_id)

        resp = client.post("/agent/step", json={
            "mode": "WRITE",
            "payload": {
                "book_id": book_id,
                "run_id": f"{run_id}_other",
                "text": "Scena z konfliktem book locka.",
            },
        })

        assert resp.status_code == 409
        data = resp.json()
        assert data["detail"]["code"] == "BOOK_LOCKED"
        assert data["detail"]["book_id"] == book_id
    finally:
        release_book_lock(book_id)
        _clean_book(book_id)
        _clean_run(run_id)

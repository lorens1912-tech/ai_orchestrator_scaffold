from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_books_root, get_runs_root


REPO_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def _book_dir(book_id: str) -> Path:
    return get_books_root() / book_id


def _run_dir(run_id: str) -> Path:
    return get_runs_root() / run_id


def _repo_book_dir(book_id: str) -> Path:
    return REPO_ROOT / "books" / book_id


def _repo_run_dir(run_id: str | None) -> Path | None:
    if not run_id:
        return None
    return REPO_ROOT / "runs" / run_id


def _assert_repo_book_absent(book_id: str) -> None:
    assert not _repo_book_dir(book_id).exists(), _repo_book_dir(book_id)


def _assert_repo_run_absent(run_id: str | None) -> None:
    repo_run = _repo_run_dir(run_id)
    if repo_run is not None:
        assert not repo_run.exists(), repo_run


@pytest.mark.usefixtures("isolated_agentpro_storage")
class TestP20RunState(unittest.TestCase):
    def test_accept_write_persists_run_state(self):
        book_id = f"p20_state_accept_{uuid4().hex[:8]}"
        run_id = f"run_state_accept_{uuid4().hex[:8]}"
        _assert_repo_book_absent(book_id)
        _assert_repo_run_absent(run_id)
        try:
            ensure_test_book_bible(book_id)
            with patch(
                "app.p20_core.runtime.execute_stub",
                return_value={"artifact_paths": ["runs/fake_accept_state/001_WRITE.json"]},
            ), patch(
                "app.p20_core.runtime.read_artifact_text",
                return_value="Scena dla testu run_state ACCEPT.",
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

            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            state_path = _run_dir(run_id) / "run_state.json"
            self.assertTrue(state_path.exists(), state_path)

            state = __import__("json").loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["run_id"], run_id)
            self.assertEqual(state["book_id"], book_id)
            self.assertEqual(state["status"], "ACCEPT")
            self.assertEqual(state["chapter_path"], data["chapter_path"])
            self.assertEqual(state["last_modes"], ["WRITE"])
            self.assertEqual(len(state["last_artifact_paths"]), 1)
        finally:
            _assert_repo_book_absent(book_id)
            _assert_repo_run_absent(run_id)

    def test_resume_reuses_latest_run_id_for_same_book(self):
        book_id = f"p20_resume_{uuid4().hex[:8]}"
        run_id = f"run_resume_{uuid4().hex[:8]}"
        _assert_repo_book_absent(book_id)
        _assert_repo_run_absent(run_id)
        try:
            ensure_test_book_bible(book_id)
            with patch(
                "app.p20_core.runtime.execute_stub",
                return_value={"artifact_paths": ["runs/fake_resume/001_WRITE.json"]},
            ), patch(
                "app.p20_core.runtime.read_artifact_text",
                return_value="Scena dla testu resume.",
            ), patch(
                "app.p20_core.runtime.run_canon_check",
                side_effect=[
                    {"ok": True, "issues": [], "scene_ref": ""},
                    {"ok": True, "issues": [], "scene_ref": ""},
                    {"ok": True, "issues": [], "scene_ref": ""},
                    {"ok": True, "issues": [], "scene_ref": ""},
                ],
            ):
                r1 = client.post("/agent/step", json={
                    "mode": "WRITE",
                    "payload": {
                        "book_id": book_id,
                        "run_id": run_id,
                        "text": "Scena pierwsza.",
                    },
                }).json()

                r2 = client.post("/agent/step", json={
                    "mode": "WRITE",
                    "payload": {
                        "book_id": book_id,
                        "resume": True,
                        "text": "Scena druga.",
                    },
                }).json()

            self.assertEqual(r1["run_id"], run_id)
            self.assertEqual(r2["run_id"], run_id)

            marker = _book_dir(book_id) / "audit" / "latest_run_id.txt"
            self.assertTrue(marker.exists(), marker)
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), run_id)
        finally:
            _assert_repo_book_absent(book_id)
            _assert_repo_run_absent(run_id)

    def test_resume_does_not_cross_books(self):
        book_a = f"p20_book_a_{uuid4().hex[:8]}"
        book_b = f"p20_book_b_{uuid4().hex[:8]}"
        run_a = f"run_a_{uuid4().hex[:8]}"
        _assert_repo_book_absent(book_a)
        _assert_repo_book_absent(book_b)
        _assert_repo_run_absent(run_a)
        try:
            ensure_test_book_bible(book_a)
            ensure_test_book_bible(book_b)
            with patch(
                "app.p20_core.runtime.execute_stub",
                return_value={"artifact_paths": ["runs/fake_cross/001_WRITE.json"]},
            ), patch(
                "app.p20_core.runtime.read_artifact_text",
                return_value="Scena dla testu izolacji run.",
            ), patch(
                "app.p20_core.runtime.run_canon_check",
                side_effect=[
                    {"ok": True, "issues": [], "scene_ref": ""},
                    {"ok": True, "issues": [], "scene_ref": ""},
                    {"ok": True, "issues": [], "scene_ref": ""},
                    {"ok": True, "issues": [], "scene_ref": ""},
                ],
            ):
                ra = client.post("/agent/step", json={
                    "mode": "WRITE",
                    "payload": {
                        "book_id": book_a,
                        "run_id": run_a,
                        "text": "Scena A.",
                    },
                }).json()

                rb = client.post("/agent/step", json={
                    "mode": "WRITE",
                    "payload": {
                        "book_id": book_b,
                        "resume": True,
                        "text": "Scena B.",
                    },
                }).json()

            self.assertEqual(ra["run_id"], run_a)
            self.assertNotEqual(rb["run_id"], run_a)
        finally:
            _assert_repo_book_absent(book_a)
            _assert_repo_book_absent(book_b)
            _assert_repo_run_absent(run_a)
            if "rb" in locals():
                _assert_repo_run_absent(rb.get("run_id"))

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
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


def _storage_path(public_path: str) -> Path:
    return get_storage_root() / public_path


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
class TestP20NovelCore(unittest.TestCase):
    def _post_write(self, book_id: str, text: str):
        body = {
            "mode": "WRITE",
            "payload": {
                "book_id": book_id,
                "text": text,
            },
        }
        return client.post("/agent/step", json=body)

    def test_accept_write_saves_chapter(self):
        book_id = f"p20_accept_{uuid4().hex[:8]}"
        _assert_repo_book_absent(book_id)
        try:
            ensure_test_book_bible(book_id)
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
                resp = self._post_write(book_id, "Wejściowa scena testowa ACCEPT.")

            self.assertEqual(resp.status_code, 200)
            data = resp.json()

            self.assertTrue(data["ok"], data)
            self.assertEqual(data["quality_gate"]["decision"], "ACCEPT", data)
            self.assertEqual(data["artifact_count"], 1, data)
            self.assertTrue(data.get("chapter_path"), data)

            self.assertTrue(data["chapter_path"].startswith("books/"), data)
            chapter_path = _storage_path(data["chapter_path"])
            self.assertTrue(chapter_path.exists(), chapter_path)

            chapters = sorted((_book_dir(book_id) / "chapters").glob("chapter_*.json"))
            self.assertEqual(len(chapters), 1, chapters)
        finally:
            _assert_repo_book_absent(book_id)
            if "data" in locals():
                _assert_repo_run_absent(data.get("run_id"))

    def test_pre_write_reject_blocks_execute_and_save(self):
        book_id = f"p20_pre_reject_{uuid4().hex[:8]}"
        _assert_repo_book_absent(book_id)
        try:
            ensure_test_book_bible(book_id)
            with patch(
                "app.p20_core.runtime.execute_stub",
                return_value={"artifact_paths": ["runs/fake_pre/001_WRITE.json"]},
            ) as mock_exec, patch(
                "app.p20_core.runtime.run_canon_check",
                return_value={"ok": False, "issues": ["canon mismatch"], "scene_ref": ""},
            ):
                resp = self._post_write(book_id, "Wejściowa scena testowa PRE REJECT.")

            self.assertEqual(resp.status_code, 200)
            data = resp.json()

            self.assertFalse(data["ok"], data)
            self.assertEqual(data["quality_gate"]["decision"], "REJECT", data)
            self.assertEqual(data["artifact_paths"], [], data)
            self.assertFalse(data.get("chapter_path"), data)
            self.assertEqual(data["post_canon_check"]["status"], "skipped", data)

            chapters_dir = _book_dir(book_id) / "chapters"
            chapters = sorted(chapters_dir.glob("chapter_*.json")) if chapters_dir.exists() else []
            self.assertEqual(len(chapters), 0, chapters)

            mock_exec.assert_not_called()
        finally:
            _assert_repo_book_absent(book_id)
            if "data" in locals():
                _assert_repo_run_absent(data.get("run_id"))

    def test_post_write_reject_blocks_chapter_save(self):
        book_id = f"p20_post_reject_{uuid4().hex[:8]}"
        _assert_repo_book_absent(book_id)
        try:
            ensure_test_book_bible(book_id)
            with patch(
                "app.p20_core.runtime.execute_stub",
                return_value={"artifact_paths": ["runs/fake_post/001_WRITE.json"]},
            ) as mock_exec, patch(
                "app.p20_core.runtime.read_artifact_text",
                return_value="Scena wygenerowana przez test POST REJECT.",
            ), patch(
                "app.p20_core.runtime.run_canon_check",
                side_effect=[
                    {"ok": True, "issues": [], "scene_ref": ""},
                    {"ok": False, "issues": ["post write canon violation"], "scene_ref": ""},
                ],
            ):
                resp = self._post_write(book_id, "Wejściowa scena testowa POST REJECT.")

            self.assertEqual(resp.status_code, 200)
            data = resp.json()

            self.assertFalse(data["ok"], data)
            self.assertEqual(data["quality_gate"]["decision"], "REJECT", data)
            self.assertEqual(data["artifact_count"], 1, data)
            self.assertFalse(data.get("chapter_path"), data)

            chapters_dir = _book_dir(book_id) / "chapters"
            chapters = sorted(chapters_dir.glob("chapter_*.json")) if chapters_dir.exists() else []
            self.assertEqual(len(chapters), 0, chapters)

            mock_exec.assert_called_once()
        finally:
            _assert_repo_book_absent(book_id)
            if "data" in locals():
                _assert_repo_run_absent(data.get("run_id"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


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


class TestP20CanonCommit(unittest.TestCase):
    def test_accept_commits_chapter_to_canon_memory(self):
        book_id = f"p20_commit_accept_{uuid4().hex[:8]}"
        run_id = f"run_commit_accept_{uuid4().hex[:8]}"
        try:
            with patch(
                "app.p20_core.runtime.execute_stub",
                return_value={"artifact_paths": ["runs/fake_commit/001_WRITE.json"]},
            ), patch(
                "app.p20_core.runtime.read_artifact_text",
                return_value="Scena dla testu canon commit ACCEPT.",
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
                        "text": "Scena wejściowa ACCEPT.",
                    },
                })

            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["ok"], data)
            self.assertTrue(data["chapter_path"], data)

            canon_path = _book_dir(book_id) / "memory" / "canon.json"
            self.assertTrue(canon_path.exists(), canon_path)

            canon = json.loads(canon_path.read_text(encoding="utf-8"))
            self.assertEqual(canon["chapter_count"], 1)
            self.assertEqual(canon["last_accepted_chapter"]["chapter_path"], data["chapter_path"])
            self.assertEqual(len(canon["approved_chapters"]), 1)
            self.assertEqual(canon["approved_chapters"][0]["run_id"], run_id)
        finally:
            _clean_book(book_id)
            _clean_run(run_id)

    def test_reject_does_not_commit_chapter_to_canon_memory(self):
        book_id = f"p20_commit_reject_{uuid4().hex[:8]}"
        run_id = f"run_commit_reject_{uuid4().hex[:8]}"
        try:
            with patch(
                "app.p20_core.runtime.execute_stub",
                return_value={"artifact_paths": ["runs/fake_commit_reject/001_WRITE.json"]},
            ), patch(
                "app.p20_core.runtime.read_artifact_text",
                return_value="Scena dla testu canon commit REJECT.",
            ), patch(
                "app.p20_core.runtime.run_canon_check",
                side_effect=[
                    {"ok": True, "issues": [], "scene_ref": ""},
                    {"ok": False, "issues": ["canon violation"], "scene_ref": ""},
                ],
            ):
                resp = client.post("/agent/step", json={
                    "mode": "WRITE",
                    "payload": {
                        "book_id": book_id,
                        "run_id": run_id,
                        "text": "Scena wejściowa REJECT.",
                    },
                })

            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertFalse(data["ok"], data)
            self.assertFalse(data["chapter_path"], data)

            canon_path = _book_dir(book_id) / "memory" / "canon.json"
            self.assertTrue(canon_path.exists(), canon_path)

            canon = json.loads(canon_path.read_text(encoding="utf-8"))
            approved = canon.get("approved_chapters") or []
            self.assertEqual(len(approved), 0)
            self.assertFalse(canon.get("last_accepted_chapter"))
            self.assertNotIn("chapter_count", canon)
        finally:
            _clean_book(book_id)
            _clean_run(run_id)


if __name__ == "__main__":
    unittest.main()

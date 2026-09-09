import unittest
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_books_root, get_runs_root

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def _repo_book_dir(book_id: str) -> Path:
    return ROOT / "books" / book_id


def _repo_run_dir(run_id: str | None) -> Path | None:
    if not run_id:
        return None
    return ROOT / "runs" / run_id



@pytest.mark.usefixtures("isolated_agentpro_storage")
class Test083ResumeEmptyLatestFileScansRuns(unittest.TestCase):
    def test_resume_scans_runs_when_latest_file_empty(self):
        book_id = f"resume_test_083_{uuid4().hex[:8]}"

        self.assertFalse(_repo_book_dir(book_id).exists(), _repo_book_dir(book_id))
        rid1 = None

        try:
            ensure_test_book_bible(book_id)

            # start -> rid1
            r1 = client.post("/agent/step", json={
                "book_id": book_id,
                "mode": "PLAN",
                "payload": {"text": "Temat"},
                "resume": False
            })
            self.assertEqual(r1.status_code, 200)
            rid1 = r1.json()["run_id"]

            # overwrite latest file with empty content
            latest = get_books_root() / book_id / "audit" / "latest_run_id.txt"
            latest.parent.mkdir(parents=True, exist_ok=True)
            latest.write_text("\n", encoding="utf-8")

            # resume -> should find rid1 by scanning runs/ and reuse same run_id
            r2 = client.post("/agent/step", json={
                "book_id": book_id,
                "mode": "WRITE",
                "payload": {},
                "resume": True
            })
            self.assertEqual(r2.status_code, 200)
            rid2 = r2.json()["run_id"]

            self.assertEqual(rid1, rid2, (rid1, rid2))
            self.assertTrue((get_books_root() / book_id).exists())
            self.assertTrue((get_runs_root() / rid1).exists())
        finally:
            self.assertFalse(_repo_book_dir(book_id).exists(), _repo_book_dir(book_id))
            repo_run_1 = _repo_run_dir(rid1)
            if repo_run_1 is not None:
                self.assertFalse(repo_run_1.exists(), repo_run_1)

if __name__ == "__main__":
    unittest.main()

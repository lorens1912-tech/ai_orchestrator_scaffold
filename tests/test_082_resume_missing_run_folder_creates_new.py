import unittest
import requests
import shutil
from pathlib import Path

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
BOOKS = ROOT / "books"

class Test082ResumeMissingRunFolderCreatesNew(unittest.TestCase):
    def test_resume_creates_new_when_latest_run_folder_missing(self):
        book_id = "resume_test_082"

        # cleanup (best-effort)
        shutil.rmtree(BOOKS / book_id, ignore_errors=True)

        # start -> rid1
        r1 = requests.post(f"{BASE}/agent/step", json={
            "book_id": book_id,
            "mode": "PLAN",
            "payload": {"text": "Temat"},
            "resume": False
        }, timeout=30)
        self.assertEqual(r1.status_code, 200)
        rid1 = r1.json()["run_id"]

        # delete run folder for rid1
        shutil.rmtree(RUNS / rid1, ignore_errors=True)

        # resume -> should create new rid2 (because rid1 folder missing)
        r2 = requests.post(f"{BASE}/agent/step", json={
            "book_id": book_id,
            "mode": "WRITE",
            "payload": {},
            "resume": True
        }, timeout=30)
        self.assertEqual(r2.status_code, 200)
        rid2 = r2.json()["run_id"]

        self.assertNotEqual(rid1, rid2, (rid1, rid2))

        # cleanup (best-effort)
        shutil.rmtree(RUNS / rid2, ignore_errors=True)
        shutil.rmtree(BOOKS / book_id, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()

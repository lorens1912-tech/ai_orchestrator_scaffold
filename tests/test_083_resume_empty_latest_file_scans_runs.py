import unittest
import requests
import shutil
from pathlib import Path

BASE = "http://127.0.0.1:8001"
ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
BOOKS = ROOT / "books"

class Test083ResumeEmptyLatestFileScansRuns(unittest.TestCase):
    def test_resume_scans_runs_when_latest_file_empty(self):
        book_id = "resume_test_083"

        # cleanup
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

        # overwrite latest file with empty content
        latest = BOOKS / book_id / "_latest_run_id.txt"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text("\n", encoding="utf-8")

        # resume -> should find rid1 by scanning runs/ and reuse same run_id
        r2 = requests.post(f"{BASE}/agent/step", json={
            "book_id": book_id,
            "mode": "WRITE",
            "payload": {},
            "resume": True
        }, timeout=30)
        self.assertEqual(r2.status_code, 200)
        rid2 = r2.json()["run_id"]

        self.assertEqual(rid1, rid2, (rid1, rid2))

        # cleanup (best-effort)
        shutil.rmtree(RUNS / rid1, ignore_errors=True)
        shutil.rmtree(BOOKS / book_id, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()


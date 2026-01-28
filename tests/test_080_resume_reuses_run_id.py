import unittest
import requests

BASE = "http://127.0.0.1:8000"

class Test080ResumeReusesRunId(unittest.TestCase):
    def test_resume_reuses_latest_run_id(self):
        r1 = requests.post(f"{BASE}/agent/step", json={
            "book_id": "resume_test_080",
            "mode": "PLAN",
            "payload": {"text": "Temat"},
            "resume": False
        }, timeout=30)
        self.assertEqual(r1.status_code, 200)
        rid1 = r1.json()["run_id"]

        r2 = requests.post(f"{BASE}/agent/step", json={
            "book_id": "resume_test_080",
            "mode": "WRITE",
            "payload": {},
            "resume": True
        }, timeout=30)
        self.assertEqual(r2.status_code, 200)
        rid2 = r2.json()["run_id"]

        self.assertEqual(rid1, rid2)

if __name__ == "__main__":
    unittest.main()

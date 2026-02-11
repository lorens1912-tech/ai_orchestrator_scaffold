import json
import unittest
import requests
from pathlib import Path

BASE = "http://127.0.0.1:8001"

class Test100OrchPresetStandardRuns5Steps(unittest.TestCase):
    def test_orch_standard_runs_5_steps(self):
        r = requests.post(f"{BASE}/agent/step", json={
            "book_id": "default",
            "preset": "ORCH_STANDARD",
            "payload": {"text": "orch standard smoke", "team": "WRITER"},
            "resume": False
        }, timeout=30)

        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)

        artifacts = j.get("artifacts") or []
        self.assertEqual(len(artifacts), 5, j)

        modes = []
        for ap in artifacts:
            p = Path(ap)
            self.assertTrue(p.exists(), str(p))
            step = json.loads(p.read_text(encoding="utf-8"))
            modes.append((step.get("mode") or "").upper())

        self.assertEqual(modes, ["PLAN","WRITE","CRITIC","EDIT","QUALITY"])

if __name__ == "__main__":
    unittest.main()


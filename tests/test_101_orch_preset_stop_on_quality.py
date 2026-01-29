import unittest
import requests
from pathlib import Path

BASE = "http://127.0.0.1:8000"

class Test101OrchPresetStopOnQuality(unittest.TestCase):
    def test_orch_stop_test_stops_on_quality_non_accept(self):
        bad = "As an AI language model, I cannot comply with that request."

        r = requests.post(f"{BASE}/agent/step", json={
            "book_id": "default",
            "preset": "ORCH_STOP_TEST",
            "input": bad,
            "resume": False
        }, timeout=30)

        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)

        artifacts = j.get("artifacts") or []
        self.assertEqual(len(artifacts), 1, j)

        self.assertTrue(j.get("stopped") is True, j)
        stop = j.get("stop") or {}
        self.assertEqual(stop.get("mode"), "QUALITY", stop)
        self.assertIn(stop.get("decision"), ("REJECT","REVISE"), stop)

        p = Path(artifacts[0])
        self.assertTrue(p.exists(), str(p))

if __name__ == "__main__":
    unittest.main()

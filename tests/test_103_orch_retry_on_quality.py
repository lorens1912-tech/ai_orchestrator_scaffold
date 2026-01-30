import unittest
import requests

BASE = "http://127.0.0.1:8000"

class Test103OrchRetryOnQuality(unittest.TestCase):
    def test_orch_retry_injects_edit_and_second_quality(self):
        body = {
            "preset": "ORCH_RETRY_TEST",
            "payload": {"text": "To jest lista:\n- a\n- b\n- c\n"}
        }
        r = requests.post(f"{BASE}/agent/step", json=body, timeout=30)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)

        artifacts = j.get("artifacts") or []
        self.assertGreaterEqual(len(artifacts), 3, f"Expected >=3 steps (QUALITY->EDIT->QUALITY), got {len(artifacts)}")

        s1 = str(artifacts[0]).upper()
        s2 = str(artifacts[1]).upper()
        s3 = str(artifacts[2]).upper()
        self.assertIn("QUALITY", s1, s1)
        self.assertIn("EDIT", s2, s2)
        self.assertIn("QUALITY", s3, s3)

if __name__ == "__main__":
    unittest.main()

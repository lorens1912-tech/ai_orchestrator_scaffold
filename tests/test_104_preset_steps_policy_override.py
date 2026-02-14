import json
import unittest
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8001"

class Test104PresetStepsPolicyOverride(unittest.TestCase):
    def test_orch_standard_steps_policy_is_applied_to_write(self):
        body = {"preset": "ORCH_STANDARD", "payload": {"text": "x"}}
        r = requests.post(f"{BASE}/agent/step", json=body, timeout=60)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)

        artifacts = j.get("artifacts") or []
        self.assertTrue(len(artifacts) >= 2, artifacts)

        write_path = None
        for ap in artifacts:
            if str(ap).upper().endswith("_WRITE.JSON"):
                write_path = ap
                break
        self.assertTrue(write_path, artifacts)

        data = json.loads(Path(write_path).read_text(encoding="utf-8"))
        inp = data.get("input") or {}
        self.assertEqual(inp.get("_requested_policy"), "WRITE_POLICY_TEST", inp)

if __name__ == "__main__":
    unittest.main()


import json
import unittest
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]

class Test105SequenceAndEffectiveIds(unittest.TestCase):
    def test_sequence_artifact_exists_and_effective_policy_is_recorded(self):
        r = requests.post(f"{BASE}/agent/step", json={"preset":"ORCH_STANDARD","payload":{"text":"x"}}, timeout=60)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)

        run_id = j.get("run_id")
        self.assertTrue(run_id, j)

        steps_dir = ROOT / "runs" / run_id / "steps"
        seq = steps_dir / "000_SEQUENCE.json"
        self.assertTrue(seq.exists(), f"Missing: {seq}")

        # find WRITE artifact
        write_path = None
        for ap in (j.get("artifacts") or []):
            if str(ap).upper().endswith("_WRITE.JSON"):
                write_path = Path(ap)
                break
        self.assertTrue(write_path and write_path.exists(), j.get("artifacts"))

        data = json.loads(write_path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("effective_policy_id"), "WRITE_POLICY_TEST", data)

if __name__ == "__main__":
    unittest.main()

import unittest
import requests

BASE = "http://127.0.0.1:8000"

class Test102ConfigPresetsEndpoint(unittest.TestCase):
    def test_config_presets_has_orch(self):
        r = requests.get(f"{BASE}/config/presets", timeout=10)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        presets = j.get("presets") if isinstance(j, dict) else {}
        self.assertTrue(isinstance(presets, dict), j)

        self.assertIn("ORCH_STANDARD", presets, list(presets.keys()))
        self.assertIn("ORCH_STOP_TEST", presets, list(presets.keys()))

if __name__ == "__main__":
    unittest.main()

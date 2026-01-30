import unittest
import requests

BASE = "http://127.0.0.1:8000"

class Test102ConfigPresetsEndpoint(unittest.TestCase):
    def test_config_presets_has_orch(self):
        r = requests.get(f"{BASE}/config/presets", timeout=10)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(isinstance(j, dict), j)

        preset_ids = j.get("preset_ids") or []
        self.assertIn("ORCH_STANDARD", preset_ids, preset_ids)
        self.assertIn("ORCH_STOP_TEST", preset_ids, preset_ids)

        presets = j.get("presets") or []
        self.assertTrue(isinstance(presets, list), j)
        ids = [p.get("id") for p in presets if isinstance(p, dict)]
        self.assertIn("ORCH_STANDARD", ids, ids)
        self.assertIn("ORCH_STOP_TEST", ids, ids)

if __name__ == "__main__":
    unittest.main()

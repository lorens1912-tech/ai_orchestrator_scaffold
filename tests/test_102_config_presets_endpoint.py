import unittest
import requests

BASE = "http://127.0.0.1:8001"


def _extract_preset_ids(j: dict) -> list:
    # Accept both schemas:
    # 1) flat:   {"preset_ids":[...], "presets":[...], "presets_count":N}
    # 2) nested: {"presets":{"preset_ids":[...], "presets":[...], "presets_count":N}}
    if not isinstance(j, dict):
        return []

    preset_ids = j.get("preset_ids")
    if isinstance(preset_ids, list):
        return preset_ids

    inner = j.get("presets")
    if isinstance(inner, dict):
        preset_ids = inner.get("preset_ids")
        if isinstance(preset_ids, list):
            return preset_ids

    return []


class Test102ConfigPresetsEndpoint(unittest.TestCase):
    def test_config_presets_has_orch(self):
        r = requests.get(f"{BASE}/config/presets", timeout=10)
        self.assertEqual(r.status_code, 200, r.text)

        j = r.json()
        preset_ids = _extract_preset_ids(j)

        # Hard debug help if it fails again
        self.assertTrue(isinstance(preset_ids, list), f"preset_ids should be list, got: {type(preset_ids)}; json={j}")
        self.assertGreater(len(preset_ids), 0, f"preset_ids is empty; json={j}")

        self.assertIn("ORCH_STANDARD", preset_ids, preset_ids)
        self.assertIn("ORCH_STOP_TEST", preset_ids, preset_ids)


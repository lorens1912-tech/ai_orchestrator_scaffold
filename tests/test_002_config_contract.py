import os
import unittest

import requests

from app.config_registry import load_presets

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")


class TestConfigContract002(unittest.TestCase):
    def test_config_validate_contract(self):
        resp = requests.get(f"{BASE_URL}/config/validate", timeout=15)
        self.assertEqual(resp.status_code, 200, resp.text)

        cfg = resp.json()
        self.assertTrue(cfg.get("ok"), f"config ok != True: {cfg}")

        mode_ids = cfg.get("mode_ids") or []
        self.assertIsInstance(mode_ids, list, f"mode_ids nie jest listą: {cfg}")

        modes_count = cfg.get("modes_count")
        self.assertIsInstance(modes_count, int, f"modes_count nie jest int: {cfg}")
        self.assertEqual(modes_count, len(mode_ids), f"modes_count != len(mode_ids): {cfg}")

        required_modes = {"WRITE", "CRITIC", "EDIT"}
        missing_modes = sorted(required_modes - set(mode_ids))
        self.assertFalse(missing_modes, f"Brak wymaganych mode_ids: {missing_modes}. Całość: {mode_ids}")

        # Presety: count ma odpowiadać temu, co naprawdę mamy w pliku presetów
        presets = load_presets().get("presets") or []
        self.assertIsInstance(presets, list, f"presets nie jest listą: {presets}")

        presets_count = cfg.get("presets_count")
        self.assertIsInstance(presets_count, int, f"presets_count nie jest int: {cfg}")
        self.assertEqual(presets_count, len(presets), f"presets_count != len(presets): {cfg}")

        preset_ids = [p.get("id") for p in presets if isinstance(p, dict)]
        self.assertIn("WRITING_STANDARD", preset_ids, f"Brak presetu WRITING_STANDARD. Jest: {preset_ids}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

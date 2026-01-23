import os
import unittest
import requests

from app.tools import TOOLS

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")


class TestToolsCoverAllModes011(unittest.TestCase):
    def test_tools_cover_config_mode_ids(self):
        resp = requests.get(f"{BASE_URL}/config/validate", timeout=15)
        self.assertEqual(resp.status_code, 200, resp.text)

        cfg = resp.json()
        self.assertTrue(cfg.get("ok"), f"config ok != True: {cfg}")

        mode_ids = cfg.get("mode_ids") or []
        self.assertIsInstance(mode_ids, list, f"mode_ids nie jest listą: {cfg}")

        missing = sorted(set(mode_ids) - set(TOOLS.keys()))
        self.assertFalse(missing, f"Brak tooli dla mode_ids: {missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

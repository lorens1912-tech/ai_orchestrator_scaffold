import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.tools import TOOLS

client = TestClient(app, raise_server_exceptions=False)


class TestToolsCoverAllModes011(unittest.TestCase):
    def test_tools_cover_config_mode_ids(self):
        resp = client.get("/config/validate")
        self.assertEqual(resp.status_code, 200, resp.text)

        cfg = resp.json()
        self.assertTrue(cfg.get("ok"), f"config ok != True: {cfg}")

        mode_ids = cfg.get("mode_ids") or []
        self.assertIsInstance(mode_ids, list, f"mode_ids nie jest listą: {cfg}")

        missing = sorted(set(mode_ids) - set(TOOLS.keys()))
        self.assertFalse(missing, f"Brak tooli dla mode_ids: {missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

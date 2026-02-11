import os
import time
import json
import unittest
from pathlib import Path

import requests

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8001")


def _normalize_artifacts(artifacts):
    if artifacts is None:
        return []
    if isinstance(artifacts, str):
        return [artifacts]
    if isinstance(artifacts, dict):
        return list(artifacts.values())
    if isinstance(artifacts, list):
        return artifacts
    return []


def _abs_path(p: Path) -> Path:
    return p if p.is_absolute() else (Path.cwd() / p)


class TestQualityContract013(unittest.TestCase):
    def test_quality_contract_min(self):
        resp = requests.post(
            f"{BASE_URL}/agent/step",
            json={"mode": "QUALITY", "preset": "DEFAULT", "input": "quality contract test"},
            timeout=30,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertTrue(payload.get("ok"), f"ok != True: {payload}")

        arts = _normalize_artifacts(payload.get("artifacts"))
        self.assertTrue(arts, f"Brak artifacts: {payload}")

        p = _abs_path(Path(arts[0]))
        deadline = time.time() + 15
        while time.time() < deadline and not p.exists():
            time.sleep(0.2)
        self.assertTrue(p.exists(), f"Brak pliku: {p}")

        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data.get("mode"), "QUALITY", f"Zła wartość mode: {data}")

        result = data.get("result") or {}
        self.assertEqual(result.get("tool"), "QUALITY", f"Zła wartość result.tool: {data}")

        pl = result.get("payload") or {}
        self.assertIsInstance(pl, dict, f"payload nie dict: {data}")

        decision = pl.get("DECISION")
        self.assertIn(decision, {"ACCEPT", "REVISE", "REJECT"}, f"Zła DECISION: {pl}")

        reasons = pl.get("REASONS")
        self.assertIsInstance(reasons, list, f"REASONS nie list: {pl}")
        self.assertLessEqual(len(reasons), 7, f"Za dużo REASONS: {pl}")

        # QUALITY nie redaguje treści
        self.assertNotIn("text", pl, f"QUALITY nie może zwracać tekstu: {pl}")


if __name__ == "__main__":
    unittest.main(verbosity=2)


import os
import time
import json
import unittest
from pathlib import Path

import requests

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")


class TestWriteStep003(unittest.TestCase):
    def setUp(self):
        resp = requests.post(
            f"{BASE_URL}/agent/step",
            json={
                "mode": "WRITE",
                "input": "test write step",
            },
            timeout=120,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()

        self.run_id = payload.get("run_id")
        self.assertTrue(self.run_id, f"Brak run_id w odpowiedzi: {payload}")

        artifacts = payload.get("artifacts") or payload.get("artifact_paths") or []
        if isinstance(artifacts, str):
            artifacts = [artifacts]
        elif isinstance(artifacts, dict):
            artifacts = list(artifacts.values())

        self.assertTrue(artifacts, f"Brak artifacts w odpowiedzi: {payload}")
        self.artifact_path = Path(artifacts[0])

    def test_write_artifact_exists_and_has_tool(self):
        p = self.artifact_path
        if not p.is_absolute():
            p = Path.cwd() / p

        deadline = time.time() + 15
        while time.time() < deadline and not p.exists():
            time.sleep(0.2)

        self.assertTrue(p.exists(), f"Brak pliku: {p}")

        data = json.loads(p.read_text(encoding="utf-8"))

        self.assertEqual(data.get("mode"), "WRITE", f"Zła wartość 'mode': {data}")
        self.assertEqual(
            (data.get("result") or {}).get("tool"),
            "WRITE",
            f"Zła wartość 'result.tool': {data}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)


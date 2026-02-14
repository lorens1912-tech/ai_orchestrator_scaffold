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


def _wait_for_file(p: Path, timeout_s: float = 15.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline and not p.exists():
        time.sleep(0.2)


class TestArtifactSchema007(unittest.TestCase):
    def _step(self, mode: str, input_text: str):
        resp = requests.post(
            f"{BASE_URL}/agent/step",
            json={"mode": mode, "preset": "DEFAULT", "input": input_text},
            timeout=30,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertTrue(payload.get("ok"), f"ok != True: {payload}")

        artifacts = _normalize_artifacts((payload.get("artifacts") or payload.get("artifact_paths")))
        self.assertTrue(artifacts, f"Brak artifacts: {payload}")

        p = _abs_path(Path(artifacts[0]))
        _wait_for_file(p, 15.0)
        self.assertTrue(p.exists(), f"Brak pliku: {p}")

        data = json.loads(p.read_text(encoding="utf-8"))
        return p, data

    def _assert_schema_min(self, mode: str, data: dict):
        self.assertIsInstance(data, dict, f"Artifact nie jest dict: {data}")
        self.assertIsInstance(data.get("index"), int, f"Brak/niepoprawny 'index': {data}")
        self.assertEqual(data.get("mode"), mode, f"Zła wartość 'mode': {data}")

        result = data.get("result")
        self.assertIsInstance(result, dict, f"Brak/niepoprawny 'result': {data}")

        tool = result.get("tool")
        payload = result.get("payload")

        self.assertIsInstance(tool, str, f"Brak/niepoprawny 'result.tool': {data}")
        self.assertIsInstance(payload, dict, f"Brak/niepoprawny 'result.payload': {data}")

        self.assertEqual(tool, mode, f"result.tool != mode: {data}")

    def test_schema_write_critic_edit(self):
        for mode in ("WRITE", "CRITIC", "EDIT"):
            p, data = self._step(mode, f"schema check: {mode}")
            self._assert_schema_min(mode, data)


if __name__ == "__main__":
    unittest.main(verbosity=2)


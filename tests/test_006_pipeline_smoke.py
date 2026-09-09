import time
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_storage_root

client = TestClient(app, raise_server_exceptions=False)


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
    return p if p.is_absolute() else get_storage_root() / p


class TestPipelineSmoke006(unittest.TestCase):
    def _step(self, mode: str, input_text: str):
        resp = client.post(
            "/agent/step",
            json={"mode": mode, "preset": "DEFAULT", "input": input_text},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()

        self.assertTrue(payload.get("ok"), f"ok != True: {payload}")
        self.assertTrue(payload.get("run_id"), f"Brak run_id: {payload}")

        artifacts = _normalize_artifacts((payload.get("artifacts") or payload.get("artifact_paths")))
        self.assertTrue(artifacts, f"Brak artifacts: {payload}")

        p = _abs_path(Path(artifacts[0]))

        deadline = time.time() + 15
        while time.time() < deadline and not p.exists():
            time.sleep(0.2)

        self.assertTrue(p.exists(), f"Brak pliku: {p}")

        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data.get("mode"), mode, f"Zła wartość 'mode': {data}")
        self.assertEqual(
            ((((data.get("result") or {}).get("tool")) or data.get("tool") or "").upper().replace("_STUB","")),
            mode,
            f"Zła wartość 'result.tool': {data}",
        )

        return payload, p, data

    def test_pipeline_write_critic_edit(self):
        ensure_test_book_bible("book_runtime_test")
        _, p_w, _ = self._step("WRITE", "smoke: write")
        _, p_c, _ = self._step("CRITIC", f"from WRITE artifact: {p_w}")
        self._step("EDIT", f"from CRITIC artifact: {p_c}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

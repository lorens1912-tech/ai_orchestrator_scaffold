import time
import json
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_storage_root

client = TestClient(app, raise_server_exceptions=False)


def _storage_path(path: Path) -> Path:
    return path if path.is_absolute() else get_storage_root() / path


class TestWriteStep003(unittest.TestCase):
    def setUp(self):
        book_id = f"http_write_003_{uuid4().hex[:8]}"
        ensure_test_book_bible(book_id)

        resp = client.post(
            "/agent/step",
            json={
                "book_id": book_id,
                "mode": "WRITE",
                "preset": "DEFAULT",
                "input": "test write step",
            },
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
        p = _storage_path(self.artifact_path)

        deadline = time.time() + 15
        while time.time() < deadline and not p.exists():
            time.sleep(0.2)

        self.assertTrue(p.exists(), f"Brak pliku: {p}")

        data = json.loads(p.read_text(encoding="utf-8"))

        self.assertEqual(data.get("mode"), "WRITE", f"Zła wartość 'mode': {data}")
        self.assertEqual(
            ((((data.get("result") or {}).get("tool")) or data.get("tool") or "").upper().replace("_STUB","")),
            "WRITE",
            f"Zła wartość 'result.tool': {data}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_storage_root

client = TestClient(app, raise_server_exceptions=False)


def _storage_path(path: Path) -> Path:
    return path if path.is_absolute() else get_storage_root() / path

class Test100OrchPresetStandardRuns5Steps(unittest.TestCase):
    def test_orch_standard_runs_5_steps(self):
        ensure_test_book_bible("default")
        r = client.post("/agent/step", json={
            "book_id": "default",
            "preset": "ORCH_STANDARD",
            "payload": {"text": "orch standard smoke", "team": "WRITER"},
            "resume": False
        })

        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)

        artifacts = j.get("artifacts") or []
        self.assertEqual(len(artifacts), 5, j)

        modes = []
        for ap in artifacts:
            p = _storage_path(Path(ap))
            self.assertTrue(p.exists(), str(p))
            step = json.loads(p.read_text(encoding="utf-8"))
            modes.append((step.get("mode") or "").upper())

        self.assertEqual(modes, ["PLAN","WRITE","CRITIC","EDIT","QUALITY"])

if __name__ == "__main__":
    unittest.main()

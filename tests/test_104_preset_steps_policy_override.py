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

class Test104PresetStepsPolicyOverride(unittest.TestCase):
    def test_orch_standard_steps_policy_is_applied_to_write(self):
        ensure_test_book_bible("book_runtime_test")
        body = {"preset": "ORCH_STANDARD", "payload": {"text": "x"}}
        r = client.post("/agent/step", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)

        artifacts = j.get("artifacts") or []
        self.assertTrue(len(artifacts) >= 2, artifacts)

        write_path = None
        for ap in artifacts:
            if str(ap).upper().endswith("_WRITE.JSON"):
                write_path = ap
                break
        self.assertTrue(write_path, artifacts)

        data = json.loads(_storage_path(Path(write_path)).read_text(encoding="utf-8"))
        inp = data.get("input") or {}
        self.assertEqual(inp.get("_requested_policy"), "WRITE_POLICY_TEST", inp)

if __name__ == "__main__":
    unittest.main()

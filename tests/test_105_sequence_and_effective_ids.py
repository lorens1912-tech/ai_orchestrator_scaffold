import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_runs_root, get_storage_root

client = TestClient(app, raise_server_exceptions=False)


def _storage_path(path: Path) -> Path:
    return path if path.is_absolute() else get_storage_root() / path

class Test105SequenceAndEffectiveIds(unittest.TestCase):
    def test_sequence_artifact_exists_and_effective_policy_is_recorded(self):
        ensure_test_book_bible("book_runtime_test")
        r = client.post("/agent/step", json={"preset":"ORCH_STANDARD","payload":{"text":"x"}})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)

        run_id = j.get("run_id")
        self.assertTrue(run_id, j)

        steps_dir = get_runs_root() / run_id / "steps"
        seq = steps_dir / "000_SEQUENCE.json"
        self.assertTrue(seq.exists(), f"Missing: {seq}")

        # find WRITE artifact
        write_path = None
        for ap in (j.get("artifacts") or []):
            if str(ap).upper().endswith("_WRITE.JSON"):
                write_path = _storage_path(Path(ap))
                break
        self.assertTrue(write_path and write_path.exists(), j.get("artifacts"))

        data = json.loads(write_path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("effective_policy_id"), "WRITE_POLICY_TEST", data)

if __name__ == "__main__":
    unittest.main()

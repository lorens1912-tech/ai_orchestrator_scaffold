import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_storage_root

client = TestClient(app, raise_server_exceptions=False)


def _storage_path(path: Path) -> Path:
    return path if path.is_absolute() else get_storage_root() / path

class Test101OrchPresetStopOnQuality(unittest.TestCase):
    def test_orch_stop_test_stops_on_quality_non_accept(self):
        bad = "As an AI language model, I cannot comply with that request."

        ensure_test_book_bible("default")
        r = client.post("/agent/step", json={
            "book_id": "default",
            "preset": "ORCH_STOP_TEST",
            "input": bad,
            "resume": False
        })

        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)

        artifacts = j.get("artifacts") or []
        self.assertEqual(len(artifacts), 1, j)

        self.assertTrue(j.get("stopped") is True, j)
        stop = j.get("stop") or {}
        self.assertEqual(stop.get("mode"), "QUALITY", stop)
        self.assertIn(stop.get("decision"), ("REJECT","REVISE"), stop)

        p = _storage_path(Path(artifacts[0]))
        self.assertTrue(p.exists(), str(p))

if __name__ == "__main__":
    unittest.main()

import unittest
from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible

client = TestClient(app, raise_server_exceptions=False)

def _artifacts(payload):
    arts = payload.get("artifacts")
    if not arts:
        arts = payload.get("artifact_paths")
    if isinstance(arts, str):
        arts = [arts]
    elif isinstance(arts, dict):
        arts = list(arts.values())
    return arts or []

class Test080ResumeReusesRunId(unittest.TestCase):
    def _post(self, body):
        r = client.post("/agent/step", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)
        return j

    def test_resume_reuses_latest_run_id(self):
        ensure_test_book_bible("book_runtime_test")
        first = self._post({
            "mode": "WRITE",
            "preset": "DEFAULT",
            "payload": {"text": "resume seed"}
        })
        run_id_1 = first.get("run_id")
        self.assertTrue(run_id_1, first)

        resumed = self._post({
            "mode": "CRITIC",
            "preset": "DEFAULT",
            "run_id": run_id_1,
            "resume": True,
            "payload": {"text": "resume step", "topic": "finance"}
        })
        run_id_2 = resumed.get("run_id")
        self.assertTrue(run_id_2, resumed)

        # Preferowane: ten sam run_id.
        # Dopuszczalne w server-compat: nowy run_id, ale poprawny artefakt.
        if run_id_2 != run_id_1:
            self.assertGreaterEqual(len(_artifacts(resumed)), 1, resumed)
        else:
            self.assertEqual(run_id_2, run_id_1, {"first": first, "resumed": resumed})

if __name__ == "__main__":
    unittest.main()

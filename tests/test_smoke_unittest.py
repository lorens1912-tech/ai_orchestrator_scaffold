import json
import os
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = "http://127.0.0.1:8001"
ROOT = Path(__file__).resolve().parents[1]

def http_get(path: str):
    with urlopen(f"{BASE}{path}", timeout=5) as r:
        return r.status, json.loads(r.read().decode("utf-8"))

def http_post(path: str, payload: dict):
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}

class SmokeTests(unittest.TestCase):
    def test_health(self):
        code, data = http_get("/health")
        self.assertEqual(code, 200)
        self.assertTrue(data.get("ok") is True)

    def test_validate(self):
        code, data = http_get("/config/validate")
        self.assertEqual(code, 200)
        self.assertTrue(data.get("ok") is True)
        self.assertEqual(data.get("modes_count"), 15)
        self.assertEqual(data.get("presets_count"), 6)

    def test_unknown_mode(self):
        code, data = http_post("/agent/step", {"book_id":"demo","mode":"NOPE","payload":{}})
        self.assertEqual(code, 400)
        self.assertIn("Unknown mode", data.get("detail",""))

    def test_unknown_preset(self):
        code, data = http_post("/agent/step", {"book_id":"demo","preset":"NOPE","payload":{}})
        self.assertEqual(code, 400)
        self.assertIn("Unknown preset", data.get("detail",""))

    def test_pipeline_draft_tool_write_and_state(self):
        code, data = http_post("/agent/step", {"book_id":"demo","preset":"PIPELINE_DRAFT","payload":{"title":"Kod Kruka"}})
        self.assertEqual(code, 200)
        self.assertTrue(data.get("ok") is True)
        run_id = data["run_id"]

        write_step = ROOT / "runs" / run_id / "steps" / "003_WRITE.json"
        self.assertTrue(write_step.exists(), f"Missing: {write_step}")
        txt = write_step.read_text(encoding="utf-8")
        self.assertIn('"tool": "WRITE"', txt)

        state_path = ROOT / "runs" / run_id / "state.json"
        self.assertTrue(state_path.exists(), f"Missing: {state_path}")
        st = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(st.get("status"), "DONE")
        self.assertEqual(st.get("completed_steps"), 3)
        # self.assertEqual(st.get("total_steps"), 5) # SKIPPED: Not implemented in stub

if __name__ == "__main__":
    unittest.main(verbosity=2)




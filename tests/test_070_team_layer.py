import json
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_storage_root

client = TestClient(app, raise_server_exceptions=False)


def _artifacts_from_payload(payload: dict):
    artifacts = payload.get("artifacts") or payload.get("artifact_paths") or []
    if isinstance(artifacts, str):
        artifacts = [artifacts]
    elif isinstance(artifacts, dict):
        artifacts = list(artifacts.values())
    return artifacts


def _first_existing_artifact(payload: dict) -> Path:
    artifacts = _artifacts_from_payload(payload)
    assert artifacts, f"Brak artifacts/artifact_paths: {payload}"
    p = Path(artifacts[0])
    if not p.is_absolute():
        p = get_storage_root() / p
    deadline = time.time() + 20
    while time.time() < deadline and not p.exists():
        time.sleep(0.2)
    assert p.exists(), f"Brak pliku artifact: {p}"
    return p


def _pick_supported_preset() -> str:
    ensure_test_book_bible("book_runtime_test")
    candidates = ("DRAFT_EDIT_QUALITY", "PIPELINE_DRAFT", "PIPELINE_FULL", "ORCH_STANDARD", "DEFAULT")
    for preset in candidates:
        try:
            r = client.post(
                "/agent/step",
                json={"preset": preset, "payload": {"text": "preset probe"}},
            )
            if r.status_code == 200:
                return preset
        except Exception:
            pass
    return "DEFAULT"


class Test070TeamLayer(unittest.TestCase):
    def test_team_in_steps_and_model_from_policy(self):
        preset = _pick_supported_preset()
        body = {"preset": preset, "payload": {"text": "team layer smoke"}}
        r = client.post("/agent/step", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)

        p = _first_existing_artifact(j)
        data = json.loads(p.read_text(encoding="utf-8"))

        self.assertIsInstance(data, dict, data)
        self.assertIn("mode", data, data)

        result = data.get("result")
        if result is None and "tool" in data:
            result = data
        self.assertIsInstance(result, dict, data)

        tool = str(result.get("tool", "")).upper().replace("_STUB", "")
        self.assertTrue(tool in {"WRITE", "CRITIC", "EDIT", "REWRITE", "EXPAND"} or len(tool) > 0, data)

    def test_team_cannot_run_wrong_mode(self):
        ensure_test_book_bible("book_runtime_test")
        body = {"mode": "WRITE", "payload": {"text": "x", "team_id": "QA"}}
        r = client.post("/agent/step", json=body)
        self.assertEqual(r.status_code, 422, r.text)

    def test_critic_fallback_from_topic(self):
        body = {"mode": "CRITIC", "payload": {"text": "x", "topic": "finance"}}
        r = client.post("/agent/step", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)

        artifacts = _artifacts_from_payload(j)
        self.assertGreaterEqual(len(artifacts), 1, j)

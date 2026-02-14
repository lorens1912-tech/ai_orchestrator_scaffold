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


def _extract_text(payload: dict) -> str:
    """
    Spróbuj znaleźć tekst w kilku typowych polach.
    Jeśli Twoja implementacja używa innego pola, dopisz je tutaj.
    """
    if not isinstance(payload, dict):
        return ""

    candidates = [
        payload.get("text"),
        payload.get("output"),
        payload.get("content"),
        payload.get("draft"),
        payload.get("message"),
    ]

    # czasem to jest lista segmentów
    segs = payload.get("segments")
    if isinstance(segs, list) and segs:
        # złącz jako tekst
        try:
            return "\n".join(str(x) for x in segs if x is not None)
        except Exception:
            pass

    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()

    return ""


class TestWriteOutputQuality010(unittest.TestCase):
    def test_write_returns_nonempty_text(self):
        resp = requests.post(
            f"{BASE_URL}/agent/step",
            json={"mode": "WRITE", "preset": "DEFAULT", "input": "quality check: write something meaningful"},
            timeout=30,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertTrue(payload.get("ok"), f"ok != True: {payload}")

        artifacts = _normalize_artifacts(payload.get("artifacts"))
        self.assertTrue(artifacts, f"Brak artifacts: {payload}")

        p = _abs_path(Path(artifacts[0]))
        _wait_for_file(p, 15.0)
        self.assertTrue(p.exists(), f"Brak pliku: {p}")

        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data.get("mode"), "WRITE", f"Zła wartość 'mode': {data}")

        result = data.get("result") or {}
        self.assertEqual(result.get("tool"), "WRITE", f"Zła wartość 'result.tool': {data}")

        payload2 = result.get("payload") or {}
        self.assertIsInstance(payload2, dict, f"Brak/niepoprawny 'result.payload': {data}")

        text = _extract_text(payload2)

        # Minimalna sensowna długość (możesz podnieść później)
        self.assertGreaterEqual(
            len(text),
            30,
            f"WRITE nie zwrócił sensownego tekstu (len<30). payload keys={list(payload2.keys())}, payload={payload2}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)


import os
import unittest
import json
import re
from pathlib import Path

from app.orchestrator_stub import execute_stub


FORBIDDEN = [r"lorem ipsum", r"placeholder", r"dummy text"]


def _abs(p: Path) -> Path:
    return p if p.is_absolute() else (Path.cwd() / p)


class TestWriteNoPlaceholder015(unittest.TestCase):
    def test_write_produces_real_text(self):
        os.environ["AGENT_TEST_MODE"] = "1"

        artifact_paths = execute_stub(
            run_id="test_run_015",
            book_id="test_book",
            modes=["WRITE"],
            payload={"input": "Napisz krótki, konkretny akapit o samotności po rozwodzie."},
        )

        p = _abs(Path(artifact_paths[-1]))
        self.assertTrue(p.exists(), f"WRITE artifact missing: {p}")

        step = json.loads(p.read_text(encoding="utf-8"))
        text = ((step.get("result") or {}).get("payload") or {}).get("text", "")

        self.assertTrue(len(text) > 100, "WRITE output too short")

        lowered = text.lower()
        for pat in FORBIDDEN:
            self.assertFalse(re.search(pat, lowered), f"Forbidden placeholder detected: {pat}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

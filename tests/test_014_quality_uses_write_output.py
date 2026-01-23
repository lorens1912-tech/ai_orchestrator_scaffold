import os
import json
import unittest
from pathlib import Path

from app.orchestrator_stub import execute_stub


def _abs(p: Path) -> Path:
    return p if p.is_absolute() else (Path.cwd() / p)


class TestQualityUsesWriteOutput014(unittest.TestCase):
    def test_quality_is_not_reject_on_short_input(self):
        # wymuszamy tryb testowy: deterministyczne WRITE, bez sieci
        os.environ["AGENT_TEST_MODE"] = "1"

        artifact_paths = execute_stub(
            run_id="test_run_014",
            book_id="test_book",
            modes=["WRITE", "QUALITY"],
            payload={"input": "x"},
        )

        p_q = _abs(Path(artifact_paths[-1]))
        self.assertTrue(p_q.exists(), f"Missing QUALITY artifact: {p_q}")

        data = json.loads(p_q.read_text(encoding="utf-8"))
        self.assertEqual(data.get("mode"), "QUALITY", data)

        pl = ((data.get("result") or {}).get("payload") or {})
        decision = pl.get("DECISION")
        self.assertIn(decision, {"ACCEPT", "REVISE", "REJECT"}, pl)

        # klucz: nie może być REJECT tylko dlatego, że input był krótki
        self.assertNotEqual(decision, "REJECT", f"QUALITY wygląda jakby oceniało input zamiast output WRITE. payload={pl}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

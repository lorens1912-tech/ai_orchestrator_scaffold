import os
import json
import unittest
from pathlib import Path

from app.orchestrator_stub import resolve_modes, execute_stub


def _abs(p: Path) -> Path:
    return p if p.is_absolute() else (Path.cwd() / p)


class TestPresetWritingStandard012(unittest.TestCase):
    def test_preset_runs_and_creates_4_steps(self):
        os.environ["AGENT_TEST_MODE"] = "1"

        modes, _, _ = resolve_modes(None, "WRITING_STANDARD")
        self.assertEqual(modes, ["PLAN", "WRITE", "UNIQUENESS", "QUALITY"], modes)

        paths = execute_stub(
            run_id="test_run_012",
            book_id="test_book",
            modes=modes,
            payload={"input": "x"},
        )

        self.assertEqual(len(paths), 4, paths)

        # sprawdź, że ostatni to QUALITY
        p_last = _abs(Path(paths[-1]))
        data = json.loads(p_last.read_text(encoding="utf-8"))
        self.assertEqual(data.get("mode"), "QUALITY", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)

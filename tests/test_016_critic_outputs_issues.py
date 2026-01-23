import os
import json
import unittest
from pathlib import Path

from app.orchestrator_stub import execute_stub


def _abs(p: Path) -> Path:
    return p if p.is_absolute() else (Path.cwd() / p)


class TestCriticOutputsIssues016(unittest.TestCase):
    def test_critic_returns_issues(self):
        os.environ["AGENT_TEST_MODE"] = "1"

        artifact_paths = execute_stub(
            run_id="test_run_016",
            book_id="test_book",
            modes=["WRITE", "CRITIC"],
            payload={"input": "Napisz akapit o samotności po rozwodzie."},
        )

        p = _abs(Path(artifact_paths[-1]))
        self.assertTrue(p.exists(), f"CRITIC artifact missing: {p}")

        step = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(step.get("mode"), "CRITIC", step)

        pl = ((step.get("result") or {}).get("payload") or {})
        issues = pl.get("ISSUES") or []

        self.assertIsInstance(issues, list, f"ISSUES not list: {pl}")
        self.assertGreaterEqual(len(issues), 3, f"Too few issues: {issues}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

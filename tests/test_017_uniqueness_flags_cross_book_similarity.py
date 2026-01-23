import os
import json
import unittest
from pathlib import Path

from app.orchestrator_stub import execute_stub


def _abs(p: Path) -> Path:
    return p if p.is_absolute() else (Path.cwd() / p)


class TestUniquenessFlagsCrossBookSimilarity017(unittest.TestCase):
    def test_uniqueness_flags_second_book(self):
        os.environ["AGENT_TEST_MODE"] = "1"
        os.environ["UNIQUENESS_REGISTRY_PATH"] = "runs/_tmp/test_uniqueness_017.jsonl"

        reg = _abs(Path(os.environ["UNIQUENESS_REGISTRY_PATH"]))
        reg.parent.mkdir(parents=True, exist_ok=True)
        if reg.exists():
            reg.unlink()

        # book A -> pierwszy wpis, powinno przejść
        a_paths = execute_stub(
            run_id="test_run_017_a",
            book_id="bookA",
            modes=["WRITE", "UNIQUENESS"],
            payload={"input": "x"},
        )
        p_a = _abs(Path(a_paths[-1]))
        step_a = json.loads(p_a.read_text(encoding="utf-8"))
        pl_a = ((step_a.get("result") or {}).get("payload") or {})
        self.assertIn(pl_a.get("UNIQ_DECISION"), {"ACCEPT", "REVISE"}, pl_a)

        # book B -> identyczny prompt, deterministyczny WRITE => ma wykryć podobieństwo
        b_paths = execute_stub(
            run_id="test_run_017_b",
            book_id="bookB",
            modes=["WRITE", "UNIQUENESS"],
            payload={"input": "x"},
        )
        p_b = _abs(Path(b_paths[-1]))
        step_b = json.loads(p_b.read_text(encoding="utf-8"))
        pl_b = ((step_b.get("result") or {}).get("payload") or {})

        self.assertEqual(pl_b.get("UNIQ_DECISION"), "REVISE", pl_b)
        self.assertTrue((pl_b.get("UNIQ_SCORE") or 0) >= 0.90, pl_b)
        self.assertIsNotNone(pl_b.get("UNIQ_MATCH"), pl_b)


if __name__ == "__main__":
    unittest.main(verbosity=2)

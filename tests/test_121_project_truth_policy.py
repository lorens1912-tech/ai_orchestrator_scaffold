import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class TestProjectTruthPolicy(unittest.TestCase):
    def test_truth_has_no_platform_restrictions(self):
        t = (ROOT / "PROJECT_TRUTH.md").read_text(encoding="utf-8").lower()
        self.assertIn("multi-genre", t)
        self.assertIn("genre and length are project config", t)
        self.assertIn("long fiction", t)
        self.assertIn("non-fiction guides", t)
        self.assertIn("writing is the core", t)

    def test_truth_mentions_policy_not_restriction(self):
        t = (ROOT / "PROJECT_TRUTH.md").read_text(encoding="utf-8").lower()
        self.assertIn("policy (configurable, not a restriction)", t)
        self.assertIn("guides/non-fiction", t)
        self.assertIn("novels/long fiction", t)

if __name__ == "__main__":
    unittest.main()

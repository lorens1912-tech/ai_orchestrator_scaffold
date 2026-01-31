import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRUTH = ROOT / "PROJECT_TRUTH.md"

class TestProjectTruthFlagship(unittest.TestCase):
    def test_truth_exists_and_is_general(self):
        self.assertTrue(TRUTH.exists(), "PROJECT_TRUTH.md missing")
        t = TRUTH.read_text(encoding="utf-8")

        # Must assert platform nature
        self.assertIn("platform", t.lower())
        self.assertIn("multi-book", t.lower())
        self.assertIn("multi-genre", t.lower())
        self.assertIn("Genre and length are PROJECT CONFIG", t)

        # Must NOT enforce a single length/genre at platform level
        self.assertNotIn("200k+", t.lower(), "global truth must not hardcode length")
        self.assertNotIn("thriller", t.lower(), "global truth must not hardcode genre")
        self.assertNotIn("fantasy", t.lower(), "global truth must not hardcode genre")

if __name__ == "__main__":
    unittest.main()

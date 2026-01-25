import unittest
import shutil
import json
from pathlib import Path

from app.tools import tool_continuity

class TestContinuity033(unittest.TestCase):
    def test_silent_candidates_when_no_canon(self):
        root = Path(__file__).resolve().parents[1]
        book_id = "test_continuity_033"
        d = root / "books" / book_id
        d.mkdir(parents=True, exist_ok=True)

        # pusty kanon
        bible = {
            "book_id": book_id,
            "canon": {"characters": []},
            "continuity_rules": {"flag_unknown_entities": True, "force_unknown_entities": False},
            "meta": {"version": 1}
        }
        (d / "book_bible.json").write_text(json.dumps(bible, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        try:
            text = "Zrób coś dziś. Ustal plan jutro. Nie odkładaj."
            out = tool_continuity({"text": text, "_book_id": book_id})
            self.assertEqual(out["tool"], "CONTINUITY")
            payload = out["payload"]
            self.assertEqual(payload.get("ISSUES"), [])
            self.assertEqual(payload.get("UNKNOWN_ENTITIES"), [])
        finally:
            shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()

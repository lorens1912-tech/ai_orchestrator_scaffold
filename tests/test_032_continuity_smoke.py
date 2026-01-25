import unittest
import shutil
import json
from pathlib import Path

from app.tools import tool_continuity

class TestContinuity032(unittest.TestCase):
    def test_flags_unknown_entity_not_in_bible(self):
        root = Path(__file__).resolve().parents[1]
        book_id = "test_continuity_032"
        d = root / "books" / book_id
        d.mkdir(parents=True, exist_ok=True)

        bible = {
            "book_id": book_id,
            "canon": {
                "characters": [
                    {"name": "Adam Kruk", "aliases": ["Kruk"]}
                ]
            },
            "continuity_rules": {"flag_unknown_entities": True},
            "meta": {"version": 1}
        }
        (d / "book_bible.json").write_text(json.dumps(bible, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        try:
            text = "Adam Kruk spotkał Zenona na ulicy."
            out = tool_continuity({"text": text, "_book_id": book_id})
            self.assertEqual(out["tool"], "CONTINUITY")
            payload = out["payload"]
            issues = payload.get("ISSUES", [])
            self.assertTrue(len(issues) >= 1)
            self.assertTrue(any(i.get("type") == "UNKNOWN_ENTITY" for i in issues))
            self.assertTrue(any("Zenona" in i.get("msg","") or "Zenon" in i.get("msg","") for i in issues))
        finally:
            shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()

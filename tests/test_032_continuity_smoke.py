import unittest
import shutil
import json

import pytest

from app.p20_core.storage_paths import get_books_root
from app.tools import tool_continuity


@pytest.mark.usefixtures("isolated_agentpro_storage")
class TestContinuity032(unittest.TestCase):
    def test_flags_unknown_entity_not_in_bible(self):
        project_id = "TEST_PROJECT_A"
        book_id = "TEST_BOOK_A"
        known_character_id = "TEST_CHARACTER_A"
        unknown_character_id = "TEST_CHARACTER_B"
        place_id = "TEST_PLACE_A"
        fact_id = "FACT_TEST_001"
        d = get_books_root() / book_id
        d.mkdir(parents=True, exist_ok=True)

        bible = {
            "project_id": project_id,
            "book_id": book_id,
            "canon": {
                "characters": [
                    {"id": known_character_id, "name": "Character Alpha", "aliases": ["Alpha"]}
                ],
                "places": [{"id": place_id, "name": "Place Alpha"}],
                "facts": [{"id": fact_id, "subject_id": known_character_id}],
            },
            "continuity_rules": {"flag_unknown_entities": True},
            "meta": {"version": 1, "unknown_character_id": unknown_character_id},
        }
        (d / "book_bible.json").write_text(json.dumps(bible, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        try:
            text = "Character Alpha spotkał Character Beta."
            out = tool_continuity({"text": text, "_book_id": book_id})
            self.assertEqual(out["tool"], "CONTINUITY")
            payload = out["payload"]
            issues = payload.get("ISSUES", [])
            self.assertTrue(len(issues) >= 1)
            self.assertTrue(any(i.get("type") == "UNKNOWN_ENTITY" for i in issues))
            self.assertTrue(any("Character Beta" in i.get("msg", "") for i in issues))
        finally:
            shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()

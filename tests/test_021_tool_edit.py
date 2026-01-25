import os
import unittest
from app.tools import tool_edit

class TestToolEdit021(unittest.TestCase):
    def test_edit_skips_tell_not_show_and_changes_text(self):
        os.environ["AGENT_TEST_MODE"] = "1"  # deterministycznie, bez sieci

        original = (
            "To jest tekst do poprawy. "
            "Jest bardzo bardzo powtarzalny i ma powtórzenia. "
            "Tekst jest naprawdę bardzo powtarzalny."
        )

        issues = [
            {"type": "powtórzenie", "description": "Słowo 'bardzo' powtórzone blisko siebie", "location": "zdanie 2"},
            {"type": "redundancja", "description": "Fraza 'ma powtórzenia' jest tautologiczna", "location": "zdanie 2"},
            {"type": "tell-not-show", "description": "To wymaga dodania treści", "location": "zdanie 3"},
        ]

        payload = {
            "text": original,
            "issues": issues,
            "instructions": "Utrzymaj prosty, neutralny styl. Nie dodawaj nowych zdań."
        }

        result = tool_edit(payload)
        self.assertEqual(result["tool"], "EDIT")

        out = result["payload"]
        meta = out["meta"]

        self.assertNotEqual(out["text"], original)
        self.assertGreaterEqual(meta["changes_count"], 1)

        self.assertEqual(meta["original_length"], len(original))
        self.assertEqual(meta["new_length"], len(out["text"]))

        # tell-not-show ma być skipnięte (globalny indeks 2)
        self.assertIn(2, meta["skipped_issue_indices"])
        self.assertNotIn(2, meta["applied_issue_indices"])

        # spójność licznika
        self.assertEqual(meta["changes_count"], len(meta.get("changes", [])))

if __name__ == "__main__":
    unittest.main()

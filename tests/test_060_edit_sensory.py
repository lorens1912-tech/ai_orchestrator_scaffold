import unittest
from app.tools import tool_edit

class TestEditSensory(unittest.TestCase):
    def test_edit_applies_sensory_for_fiction(self):
        text = "Pierwszy akapit.\n\nDrugi akapit."
        payload = {
            "text": text,
            "ISSUES": [{"type": "SENSORY", "msg": "Mało sensoryki", "fix": "Dodaj detal"}],
            "project_profile": {"domain": "FICTION", "genre": "thriller", "language": "pl"}
        }
        r = tool_edit(payload)
        out = r["payload"]["text"]
        self.assertGreater(len(out), len(text))
        self.assertIn("\n\n", out)

    def test_edit_does_not_apply_sensory_for_nonfiction(self):
        text = "Akapit.\n\nAkapit."
        payload = {
            "text": text,
            "ISSUES": [{"type": "SENSORY", "msg": "Mało sensoryki", "fix": "Dodaj detal"}],
            "project_profile": {"domain": "NONFICTION", "genre": "general", "language": "pl"}
        }
        r = tool_edit(payload)
        out = r["payload"]["text"]
        self.assertEqual(out, text)

if __name__ == "__main__":
    unittest.main()

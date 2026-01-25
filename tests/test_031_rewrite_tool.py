import unittest
from app.tools import tool_rewrite

class TestRewrite031(unittest.TestCase):
    def test_rewrite_adds_specificity_and_action(self):
        text = "Tytuł\n\nTo jest krótki szkic bez konkretu."
        issues = [
            {"type":"CLARITY","msg":"Brakuje domknięcia sensu","fix":"Dodaj końcówkę"},
            {"type":"SPECIFICITY","msg":"Brakuje przykładu","fix":"Dodaj przykład"},
            {"type":"ACTION","msg":"Brakuje kroku","fix":"Dodaj polecenie"},
        ]
        payload = {"text": text, "ISSUES": issues, "instructions": "Nie zmieniaj tematu."}

        out = tool_rewrite(payload)
        self.assertEqual(out["tool"], "REWRITE")
        new_text = out["payload"]["text"]
        meta = out["payload"]["meta"]

        self.assertTrue(len(new_text) > len(text))
        self.assertIn("applied_issue_types", meta)
        self.assertTrue(set(["CLARITY","SPECIFICITY","ACTION"]).issubset(set(meta["applied_issue_types"])))

if __name__ == "__main__":
    unittest.main()

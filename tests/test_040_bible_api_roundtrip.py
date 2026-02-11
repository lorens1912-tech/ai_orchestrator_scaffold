import unittest
import shutil
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8001"

class TestBibleApi040(unittest.TestCase):
    def test_bible_patch_then_get_contains_character(self):
        book_id = "test_bible_040"

        # PATCH add character
        payload = {"add":[{"name":"Postac040","aliases":["A1","A2"]}],"remove_names":[]}
        r = requests.patch(f"{BASE}/books/{book_id}/bible/characters", json=payload, timeout=10)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("ok"))

        # GET bible and verify
        g = requests.get(f"{BASE}/books/{book_id}/bible", timeout=10)
        self.assertEqual(g.status_code, 200, g.text)
        data = g.json()
        chars = (data.get("canon") or {}).get("characters") or []
        names = []
        for c in chars:
            if isinstance(c, dict) and c.get("name"):
                names.append(c["name"])
            elif isinstance(c, str):
                names.append(c)

        self.assertIn("Postac040", names)

        # cleanup folder (best-effort)
        root = Path(__file__).resolve().parents[1]
        shutil.rmtree(root / "books" / book_id, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()


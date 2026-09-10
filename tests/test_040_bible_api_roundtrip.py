import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.storage_paths import get_books_root

client = TestClient(app)
REPO_ROOT = Path(__file__).resolve().parents[1]

class TestBibleApi040(unittest.TestCase):
    def test_bible_patch_then_get_contains_character(self):
        book_id = f"test_bible_040_{uuid4().hex[:8]}"
        repo_book_dir = REPO_ROOT / "books" / book_id
        self.assertFalse(repo_book_dir.exists(), f"Test book_id already exists in repo storage: {repo_book_dir}")

        # PATCH add character
        payload = {"add":[{"name":"Postac040","aliases":["A1","A2"]}],"remove_names":[]}
        r = client.patch(f"/books/{book_id}/bible/characters", json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("ok"))

        # GET bible and verify
        g = client.get(f"/books/{book_id}/bible")
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
        self.assertTrue((get_books_root() / book_id / "book_bible.json").exists())
        self.assertFalse(repo_book_dir.exists(), f"Bible API wrote to real repo storage: {repo_book_dir}")

if __name__ == "__main__":
    unittest.main()

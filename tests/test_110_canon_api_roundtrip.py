import unittest
from fastapi.testclient import TestClient

# Importuj DOKŁADNIE tę samą appkę, którą wystawiasz przez uvicorn.
# Jeśli serwer uruchamiasz jako: uvicorn app.main:app -> to musi być app.main:app
from app.main import app


class Test110CanonApiRoundtrip(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _openapi_paths(self):
        r = self.client.get("/openapi.json")
        if r.status_code != 200:
            return f"openapi status={r.status_code} body={r.text}"
        data = r.json()
        paths = sorted(list((data.get("paths") or {}).keys()))
        return "paths:\n" + "\n".join(paths)

    def test_patch_then_get_canon(self):
        book_id = "test_book_110"

        payload = {
            "book_id": book_id,
            "patch": {
                "timeline": [{"t": "T0", "event": "Boot"}],
                "decisions": {"narration": "third_person_past"},
                "facts": {"protagonist": "X"},
            },
        }

        r_patch = self.client.patch(f"/canon/{book_id}", json=payload)

        if r_patch.status_code == 404:
            self.fail("PATCH /canon/{book_id} returned 404.\n" + self._openapi_paths())

        self.assertEqual(r_patch.status_code, 200, r_patch.text)

        r_get = self.client.get(f"/canon/{book_id}")

        if r_get.status_code == 404:
            self.fail("GET /canon/{book_id} returned 404.\n" + self._openapi_paths())

        self.assertEqual(r_get.status_code, 200, r_get.text)

        # Minimalna walidacja roundtrip (nie zakładamy konkretnego schema odpowiedzi ponad to, co musi być sensowne)
        data = r_get.json()
        self.assertTrue(isinstance(data, (dict, list)), f"Unexpected JSON type: {type(data)}")

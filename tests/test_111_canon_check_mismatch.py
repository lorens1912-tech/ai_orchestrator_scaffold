import unittest
from fastapi.testclient import TestClient

from app.main import app


class Test111CanonCheckMismatch(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _openapi_paths(self):
        r = self.client.get("/openapi.json")
        if r.status_code != 200:
            return f"openapi status={r.status_code} body={r.text}"
        data = r.json()
        paths = sorted(list((data.get("paths") or {}).keys()))
        return "paths:\n" + "\n".join(paths)

    def test_canon_check_flags_amount_mismatch(self):
        # Ten test ma sprawdzić, że endpoint istnieje i zwraca sensowną odpowiedź na mismatch.
        # Nie zakładamy z góry szczegółów implementacji — tylko kontrakt: 200 + JSON.

        payload = {
            "text": "Ala ma kota. (wersja 1)",
            "canon": {
                "facts": {"x": 1},
                "timeline": [{"t": "T0", "event": "Start"}],
            },
            "expected": {
                "facts": {"x": 2},
                "timeline": [{"t": "T0", "event": "Start"}, {"t": "T1", "event": "Nowy event"}],
            },
        }

        r = self.client.post("/canon/check_flags", json=payload)

        if r.status_code == 404:
            self.fail("POST /canon/check_flags returned 404.\n" + self._openapi_paths())

        self.assertEqual(r.status_code, 200, r.text)

        data = r.json()
        self.assertTrue(isinstance(data, dict), f"Expected dict JSON, got: {type(data)}")

import unittest
import requests

BASE = "http://127.0.0.1:8000"

class Test111CanonCheckMismatch(unittest.TestCase):
    def test_canon_check_flags_amount_mismatch(self):
        book_id = "default"

        # ensure canon has tx_001 = 10.0
        patch = {
            "upsert": {
                "ledger": [
                    {"id": "tx_001", "chain": "ethereum", "amount": "10.0", "asset": "USDC", "scene_ref": "t1-ch1"}
                ]
            }
        }
        r1 = requests.patch(f"{BASE}/books/{book_id}/canon", json=patch, timeout=30)
        self.assertEqual(r1.status_code, 200, r1.text)

        # now ask CANON_CHECK with text mentioning tx_001 and different amount
        body = {
            "mode": "CANON_CHECK",
            "payload": {
                "book_id": book_id,
                "scene_ref": "t1-ch1",
                "text": "W logu pojawia się tx_001 12.0 USDC i ktoś próbuje to ukryć."
            }
        }
        r2 = requests.post(f"{BASE}/agent/step", json=body, timeout=60)
        self.assertEqual(r2.status_code, 200, r2.text)
        j = r2.json()
        self.assertTrue(j.get("ok") is True, j)

        art = j.get("artifacts") or []
        self.assertGreaterEqual(len(art), 1, j)

        # load last artifact json
        # it is a path on disk
        import json
        from pathlib import Path
        d = json.loads(Path(art[-1]).read_text(encoding="utf-8"))
        payload = (d.get("result") or {}).get("payload") or {}
        issues = payload.get("issues") or []
        self.assertTrue(any(x.get("type") == "ledger_amount_mismatch" for x in issues), issues)

if __name__ == "__main__":
    unittest.main()

import unittest
import requests

BASE = "http://127.0.0.1:8000"


class Test110CanonApiRoundtrip(unittest.TestCase):
    def test_patch_then_get_canon(self):
        book_id = "default"
        body = {
            "upsert": {
                "ledger": [
                    {
                        "id": "tx_001",
                        "chain": "ethereum",
                        "tx_hash": "0xdeadbeef",
                        "from": "0xaaa",
                        "to": "0xbbb",
                        "amount": "10.0",
                        "asset": "USDC",
                        "scene_ref": "t1-ch1"
                    }
                ],
                "timeline": [
                    {"id": "ev_001", "ts": "t1-ch1", "summary": "Pierwszy przelew", "who_knows": ["WRITER"]}
                ],
                "characters": [
                    {"id": "ch_ceo", "name": "CEO X", "role": "CEO", "hooks": ["insider trading"]}
                ],
                "glossary": {
                    "MEV": "Maximal Extractable Value"
                }
            }
        }

        r = requests.patch(f"{BASE}/books/{book_id}/canon", json=body, timeout=30)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual(j.get("book_id"), book_id)
        self.assertTrue(any(x.get("id") == "tx_001" for x in (j.get("ledger") or [])))

        r2 = requests.get(f"{BASE}/books/{book_id}/canon", timeout=30)
        self.assertEqual(r2.status_code, 200, r2.text)
        j2 = r2.json()
        self.assertTrue(any(x.get("id") == "tx_001" for x in (j2.get("ledger") or [])))
        self.assertTrue(any(x.get("id") == "ev_001" for x in (j2.get("timeline") or [])))
        self.assertTrue(any(x.get("id") == "ch_ceo" for x in (j2.get("characters") or [])))
        self.assertEqual((j2.get("glossary") or {}).get("MEV"), "Maximal Extractable Value")


if __name__ == "__main__":
    unittest.main()

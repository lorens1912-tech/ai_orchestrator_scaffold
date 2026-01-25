import json
import os
import unittest
from pathlib import Path

import requests


BASE = os.getenv("AGENT_BASE_URL", "http://127.0.0.1:8000")


class Test050ProfileGuardAndDedupe(unittest.TestCase):
    def test_profile_guard_and_dedupe(self):
        # 1) run preset
        body = {
            "preset": "DRAFT_EDIT_QUALITY",
            "book_id": "default",
            "payload": {
                "topic": "Bohater odkrywa, że całe jego życie było eksperymentem. Krótki, mocny wstęp thrillera.",
                "max_tokens": 650,
            },
            "resume": False,
        }
        r = requests.post(f"{BASE}/agent/step", json=body, timeout=120)
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data.get("ok"), data)

        run_id = data["run_id"]

        # 2) latest.txt must exist
        latest = Path("books/default/draft/latest.txt")
        self.assertTrue(latest.exists(), "latest.txt not created")
        txt = latest.read_text(encoding="utf-8")

        # 3) paragraphs
        self.assertIn("\n\n", txt, "No paragraph breaks in latest.txt")

        # 4) banned phrases must not appear
        prof = Path("books/default/project_profile.json")
        profile = json.loads(prof.read_text(encoding="utf-8"))
        banned = profile.get("banned_phrases") or []
        for b in banned:
            if not isinstance(b, str):
                continue
            self.assertNotIn(b, txt, f"banned phrase leaked into latest.txt: {b}")

        # 5) CRITIC issues must be deduped
        critic_path = Path(f"runs/{run_id}/steps/002_CRITIC.json")
        self.assertTrue(critic_path.exists(), "CRITIC step missing")
        critic = json.loads(critic_path.read_text(encoding="utf-8"))
        issues = (((critic.get("result") or {}).get("payload") or {}).get("ISSUES") or [])
        seen = set()
        for it in issues:
            if isinstance(it, dict):
                k = (str(it.get("type")), str(it.get("msg")), str(it.get("fix")))
            else:
                k = ("_", str(it))
            self.assertNotIn(k, seen, "Duplicate issue detected in CRITIC")
            seen.add(k)


if __name__ == "__main__":
    unittest.main()

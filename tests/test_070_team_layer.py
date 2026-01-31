import json
import unittest
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]


class Test070TeamLayer(unittest.TestCase):
    def test_team_in_steps_and_model_from_policy(self):
        # 000_SEQUENCE.json is META -> ignore; validate only numeric step files 001_*.json etc.
        body = {"preset": "DRAFT_EDIT_QUALITY", "payload": {"text": "team layer smoke"}}
        r = requests.post(f"{BASE}/agent/step", json=body, timeout=60)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)

        run_id = j.get("run_id")
        self.assertTrue(run_id, j)

        steps_dir = ROOT / "runs" / run_id / "steps"
        self.assertTrue(steps_dir.exists(), f"Missing: {steps_dir}")

        def is_real_step(p: Path) -> bool:
            name = p.name
            if not name.endswith(".json"):
                return False
            if name == "000_SEQUENCE.json":
                return False
            if len(name) < 4 or not name[:3].isdigit():
                return False
            return int(name[:3]) >= 1  # only 001+ are real steps

        step_files = sorted([p for p in steps_dir.glob("*.json") if is_real_step(p)], key=lambda p: p.name)
        self.assertGreaterEqual(len(step_files), 1, [p.name for p in steps_dir.glob("*.json")])

        for fp in step_files:
            d = json.loads(fp.read_text(encoding="utf-8"))
            self.assertIn("team", d, f"Missing team in {fp.name}")
            team = d.get("team")
            self.assertIsInstance(team, dict, f"team must be dict in {fp.name}")
            self.assertIn("id", team, fp.name)
            self.assertIn("model", team, fp.name)

            pol = team.get("policy") or {}
            if isinstance(pol, dict) and pol.get("model"):
                self.assertEqual(team.get("model"), pol.get("model"), fp.name)

            eff = d.get("effective_model_id")
            if eff:
                self.assertEqual(eff, team.get("model"), fp.name)

    def test_team_cannot_run_wrong_mode(self):
        # QA team should not be allowed to run WRITE -> expect validation error
        body = {"mode": "WRITE", "payload": {"text": "x", "team_id": "QA"}}
        r = requests.post(f"{BASE}/agent/step", json=body, timeout=60)
        self.assertEqual(r.status_code, 422, r.text)

    def test_critic_fallback_from_topic(self):
        body = {"mode": "CRITIC", "payload": {"text": "x", "topic": "finance"}}
        r = requests.post(f"{BASE}/agent/step", json=body, timeout=60)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j.get("ok") is True, j)

        artifacts = j.get("artifacts") or []
        self.assertGreaterEqual(len(artifacts), 1, j)

        d = json.loads(Path(artifacts[-1]).read_text(encoding="utf-8"))
        self.assertIn("team", d)
        self.assertIsInstance(d["team"], dict)
        self.assertEqual(d["team"].get("id"), "CRITIC", d["team"])


if __name__ == "__main__":
    unittest.main()

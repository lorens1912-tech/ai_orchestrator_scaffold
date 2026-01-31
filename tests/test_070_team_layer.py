import unittest
import requests
from pathlib import Path
import json

BASE = "http://127.0.0.1:8000"

class Test070TeamLayer(unittest.TestCase):
  def test_team_in_steps_and_model_from_policy(self):
    # B7.1+: 000_SEQUENCE.json is meta, not a step; assert team in real step artifacts
    body = {
        "preset": "DRAFT_EDIT_QUALITY",
        "payload": {"text": "team layer smoke"}
    }
    r = requests.post(f"{BASE}/agent/step", json=body, timeout=60)
    self.assertEqual(r.status_code, 200, r.text)
    j = r.json()
    self.assertTrue(j.get("ok") is True, j)

    artifacts = j.get("artifacts") or []
    self.assertGreaterEqual(len(artifacts), 1, j)

    # check each returned step artifact has team + model from policy
    for ap in artifacts:
        d = json.loads(Path(ap).read_text(encoding="utf-8"))
        self.assertIn("team", d)  # TEAM w każdym *kroku*
        team = d.get("team") or {}
        self.assertIsInstance(team, dict)
        self.assertIn("id", team)
        self.assertIn("model", team)

        pol = team.get("policy") or {}
        if isinstance(pol, dict) and pol.get("model"):
            self.assertEqual(team.get("model"), pol.get("model"))

        # telemetry should match
        eff = d.get("effective_model_id")
        if eff:
            self.assertEqual(eff, team.get("model"))
    body = {
      "preset": "DRAFT_EDIT_QUALITY",
      "book_id": "default",
      "payload": {"topic": "Thriller wstęp: eksperyment.", "max_tokens": 250},
      "resume": False
    }
    r = requests.post(f"{BASE}/agent/step", json=body, timeout=60)
    self.assertEqual(r.status_code, 200)
    data = r.json()
    run_id = data["run_id"]

    steps = Path(f"runs/{run_id}/steps")
    for step in steps.glob("*.json"):
      d = json.loads(step.read_text("utf-8"))
      self.assertIn("team", d)  # TEAM w każdym stepie

    write_step = steps / "001_WRITE.json"
    meta = json.loads(write_step.read_text("utf-8"))["result"]["payload"]["meta"]
    self.assertEqual(meta["requested_model"], "gpt-4.1-mini")  # z policy/env

  def test_team_cannot_run_wrong_mode(self):
    body = {
      "mode": "CRITIC",
      "book_id": "default",
      "payload": {"team_id": "AUTHOR", "text": "Test"},
      "resume": False
    }
    r = requests.post(f"{BASE}/agent/step", json=body, timeout=30)
    self.assertEqual(r.status_code, 422)  # error walidacji

  def test_critic_fallback_from_topic(self):
    body = {
      "mode": "CRITIC",
      "book_id": "default",
      "payload": {"text": "Krótki tekst.", "topic": "thriller bohater odkrywa eksperyment"},
      "resume": False
    }
    r = requests.post(f"{BASE}/agent/step", json=body, timeout=30)
    self.assertEqual(r.status_code, 200)
    data = r.json()
    run_id = data["run_id"]
    critic = json.loads(Path(f"runs/{run_id}/steps/001_CRITIC.json").read_text("utf-8"))
    summary = critic["result"]["payload"]["SUMMARY"]
    self.assertIn("(FICTION)", summary)  # fallback z topic

if __name__ == "__main__":
  unittest.main()

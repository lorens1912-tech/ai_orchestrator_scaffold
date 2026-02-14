import json
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8001"
ROOT = Path(__file__).resolve().parents[1]

def test_steps_have_team_and_policy():
    body = {"book_id":"demo","preset":"PIPELINE_DRAFT","payload":{"title":"X"}, "resume": False}
    r = requests.post(f"{BASE}/agent/step", json=body, timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    run_id = data["run_id"]

    steps_dir = ROOT / "runs" / run_id / "steps"
    step_files = sorted(steps_dir.glob("*.json"))
    assert step_files, f"no step files in {steps_dir}"

    for p in step_files:
        obj = json.loads(p.read_text("utf-8"))
        team = obj.get("team") or {}
        assert team.get("id") or team.get("team_id"), f"{p.name} missing team.id"
        # policy może być w team albo w meta resultu — zależnie jak trzymasz
        policy = team.get("policy_id") or ((obj.get("result") or {}).get("payload") or {}).get("meta", {}).get("policy_id")
        assert policy is not None, f"{p.name} missing policy_id"


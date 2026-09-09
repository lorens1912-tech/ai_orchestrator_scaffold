import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.storage_paths import get_runs_root

client = TestClient(app, raise_server_exceptions=False)

def test_steps_have_team_and_policy():
    ensure_test_book_bible("demo")
    body = {"book_id":"demo","preset":"PIPELINE_DRAFT","payload":{"title":"X"}, "resume": False}
    r = client.post("/agent/step", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    run_id = data["run_id"]

    steps_dir = get_runs_root() / run_id / "steps"
    step_files = sorted(steps_dir.glob("*.json"))
    assert step_files, f"no step files in {steps_dir}"

    for p in step_files:
        obj = json.loads(p.read_text("utf-8"))
        team = obj.get("team") or {}
        assert team.get("id") or team.get("team_id"), f"{p.name} missing team.id"
        # policy może być w team albo w meta resultu — zależnie jak trzymasz
        policy = team.get("policy_id") or ((obj.get("result") or {}).get("payload") or {}).get("meta", {}).get("policy_id")
        assert policy is not None, f"{p.name} missing policy_id"

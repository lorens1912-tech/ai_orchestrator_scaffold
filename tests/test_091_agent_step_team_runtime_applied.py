import json
import hashlib
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8000"

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

def test_agent_step_applies_team_runtime_to_payload():
    # call /agent/step with explicit team_id => strict_team=True
    r = requests.post(f"{BASE}/agent/step", json={
        "book_id": "default",
        "mode": "WRITE",
        "payload": {"text": "x", "team_id": "WRITER"},
        "resume": False
    }, timeout=30)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("ok") is True

    artifacts = j.get("artifacts") or []
    assert artifacts, j
    step_path = Path(artifacts[0])
    assert step_path.exists(), step_path

    step = json.loads(step_path.read_text(encoding="utf-8"))
    inp = step.get("input") or {}

    assert inp.get("_team_id") == "WRITER"
    assert isinstance(inp.get("_team_policy_id"), str) and inp.get("_team_policy_id")
    assert isinstance(inp.get("_requested_model"), str) and inp.get("_requested_model")

    # prompts hashes should match files (if exist)
    prompts = inp.get("_team_prompts") or {}
    sys_p = Path(prompts.get("system_path",""))
    mode_p = Path(prompts.get("mode_path",""))

    if sys_p.exists():
        assert prompts.get("system_sha1") == sha1(sys_p.read_text(encoding="utf-8"))
    if mode_p.exists():
        assert prompts.get("mode_sha1") == sha1(mode_p.read_text(encoding="utf-8"))

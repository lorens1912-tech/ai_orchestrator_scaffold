import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "p20_3_build_retry_feedback.py"


def _run_builder(tmp_path, policy_obj, summary_obj, events_lines):
    policy = tmp_path / "policy.json"
    summary = tmp_path / "summary.json"
    events = tmp_path / "events.jsonl"
    out = tmp_path / "out.json"

    policy.write_text(json.dumps(policy_obj, ensure_ascii=False), encoding="utf-8")
    summary.write_text(json.dumps(summary_obj, ensure_ascii=False), encoding="utf-8")
    events.write_text("".join(events_lines), encoding="utf-8")

    env = os.environ.copy()
    env["P20_POLICY_FILE"] = str(policy)
    env["P20_SUMMARY_FILE"] = str(summary)
    env["P20_EVENTS_FILE"] = str(events)
    env["P20_3_OUT"] = str(out)

    subprocess.check_call([sys.executable, str(SCRIPT)], cwd=str(ROOT), env=env)
    return json.loads(out.read_text(encoding="utf-8"))


def test_p20_3_case_insensitive_policy_and_summary_events(tmp_path):
    j = _run_builder(
        tmp_path,
        {"POLICY": "YELLOW", "fail_rate": 0.07},
        {"total_events": 128},
        ['{"e":1}\n', '{"e":2}\n'],
    )
    assert j["ok"] is True
    assert j["policy"] == "YELLOW"
    assert j["events_count"] == 128
    assert j["retry_feedback_policy"] == "keep_monitoring"


def test_p20_3_fallback_to_jsonl_when_summary_has_no_events(tmp_path):
    j = _run_builder(
        tmp_path,
        {"policy": "GREEN"},
        {"x": 1},
        ['{"e":1}\n', '{"e":2}\n', '{"e":3}\n'],
    )
    assert j["ok"] is True
    assert j["policy"] == "GREEN"
    assert j["events_count"] == 3
    assert j["retry_feedback_policy"] == "can_relax"

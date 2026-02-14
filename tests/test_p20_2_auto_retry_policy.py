import json
import os
import subprocess
import sys
from pathlib import Path


def test_p20_2_tuner_generates_valid_policy(tmp_path: Path):
    baseline = {
        "project": "AgentAI PRO",
        "phase": "P20.2 BASELINE",
        "sample_runs": 3,
        "policy_distribution": {"YELLOW": 3},
        "dominant_policy": "YELLOW",
        "avg_fail_rate": 0.09,
        "avg_reject_rate": 0.04,
        "avg_min_words_fail_rate": 0.11,
        "avg_events": 124.0
    }

    thresholds = {
        "green_fail_max": 0.05,
        "yellow_fail_max": 0.12,
        "red_fail_min": 0.20,
        "green_reject_max": 0.03,
        "yellow_reject_max": 0.10,
        "red_reject_min": 0.20
    }

    baseline_path = tmp_path / "baseline.json"
    thresholds_path = tmp_path / "thresholds.json"
    out_path = tmp_path / "auto_retry_policy.json"

    baseline_path.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    thresholds_path.write_text(json.dumps(thresholds, ensure_ascii=False), encoding="utf-8")

    env = os.environ.copy()
    env["P20_BASELINE_IN"] = str(baseline_path)
    env["P20_THRESHOLDS_IN"] = str(thresholds_path)
    env["P20_POLICY_OUT"] = str(out_path)

    cp = subprocess.run(
        [sys.executable, r".\scripts\p20_2_tune_auto_retry_policy.py"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert cp.returncode == 0, f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
    assert out_path.exists(), "Brak pliku auto_retry_policy.json"

    j = json.loads(out_path.read_text(encoding="utf-8"))

    required = [
        "project",
        "phase",
        "policy_level",
        "max_retries",
        "backoff_seconds",
        "retry_strategy",
        "require_critic_on_retry",
        "force_human_review",
        "block_publish_on_red",
        "signals",
        "thresholds_used",
    ]
    missing = [k for k in required if k not in j]
    assert not missing, f"Brak kluczy: {missing}"

    assert j["policy_level"] in ("GREEN", "YELLOW", "RED")
    assert isinstance(j["max_retries"], int)
    assert 0 <= j["max_retries"] <= 3
    assert isinstance(j["backoff_seconds"], list)

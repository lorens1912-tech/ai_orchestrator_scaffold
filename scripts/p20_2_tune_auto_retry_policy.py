from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _load_json_env(name: str) -> dict[str, Any]:
    path_value = os.environ.get(name)
    if not path_value:
        raise SystemExit(f"Missing required env var: {name}")
    path = Path(path_value)
    return json.loads(path.read_text(encoding="utf-8"))


def _policy_level(baseline: dict[str, Any], thresholds: dict[str, Any]) -> str:
    fail_rate = float(baseline.get("avg_fail_rate") or 0.0)
    reject_rate = float(baseline.get("avg_reject_rate") or 0.0)

    if (
        fail_rate >= float(thresholds.get("red_fail_min", 1.0))
        or reject_rate >= float(thresholds.get("red_reject_min", 1.0))
    ):
        return "RED"

    if (
        fail_rate <= float(thresholds.get("green_fail_max", 0.0))
        and reject_rate <= float(thresholds.get("green_reject_max", 0.0))
    ):
        return "GREEN"

    if (
        fail_rate <= float(thresholds.get("yellow_fail_max", 1.0))
        and reject_rate <= float(thresholds.get("yellow_reject_max", 1.0))
    ):
        return "YELLOW"

    dominant = str(baseline.get("dominant_policy") or "").upper().strip()
    if dominant in {"GREEN", "YELLOW", "RED"}:
        return dominant
    return "RED"


def _build_policy(baseline: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, Any]:
    level = _policy_level(baseline, thresholds)
    max_retries = {"GREEN": 0, "YELLOW": 1, "RED": 3}[level]
    return {
        "project": baseline.get("project", "AgentPRO"),
        "phase": "P20.2 AUTO RETRY POLICY",
        "policy_level": level,
        "max_retries": max_retries,
        "backoff_seconds": [2, 5, 10][:max_retries],
        "retry_strategy": "none" if max_retries == 0 else "quality_feedback",
        "require_critic_on_retry": level in {"YELLOW", "RED"},
        "force_human_review": level == "RED",
        "block_publish_on_red": level == "RED",
        "signals": {
            "sample_runs": baseline.get("sample_runs"),
            "dominant_policy": baseline.get("dominant_policy"),
            "avg_fail_rate": baseline.get("avg_fail_rate"),
            "avg_reject_rate": baseline.get("avg_reject_rate"),
            "avg_min_words_fail_rate": baseline.get("avg_min_words_fail_rate"),
            "avg_events": baseline.get("avg_events"),
        },
        "thresholds_used": thresholds,
    }


def main() -> int:
    baseline = _load_json_env("P20_BASELINE_IN")
    thresholds = _load_json_env("P20_THRESHOLDS_IN")
    out_path_value = os.environ.get("P20_POLICY_OUT")
    if not out_path_value:
        raise SystemExit("Missing required env var: P20_POLICY_OUT")

    out_path = Path(out_path_value)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(_build_policy(baseline, thresholds), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

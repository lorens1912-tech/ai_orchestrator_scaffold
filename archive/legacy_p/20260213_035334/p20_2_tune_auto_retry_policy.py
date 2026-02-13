from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple


def deep_items(obj: Any, prefix: str = "") -> Iterable[Tuple[str, Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            yield from deep_items(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{prefix}[{i}]"
            yield from deep_items(v, p)
    else:
        yield prefix, obj


def flatten(obj: Any) -> Dict[str, Any]:
    return {k: v for k, v in deep_items(obj)}


def latest_file(pattern: str) -> str:
    files = glob.glob(pattern)
    if not files:
        raise FileNotFoundError(f"No files matched pattern: {pattern}")
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[0]


def load_json(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))


def pick_number(flat: Dict[str, Any], candidates: list[str]) -> float | None:
    keys = list(flat.keys())

    # exact/suffix match first
    for c in candidates:
        lc = c.lower()
        for k in keys:
            lk = k.lower()
            if lk == lc or lk.endswith("." + lc) or lk.endswith("_" + lc):
                v = flat[k]
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    try:
                        return float(v.strip())
                    except Exception:
                        pass

    # contains fallback
    for c in candidates:
        lc = c.lower()
        for k, v in flat.items():
            if lc in k.lower():
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    try:
                        return float(v.strip())
                    except Exception:
                        pass
    return None


def pick_policy(flat: Dict[str, Any]) -> str:
    allowed = {"GREEN", "YELLOW", "RED"}
    preferred_keys = [
        "policy",
        "dominant_policy",
        "alert_policy",
        "quality_policy",
        "level",
        "status",
    ]

    for pref in preferred_keys:
        lp = pref.lower()
        for k, v in flat.items():
            lk = k.lower()
            if (lk == lp or lk.endswith("." + lp) or lk.endswith("_" + lp) or lp in lk) and isinstance(v, str):
                up = v.strip().upper()
                if up in allowed:
                    return up

    for v in flat.values():
        if isinstance(v, str):
            up = v.strip().upper()
            if up in allowed:
                return up

    return "UNKNOWN"


def thr_value(thr_flat: Dict[str, Any], name: str, default: float) -> float:
    v = pick_number(thr_flat, [name])
    return float(v) if v is not None else float(default)


def build_policy(baseline: Dict[str, Any], thresholds: Dict[str, Any], baseline_path: str) -> Dict[str, Any]:
    bflat = flatten(baseline)
    tflat = flatten(thresholds)

    dominant = pick_policy(bflat)
    avg_fail = pick_number(bflat, ["avg_fail_rate", "fail_rate"])
    avg_reject = pick_number(bflat, ["avg_reject_rate", "reject_rate"])
    avg_min_words_fail = pick_number(bflat, ["avg_min_words_fail_rate", "min_words_fail_rate"])
    avg_events = pick_number(bflat, ["avg_events", "events", "event_count", "total_events", "count"])

    # Defaults conservative
    green_fail_max = thr_value(tflat, "green_fail_max", 0.05)
    yellow_fail_max = thr_value(tflat, "yellow_fail_max", 0.12)
    red_fail_min = thr_value(tflat, "red_fail_min", 0.20)

    green_reject_max = thr_value(tflat, "green_reject_max", 0.03)
    yellow_reject_max = thr_value(tflat, "yellow_reject_max", 0.10)
    red_reject_min = thr_value(tflat, "red_reject_min", 0.20)

    # Determine policy level
    level = dominant
    if level not in {"GREEN", "YELLOW", "RED"}:
        # infer from numeric signals
        if avg_fail is None and avg_reject is None:
            level = "YELLOW"
        else:
            f = 0.0 if avg_fail is None else float(avg_fail)
            r = 0.0 if avg_reject is None else float(avg_reject)
            if f >= red_fail_min or r >= red_reject_min:
                level = "RED"
            elif f <= green_fail_max and r <= green_reject_max:
                level = "GREEN"
            else:
                level = "YELLOW"
    else:
        # escalation only (anti-oscillation)
        f = None if avg_fail is None else float(avg_fail)
        r = None if avg_reject is None else float(avg_reject)
        if (f is not None and f >= red_fail_min) or (r is not None and r >= red_reject_min):
            level = "RED"
        elif level == "GREEN":
            # guard against under-reacting
            if (f is not None and f > yellow_fail_max) or (r is not None and r > yellow_reject_max):
                level = "YELLOW"

    if level == "GREEN":
        max_retries = 0
        backoff_seconds = [0]
        require_critic_on_retry = False
        force_human_review = False
        block_publish_on_red = False
    elif level == "YELLOW":
        max_retries = 1
        backoff_seconds = [3]
        require_critic_on_retry = True
        force_human_review = False
        block_publish_on_red = True
    else:  # RED
        max_retries = 2
        backoff_seconds = [5, 12]
        require_critic_on_retry = True
        force_human_review = True
        block_publish_on_red = True

    min_words_focus = bool((avg_min_words_fail is not None) and (float(avg_min_words_fail) >= 0.10))

    policy = {
        "project": "AgentAI PRO",
        "phase": "P20.2 AUTO RETRY POLICY",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_baseline_file": str(baseline_path),
        "policy_level": level,
        "max_retries": int(max_retries),
        "backoff_seconds": backoff_seconds,
        "retry_strategy": "targeted_min_words_fix" if min_words_focus else "standard_retry",
        "require_critic_on_retry": require_critic_on_retry,
        "force_human_review": force_human_review,
        "block_publish_on_red": block_publish_on_red,
        "signals": {
            "dominant_policy_raw": dominant,
            "avg_fail_rate": avg_fail,
            "avg_reject_rate": avg_reject,
            "avg_min_words_fail_rate": avg_min_words_fail,
            "avg_events": avg_events,
        },
        "thresholds_used": {
            "green_fail_max": green_fail_max,
            "yellow_fail_max": yellow_fail_max,
            "red_fail_min": red_fail_min,
            "green_reject_max": green_reject_max,
            "yellow_reject_max": yellow_reject_max,
            "red_reject_min": red_reject_min,
        },
    }
    return policy


def main() -> int:
    baseline_in = os.environ.get("P20_BASELINE_IN")
    thresholds_in = os.environ.get("P20_THRESHOLDS_IN", "config/quality_alert_thresholds.json")
    policy_out = os.environ.get("P20_POLICY_OUT", "config/auto_retry_policy.json")

    if not baseline_in:
        baseline_in = latest_file(r"reports\handoff\telemetry\P20_2_BASELINE_*.json")

    baseline = load_json(baseline_in)
    thresholds = {}
    if Path(thresholds_in).exists():
        thresholds = load_json(thresholds_in)

    policy = build_policy(baseline, thresholds, baseline_in)

    out_path = Path(policy_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")

    print("P20_2_AUTO_RETRY_POLICY_READY:", str(out_path))
    print(json.dumps(policy, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

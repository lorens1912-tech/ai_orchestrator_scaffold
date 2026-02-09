from __future__ import annotations
from typing import Dict, Any, Tuple

def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def adjust_policy_from_feedback(current_policy: Dict[str, Any] | None, feedback: Dict[str, Any] | None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    cp = dict(current_policy or {})
    fb = dict(feedback or {})

    policy = {
        "quality_min": _f(cp.get("quality_min"), 0.78),
        "max_retries": int(_f(cp.get("max_retries"), 2)),
        "critic_weight": _f(cp.get("critic_weight"), 1.00),
        "writer_temperature": _f(cp.get("writer_temperature"), 0.55),
    }

    reject_rate       = _clamp(_f(fb.get("reject_rate"), 0.0), 0.0, 1.0)
    retry_rate        = _clamp(_f(fb.get("retry_rate"), 0.0), 0.0, 1.0)
    accept_rate       = _clamp(_f(fb.get("accept_rate"), 1.0), 0.0, 1.0)
    observed_quality  = _clamp(_f(fb.get("observed_quality"), policy["quality_min"]), 0.0, 1.0)
    user_satisfaction = _clamp(_f(fb.get("user_satisfaction"), 0.5), 0.0, 1.0)

    quality_gap = max(0.0, policy["quality_min"] - observed_quality)

    pressure = (
        0.40 * reject_rate +
        0.30 * retry_rate +
        0.15 * (1.0 - accept_rate) +
        0.10 * quality_gap +
        0.05 * (1.0 - user_satisfaction)
    )
    pressure = _clamp(pressure, 0.0, 1.0)

    if pressure >= 0.55:
        band = "tighten"
        policy["quality_min"] += 0.03
        policy["max_retries"] += 1
        policy["critic_weight"] += 0.10
        policy["writer_temperature"] -= 0.05
    elif pressure <= 0.25:
        band = "relax"
        policy["quality_min"] -= 0.02
        policy["max_retries"] -= 1
        policy["critic_weight"] -= 0.05
        policy["writer_temperature"] += 0.05
    else:
        band = "hold"

    policy["quality_min"] = round(_clamp(policy["quality_min"], 0.72, 0.90), 3)
    policy["max_retries"] = int(_clamp(policy["max_retries"], 1, 5))
    policy["critic_weight"] = round(_clamp(policy["critic_weight"], 0.80, 1.60), 3)
    policy["writer_temperature"] = round(_clamp(policy["writer_temperature"], 0.20, 0.90), 3)

    audit = {
        "band": band,
        "pressure": round(pressure, 4),
        "signals": {
            "reject_rate": reject_rate,
            "retry_rate": retry_rate,
            "accept_rate": accept_rate,
            "observed_quality": observed_quality,
            "user_satisfaction": user_satisfaction,
            "quality_gap": round(quality_gap, 4),
        },
    }

    return policy, audit

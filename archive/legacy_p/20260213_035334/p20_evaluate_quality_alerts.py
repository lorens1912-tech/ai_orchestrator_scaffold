from __future__ import annotations

import json
import os
import glob
from pathlib import Path
from datetime import datetime


def ci_get(d, *keys):
    if not isinstance(d, dict):
        return None
    m = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        v = m.get(str(k).lower())
        if v is not None and not (isinstance(v, str) and v.strip() == ""):
            return v
    return None


def as_float(*vals):
    for v in vals:
        try:
            if v is None:
                continue
            if isinstance(v, str) and v.strip() == "":
                continue
            return float(v)
        except Exception:
            pass
    return None


def as_int(*vals):
    for v in vals:
        try:
            if v is None:
                continue
            if isinstance(v, str) and v.strip() == "":
                continue
            return int(float(v))
        except Exception:
            pass
    return None


def latest_file(pattern: str) -> Path | None:
    xs = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    return Path(xs[0]) if xs else None


repo = Path(__file__).resolve().parents[1]
telemetry_dir = repo / "reports" / "handoff" / "telemetry"
telemetry_dir.mkdir(parents=True, exist_ok=True)

summary_env = os.environ.get("P20_SUMMARY_FILE", "").strip()
events_env = os.environ.get("P20_EVENTS_FILE", "").strip()
out_env = os.environ.get("P20_ALERT_OUT", "").strip()

summary_file = Path(summary_env) if summary_env else latest_file(str(telemetry_dir / "P20_QUALITY_SUMMARY_*.json"))
events_file = Path(events_env) if events_env else latest_file(str(telemetry_dir / "P20_QUALITY_EVENTS_*.jsonl"))

summary = {}
if summary_file and summary_file.exists():
    summary = json.loads(summary_file.read_text(encoding="utf-8"))

metrics = ci_get(summary, "metrics", "stats", "summary")
if not isinstance(metrics, dict):
    metrics = {}

events = as_int(
    ci_get(summary, "events", "total_events", "count", "event_count"),
    ci_get(metrics, "events", "total_events", "count", "event_count"),
)

if (events is None or events <= 0) and events_file and events_file.exists():
    try:
        with events_file.open("r", encoding="utf-8") as f:
            events = sum(1 for _ in f)
    except Exception:
        pass

fail_rate = as_float(
    ci_get(summary, "fail_rate", "failure_rate"),
    ci_get(metrics, "fail_rate", "failure_rate"),
)
reject_rate = as_float(
    ci_get(summary, "reject_rate"),
    ci_get(metrics, "reject_rate"),
)
min_words_fail_rate = as_float(
    ci_get(summary, "min_words_fail_rate"),
    ci_get(metrics, "min_words_fail_rate"),
)

# Progi
defaults = {
    "yellow": {"fail_rate": 0.15, "reject_rate": 0.08, "min_words_fail_rate": 0.12},
    "red": {"fail_rate": 0.30, "reject_rate": 0.20, "min_words_fail_rate": 0.25},
}
thr_file = repo / "config" / "quality_alert_thresholds.json"
cfg = {}
if thr_file.exists():
    try:
        cfg = json.loads(thr_file.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}

yellow = {
    "fail_rate": as_float(ci_get(ci_get(cfg, "yellow") or {}, "fail_rate")) or defaults["yellow"]["fail_rate"],
    "reject_rate": as_float(ci_get(ci_get(cfg, "yellow") or {}, "reject_rate")) or defaults["yellow"]["reject_rate"],
    "min_words_fail_rate": as_float(ci_get(ci_get(cfg, "yellow") or {}, "min_words_fail_rate")) or defaults["yellow"]["min_words_fail_rate"],
}
red = {
    "fail_rate": as_float(ci_get(ci_get(cfg, "red") or {}, "fail_rate")) or defaults["red"]["fail_rate"],
    "reject_rate": as_float(ci_get(ci_get(cfg, "red") or {}, "reject_rate")) or defaults["red"]["reject_rate"],
    "min_words_fail_rate": as_float(ci_get(ci_get(cfg, "red") or {}, "min_words_fail_rate")) or defaults["red"]["min_words_fail_rate"],
}

if events is None or events <= 0:
    policy = "RED"
    reason = "EVENTS_LE_ZERO_OR_NULL"
else:
    rates = {
        "fail_rate": fail_rate,
        "reject_rate": reject_rate,
        "min_words_fail_rate": min_words_fail_rate,
    }
    if all(v is None for v in rates.values()):
        policy = "YELLOW"
        reason = "NO_RATE_METRICS"
    else:
        above_red = any((rates[k] is not None and rates[k] >= red[k]) for k in rates.keys())
        above_yellow = any((rates[k] is not None and rates[k] >= yellow[k]) for k in rates.keys())
        if above_red:
            policy = "RED"
            reason = "RATE_ABOVE_RED"
        elif above_yellow:
            policy = "YELLOW"
            reason = "RATE_ABOVE_YELLOW"
        else:
            policy = "GREEN"
            reason = "WITHIN_GREEN"

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out_file = Path(out_env) if out_env else (telemetry_dir / f"P20_ALERT_POLICY_{ts}.json")

payload = {
    "policy": policy,
    "reason": reason,
    "events": events,
    "total_events": events,
    "count": events,
    "fail_rate": fail_rate,
    "reject_rate": reject_rate,
    "min_words_fail_rate": min_words_fail_rate,
    "summary_file": str(summary_file) if summary_file else "",
    "events_file": str(events_file) if events_file else "",
    "generated_at": datetime.now().isoformat(timespec="seconds"),
}

out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"P20_ALERT_POLICY: {policy}")
print(f"OUT: {out_file}")

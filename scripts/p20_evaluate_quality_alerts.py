import argparse
import datetime as dt
import glob
import json
import os
import sys
from pathlib import Path

def load_json(p: Path):
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)

def deep_find(obj, wanted_lower: set):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in wanted_lower:
                return v
        for v in obj.values():
            r = deep_find(v, wanted_lower)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for it in obj:
            r = deep_find(it, wanted_lower)
            if r is not None:
                return r
    return None

def to_float(v):
    try:
        x = float(v)
        # jeśli podane w % (np. 12), znormalizuj do 0.12
        if x > 1.0 and x <= 100.0:
            x = x / 100.0
        return x
    except Exception:
        return None

def to_int(v):
    try:
        return int(float(v))
    except Exception:
        return None

def pick_latest(pattern: str):
    items = glob.glob(pattern)
    if not items:
        return None
    items.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return Path(items[0])

def read_thresholds(path: Path | None):
    defaults = {
        "fail_rate_yellow": 0.10, "fail_rate_red": 0.25,
        "reject_rate_yellow": 0.05, "reject_rate_red": 0.15,
        "min_words_fail_rate_yellow": 0.08, "min_words_fail_rate_red": 0.20
    }
    if not path or not path.exists():
        return defaults
    try:
        j = load_json(path)
    except Exception:
        return defaults

    def get(name, aliases, dflt):
        v = deep_find(j, {a.lower() for a in aliases})
        fv = to_float(v)
        return dflt if fv is None else fv

    out = {
        "fail_rate_yellow": get("fail_rate_yellow", ["fail_rate_yellow","yellow_fail_rate","fail_yellow"], defaults["fail_rate_yellow"]),
        "fail_rate_red": get("fail_rate_red", ["fail_rate_red","red_fail_rate","fail_red"], defaults["fail_rate_red"]),
        "reject_rate_yellow": get("reject_rate_yellow", ["reject_rate_yellow","yellow_reject_rate","reject_yellow"], defaults["reject_rate_yellow"]),
        "reject_rate_red": get("reject_rate_red", ["reject_rate_red","red_reject_rate","reject_red"], defaults["reject_rate_red"]),
        "min_words_fail_rate_yellow": get("min_words_fail_rate_yellow", ["min_words_fail_rate_yellow","yellow_min_words_fail_rate","min_words_yellow"], defaults["min_words_fail_rate_yellow"]),
        "min_words_fail_rate_red": get("min_words_fail_rate_red", ["min_words_fail_rate_red","red_min_words_fail_rate","min_words_red"], defaults["min_words_fail_rate_red"]),
    }
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default=os.environ.get("P20_SUMMARY_FILE",""))
    ap.add_argument("--out", default=os.environ.get("P20_POLICY_OUT",""))
    ap.add_argument("--thresholds", default=os.environ.get("P20_THRESHOLDS_FILE","config/quality_alert_thresholds.json"))
    args = ap.parse_args()

    summary_path = Path(args.summary) if args.summary else None
    if summary_path is None or not summary_path.exists():
        summary_path = pick_latest(r"reports\handoff\telemetry\P20_QUALITY_SUMMARY_*.json")
    if summary_path is None or not summary_path.exists():
        print("P20_ALERT_POLICY: UNKNOWN")
        print("OUT: ")
        print("ERROR: missing summary file", file=sys.stderr)
        sys.exit(2)

    out_path = Path(args.out) if args.out else None
    if out_path is None or str(out_path).strip() == "":
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = Path(rf"reports\handoff\telemetry\P20_ALERT_POLICY_{ts}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    thresholds_path = Path(args.thresholds) if args.thresholds else Path("config/quality_alert_thresholds.json")
    thresholds = read_thresholds(thresholds_path)

    sj = load_json(summary_path)

    events = to_int(deep_find(sj, {"events","total_events","count","event_count","total"}))
    fail_rate = to_float(deep_find(sj, {"fail_rate","failure_rate","failratio","fails_ratio"}))
    reject_rate = to_float(deep_find(sj, {"reject_rate","rejected_rate","rejectratio"}))
    min_words_fail_rate = to_float(deep_find(sj, {"min_words_fail_rate","minwords_fail_rate","min_words_failure_rate"}))

    reasons = []
    policy = "GREEN"

    if events is None or events <= 0:
        policy = "RED"
        reasons.append("NO_EVENTS")
    else:
        red_hits = []
        yellow_hits = []

        if fail_rate is not None:
            if fail_rate >= thresholds["fail_rate_red"]:
                red_hits.append(f"FAIL_RATE>={thresholds['fail_rate_red']}")
            elif fail_rate >= thresholds["fail_rate_yellow"]:
                yellow_hits.append(f"FAIL_RATE>={thresholds['fail_rate_yellow']}")
        else:
            reasons.append("MISSING_FAIL_RATE")

        if reject_rate is not None:
            if reject_rate >= thresholds["reject_rate_red"]:
                red_hits.append(f"REJECT_RATE>={thresholds['reject_rate_red']}")
            elif reject_rate >= thresholds["reject_rate_yellow"]:
                yellow_hits.append(f"REJECT_RATE>={thresholds['reject_rate_yellow']}")
        else:
            reasons.append("MISSING_REJECT_RATE")

        if min_words_fail_rate is not None:
            if min_words_fail_rate >= thresholds["min_words_fail_rate_red"]:
                red_hits.append(f"MIN_WORDS_FAIL_RATE>={thresholds['min_words_fail_rate_red']}")
            elif min_words_fail_rate >= thresholds["min_words_fail_rate_yellow"]:
                yellow_hits.append(f"MIN_WORDS_FAIL_RATE>={thresholds['min_words_fail_rate_yellow']}")
        else:
            reasons.append("MISSING_MIN_WORDS_FAIL_RATE")

        if red_hits:
            policy = "RED"
            reasons.extend(red_hits)
        elif yellow_hits:
            policy = "YELLOW"
            reasons.extend(yellow_hits)
        elif fail_rate is None and reject_rate is None and min_words_fail_rate is None:
            policy = "YELLOW"
            reasons.append("MISSING_ALL_RATE_FIELDS")
        else:
            policy = "GREEN"
            reasons.append("WITHIN_THRESHOLDS")

    out = {
        "project": "AgentAI PRO",
        "phase": "P20.2",
        "policy": policy,          # kanoniczne
        "POLICY": policy,          # kompatybilność
        "events": events,
        "fail_rate": fail_rate,
        "reject_rate": reject_rate,
        "min_words_fail_rate": min_words_fail_rate,
        "reasons": reasons,
        "summary_file": str(summary_path),
        "thresholds_file": str(thresholds_path),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds")
    }

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"P20_ALERT_POLICY: {policy}")
    print(f"OUT: {out_path}")

if __name__ == "__main__":
    main()

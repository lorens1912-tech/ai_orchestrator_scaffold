from __future__ import annotations
import argparse
import json
from pathlib import Path
from datetime import datetime

def _pick(d: dict, *keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default

def _latest_summary_file() -> Path:
    p = Path("reports/handoff/telemetry")
    files = sorted(p.glob("P20_QUALITY_SUMMARY_*.json"))
    if not files:
        raise FileNotFoundError("Brak plików P20_QUALITY_SUMMARY_*.json")
    return files[-1]

def _norm_reason_counts(reason_counts: dict) -> dict:
    out = {}
    for k, v in (reason_counts or {}).items():
        ku = str(k).upper()
        out[ku] = int(v or 0)
    return out

def evaluate(summary: dict, thr: dict) -> dict:
    total = int(_pick(summary, "events", "total_events", "count", default=0) or 0)
    dec = _pick(summary, "decision_counts", "decisions", default={}) or {}
    reasons = _norm_reason_counts(_pick(summary, "reason_counts", "reasons", default={}) or {})

    fail = int(dec.get("FAIL", 0))
    block_true = int(_pick(summary, "block_pipeline_true", "blocked_count", default=0) or 0)
    empty = int(reasons.get("EMPTY", 0))
    min_words = int(reasons.get("MIN_WORDS", 0))

    def rate(x): 
        return (x / total) if total > 0 else 0.0

    fail_rate = rate(fail)
    block_rate = rate(block_true)
    empty_rate = rate(empty)
    min_words_rate = rate(min_words)

    breaches = []
    warns = []

    def check(metric_name, value, warn_key, max_key):
        w = float(thr.get(warn_key, 1.0))
        m = float(thr.get(max_key, 1.0))
        if value > m:
            breaches.append(f"{metric_name}: {value:.4f} > {m:.4f}")
        elif value > w:
            warns.append(f"{metric_name}: {value:.4f} > {w:.4f}")

    check("fail_rate", fail_rate, "warn_fail_rate", "max_fail_rate")
    check("block_rate", block_rate, "warn_block_rate", "max_block_rate")
    check("empty_rate", empty_rate, "warn_empty_rate", "max_empty_rate")
    check("min_words_rate", min_words_rate, "warn_min_words_rate", "max_min_words_rate")

    window_min = int(thr.get("window_events_min", 0))
    if total < window_min:
        warns.append(f"window_events: {total} < {window_min}")

    status = "GREEN"
    if breaches:
        status = "RED"
    elif warns:
        status = "YELLOW"

    return {
        "status": status,
        "events": total,
        "metrics": {
            "fail_rate": fail_rate,
            "block_rate": block_rate,
            "empty_rate": empty_rate,
            "min_words_rate": min_words_rate
        },
        "counts": {
            "fail": fail,
            "block_true": block_true,
            "empty": empty,
            "min_words": min_words
        },
        "warns": warns,
        "breaches": breaches,
        "thresholds": thr
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", type=str, default="")
    ap.add_argument("--thresholds", type=str, default="config/quality_alert_thresholds.json")
    ap.add_argument("--out", type=str, default="")
    args = ap.parse_args()

    summary_path = Path(args.summary) if args.summary else _latest_summary_file()
    thr_path = Path(args.thresholds)

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    thr = json.loads(thr_path.read_text(encoding="utf-8"))

    result = evaluate(summary, thr)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else Path(f"reports/handoff/telemetry/P20_ALERT_POLICY_{ts}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"P20_ALERT_POLICY: {result['status']}")
    print(f"OUT: {out}")
    if result["status"] == "RED":
        raise SystemExit(2)

if __name__ == "__main__":
    main()

from __future__ import annotations
import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.quality_taxonomy import classify_quality_payload

def iter_quality_files(runs_root: Path, max_runs: int):
    run_dirs = [p for p in runs_root.glob("run_*") if p.is_dir()]
    run_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if max_runs > 0:
        run_dirs = run_dirs[:max_runs]

    for rd in run_dirs:
        steps = rd / "steps"
        if not steps.exists():
            continue
        for qf in sorted(steps.glob("*_QUALITY.json")):
            yield qf

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", default="runs")
    ap.add_argument("--out-dir", default="reports/handoff/telemetry")
    ap.add_argument("--max-runs", type=int, default=200)
    args = ap.parse_args()

    runs_root = Path(args.runs_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    events_path = out_dir / f"P20_QUALITY_EVENTS_{ts}.jsonl"
    summary_path = out_dir / f"P20_QUALITY_SUMMARY_{ts}.json"

    decision_counts = Counter()
    reason_counts = Counter()
    tag_counts = Counter()

    events_written = 0

    with events_path.open("w", encoding="utf-8") as f_out:
        for qf in iter_quality_files(runs_root, args.max_runs):
            try:
                j = json.loads(qf.read_text(encoding="utf-8"))
            except Exception:
                continue

            payload = ((j.get("result") or {}).get("payload") or {})
            t = classify_quality_payload(payload)

            ev = {
                "run_id": j.get("run_id"),
                "index": j.get("index"),
                "preset_id": j.get("preset_id"),
                "mode": j.get("mode"),
                "created_at": j.get("created_at"),
                "decision": t["decision"],
                "reason_codes": t["reason_codes"],
                "words": t["words"],
                "chars": t["chars"],
                "tags": t["tags"],
                "source_file": str(qf).replace("\\\\", "/"),
            }

            f_out.write(json.dumps(ev, ensure_ascii=False) + "\n")
            events_written += 1

            decision_counts[ev["decision"]] += 1
            for rc in ev["reason_codes"]:
                reason_counts[rc] += 1
            for tg in ev["tags"]:
                tag_counts[tg] += 1

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "runs_root": str(runs_root).replace("\\\\", "/"),
        "events_file": str(events_path).replace("\\\\", "/"),
        "events_count": events_written,
        "decision_counts": dict(decision_counts),
        "reason_counts": dict(reason_counts),
        "top_tags": dict(tag_counts.most_common(25)),
    }

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"P20_TELEMETRY_OK: events={events_written}")
    print(f"EVENTS: {events_path}")
    print(f"SUMMARY: {summary_path}")

if __name__ == "__main__":
    main()

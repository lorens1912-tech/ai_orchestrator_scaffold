from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TELE_DIR = ROOT / "reports" / "handoff" / "telemetry"


def _latest(pattern: str) -> Path | None:
    files = sorted(TELE_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _pick_ci(d: dict, *keys: str):
    if not isinstance(d, dict):
        return None
    lowered = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        lk = k.lower()
        if lk in lowered and lowered[lk] is not None:
            return lowered[lk]
    return None


def _events_from_summary(summary: dict) -> int | None:
    v = _pick_ci(summary, "events", "total_events", "count")
    try:
        return int(v) if v is not None else None
    except Exception:
        return None


def _events_from_jsonl(path: Path | None) -> int | None:
    if path is None or not path.exists():
        return None
    n = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def _recommendation(policy: str, events_count: int) -> tuple[str, dict]:
    # Prosty, deterministyczny mapping dla P20.3
    if policy == "RED":
        return "tighten_control", {
            "max_retries": 0,
            "backoff_seconds": [5],
            "require_critic_on_retry": True,
            "force_human_review": True,
            "block_publish_on_red": True,
        }
    if policy == "YELLOW":
        return "keep_monitoring", {
            "max_retries": 1,
            "backoff_seconds": [3],
            "require_critic_on_retry": True,
            "force_human_review": False,
            "block_publish_on_red": True,
        }
    if policy == "GREEN":
        return "can_relax", {
            "max_retries": 2,
            "backoff_seconds": [2, 4],
            "require_critic_on_retry": False,
            "force_human_review": False,
            "block_publish_on_red": True,
        }
    return "unknown_policy", {
        "max_retries": 1,
        "backoff_seconds": [3],
        "require_critic_on_retry": True,
        "force_human_review": False,
        "block_publish_on_red": True,
    }


def main() -> int:
    TELE_DIR.mkdir(parents=True, exist_ok=True)

    policy_env = os.getenv("P20_POLICY_FILE", "").strip()
    summary_env = os.getenv("P20_SUMMARY_FILE", "").strip()
    events_env = os.getenv("P20_EVENTS_FILE", "").strip()
    out_env = os.getenv("P20_3_OUT", "").strip()

    policy_file = Path(policy_env) if policy_env else _latest("P20_ALERT_POLICY_*.json")
    summary_file = Path(summary_env) if summary_env else _latest("P20_QUALITY_SUMMARY_*.json")
    events_file = Path(events_env) if events_env else _latest("P20_QUALITY_EVENTS_*.jsonl")

    if policy_file is None or not policy_file.exists():
        raise FileNotFoundError("Missing policy file: P20_ALERT_POLICY_*.json")
    if summary_file is None or not summary_file.exists():
        raise FileNotFoundError("Missing summary file: P20_QUALITY_SUMMARY_*.json")

    pj = _load_json(policy_file)
    sj = _load_json(summary_file)

    policy_raw = _pick_ci(pj, "policy", "level", "dominant_policy")
    policy = str(policy_raw).upper().strip() if policy_raw is not None else "UNKNOWN"
    if policy not in {"GREEN", "YELLOW", "RED"}:
        policy = "UNKNOWN"

    events_count = _events_from_summary(sj)
    if events_count is None or events_count <= 0:
        fallback = _events_from_jsonl(events_file)
        if fallback is not None:
            events_count = fallback

    retry_feedback_policy, suggested_override = _recommendation(policy, int(events_count or 0))
    ok = policy in {"GREEN", "YELLOW", "RED"} and (events_count is not None and int(events_count) > 0)

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    out_path = Path(out_env) if out_env else (TELE_DIR / f"P20_3_RETRY_FEEDBACK_{ts}.json")

    result = {
        "project": "AgentAI PRO",
        "phase": "P20.3 RETRY OUTCOME TELEMETRY FEEDBACK LOOP",
        "generated_at_utc": now.isoformat(),
        "ok": bool(ok),
        "policy": policy,
        "events_count": int(events_count) if events_count is not None else None,
        "retry_feedback_policy": retry_feedback_policy,
        "suggested_policy_override": suggested_override,
        "source_files": {
            "policy_file": str(policy_file.resolve()),
            "summary_file": str(summary_file.resolve()),
            "events_file": str(events_file.resolve()) if events_file else "",
        },
    }

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"P20_3_RETRY_FEEDBACK_READY: {out_path}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

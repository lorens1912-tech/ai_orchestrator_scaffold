from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _env_path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required env var: {name}")
    return Path(value)


def _policy_level(policy: dict[str, Any]) -> str:
    raw = policy.get("policy") or policy.get("POLICY") or policy.get("policy_level")
    level = str(raw or "YELLOW").upper().strip()
    return level if level in {"GREEN", "YELLOW", "RED"} else "YELLOW"


def _events_count(summary: dict[str, Any], events_path: Path) -> int:
    for key in ("total_events", "events_count", "count"):
        value = summary.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)

    if not events_path.exists():
        return 0
    return sum(1 for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip())


def _feedback_policy(level: str) -> str:
    if level == "GREEN":
        return "can_relax"
    if level == "RED":
        return "tighten"
    return "keep_monitoring"


def main() -> int:
    policy_path = _env_path("P20_POLICY_FILE")
    summary_path = _env_path("P20_SUMMARY_FILE")
    events_path = _env_path("P20_EVENTS_FILE")
    out_path = _env_path("P20_3_OUT")

    policy = _load_json(policy_path)
    summary = _load_json(summary_path)
    level = _policy_level(policy)
    out = {
        "ok": True,
        "policy": level,
        "events_count": _events_count(summary, events_path),
        "retry_feedback_policy": _feedback_policy(level),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

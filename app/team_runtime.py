from __future__ import annotations

from pathlib import Path
import json
from typing import Any, Dict, Tuple

ROOT = Path(__file__).resolve().parents[1]
TEAMS_FILE = ROOT / "app" / "teams.json"

class TeamRuntimeError(ValueError):
    pass

def _load_teams() -> Dict[str, Any]:
    try:
        data = json.loads(TEAMS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TeamRuntimeError("teams.json missing")
    except Exception as e:
        raise TeamRuntimeError(f"teams.json invalid: {e}")
    if not isinstance(data, dict):
        raise TeamRuntimeError("teams.json must be an object {TEAM_ID: {...}}")
    # normalizuj klucze do UPPER dla bezpieczeństwa
    out: Dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(k, str):
            out[k.strip().upper()] = v
    return out

def apply_team_runtime(payload: Dict[str, Any], mode: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if payload is None:
        payload = {}

    team_id = payload.get("team") or payload.get("team_id") or "WRITER"
    team_id = str(team_id).strip().upper()

    mode_u = str(mode or "").strip().upper()
    if not mode_u:
        raise TeamRuntimeError("mode missing")

    teams = _load_teams()
    if team_id not in teams:
        raise TeamRuntimeError(f"Invalid team_id: {team_id}")

    team_cfg = teams[team_id] or {}
    allowed = team_cfg.get("allowed_modes") or []
    allowed_u = [str(m).strip().upper() for m in allowed if str(m).strip()]
    if allowed_u and mode_u not in allowed_u:
        raise TeamRuntimeError(f"Mode {mode_u} not allowed for team {team_id}")

    policy = team_cfg.get("policy") or {}
    if not isinstance(policy, dict):
        policy = {}

    # WSTRZYKNIJ runtime do payload => trafi do step.input
    payload["_team_id"] = team_id
    payload["_team_policy_id"] = policy.get("policy_id") or policy.get("id") or team_cfg.get("policy_id")
    payload["_team_model"] = policy.get("model")

    meta = {
        "team_id": team_id,
        "mode": mode_u,
        "allowed_modes": allowed_u,
        "policy": policy,
    }
    return payload, meta

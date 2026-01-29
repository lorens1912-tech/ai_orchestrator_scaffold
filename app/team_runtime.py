from __future__ import annotations

from pathlib import Path
import json
from typing import Any, Dict, Tuple, List, Optional

ROOT = Path(__file__).resolve().parents[1]
TEAMS_FILE = ROOT / "app" / "teams.json"

class TeamRuntimeError(ValueError):
    pass

class InvalidTeamId(TeamRuntimeError):
    pass

class ModeNotAllowed(TeamRuntimeError):
    pass

def _normalize_team_id(x: Any) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return s.upper() if s else None

def _load_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TeamRuntimeError("TEAM_ROUTER: teams.json missing")
    except Exception as e:
        raise TeamRuntimeError(f"TEAM_ROUTER: teams.json invalid: {e}")

def _load_teams() -> Dict[str, Dict[str, Any]]:
    data = _load_json(TEAMS_FILE)
    teams: Dict[str, Dict[str, Any]] = {}

    # --- PRIORITY: wrapper {"teams": {...}} or {"teams":[...]} ---
    if isinstance(data, dict) and "teams" in data:
        t = data.get("teams")

        # wrapper dict: {"teams": {"WRITER": {...}, "CRITIC": {...}}}
        if isinstance(t, dict):
            for k, v in t.items():
                tid = _normalize_team_id(k)
                if not tid:
                    continue
                teams[tid] = v if isinstance(v, dict) else {"_raw": v}
            if teams:
                return teams

        # wrapper list: {"teams": [{"id":"WRITER", ...}, ...]}
        if isinstance(t, list):
            for item in t:
                if not isinstance(item, dict):
                    continue
                tid = _normalize_team_id(item.get("id") or item.get("team_id") or item.get("name"))
                if not tid:
                    continue
                teams[tid] = item
            if teams:
                return teams

        raise TeamRuntimeError("TEAM_ROUTER: teams.json contains 'teams' but format unsupported")

    # --- Format A: direct dict {"WRITER": {...}} (skip metadata keys) ---
    if isinstance(data, dict):
        META_KEYS = {"version", "defaults", "policies", "schema", "_comment", "comment", "meta"}
        for k, v in data.items():
            if not isinstance(k, str):
                continue
            if k.strip().lower() in META_KEYS:
                continue
            tid = _normalize_team_id(k)
            if not tid:
                continue
            if isinstance(v, dict):
                teams[tid] = v
        if teams:
            return teams

    # --- Format B: list [{"id":"WRITER",...}] ---
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            tid = _normalize_team_id(item.get("id") or item.get("team_id") or item.get("name"))
            if not tid:
                continue
            teams[tid] = item
        if teams:
            return teams

    raise TeamRuntimeError("TEAM_ROUTER: teams.json has unsupported format")

def apply_team_runtime(payload: Dict[str, Any], mode: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if payload is None:
        payload = {}

    team_id = _normalize_team_id(payload.get("team") or payload.get("team_id")) or "WRITER"
    mode_u = _normalize_team_id(mode) or _normalize_team_id(payload.get("mode")) or None

    # jeśli mode brak — nie wywalaj 400 (kompatybilność ze starymi ścieżkami/testami)
    if not mode_u:
        meta = {"team_id": team_id, "mode": None, "skipped": True, "reason": "mode missing"}
        return payload, meta

    teams = _load_teams()
    if team_id not in teams:
        raise InvalidTeamId(f"TEAM_ROUTER: Invalid team_id: {team_id}")

    team_cfg = teams[team_id] or {}

    allowed = (
        team_cfg.get("allowed_modes")
        or team_cfg.get("allowed_mode_ids")
        or team_cfg.get("modes")
        or []
    )
    allowed_u: List[str] = [_normalize_team_id(x) for x in allowed if _normalize_team_id(x)]
    if allowed_u and mode_u not in allowed_u:
        raise ModeNotAllowed(f"TEAM_ROUTER: Mode {mode_u} not allowed for team {team_id}")

    policy = team_cfg.get("policy") or {}
    if not isinstance(policy, dict):
        policy = {}

    # telemetry do step.input
    payload["_team_id"] = team_id
    payload["_team_policy_id"] = policy.get("policy_id") or policy.get("id") or team_cfg.get("policy_id")
    payload["_team_model"] = policy.get("model")

    meta = {
        "team_id": team_id,
        "mode": mode_u,
        "allowed_modes": allowed_u,
        "policy": policy,
        "skipped": False,
    }
    return payload, meta

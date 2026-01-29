from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TEAMS_JSON = ROOT / "app" / "teams.json"
PROMPTS_DIR = ROOT / "prompts" / "teams"


@dataclass(frozen=True)
class TeamPolicy:
    policy_id: str
    model: str
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class TeamContext:
    team_id: str
    mode: str
    policy: TeamPolicy
    prompts: Dict[str, str]          # {"system": "...", "mode": "..."}
    prompt_id: str                  # e.g. "teams/WRITER/WRITE.txt"


def _load_teams() -> Dict[str, Any]:
    if not TEAMS_JSON.exists():
        return {"version": 0, "teams": {}}
    try:
        data = json.loads(TEAMS_JSON.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 0, "teams": {}}
        if not isinstance(data.get("teams"), dict):
            data["teams"] = {}
        return data
    except Exception:
        return {"version": 0, "teams": {}}


def _read_prompt_file(team_id: str, filename: str) -> Tuple[str, str]:
    """
    Returns (prompt_text, prompt_id).
    prompt_id is a stable identifier for audits.
    """
    p = PROMPTS_DIR / team_id / filename
    if not p.exists():
        return ("", "")
    try:
        txt = p.read_text(encoding="utf-8").strip()
        pid = f"teams/{team_id}/{filename}"
        return (txt, pid)
    except Exception:
        return ("", "")


def get_team_prompts(team_id: str, mode: str) -> Tuple[Dict[str, str], str]:
    """
    Loads:
      - system.txt (if exists)
      - <MODE>.txt (if exists) else empty
    Returns ({"system":..., "mode":...}, prompt_id_prefer_mode_else_system)
    """
    team_id = (team_id or "").strip()
    mode = (mode or "").strip().upper()

    sys_txt, sys_id = _read_prompt_file(team_id, "system.txt")
    mode_txt, mode_id = _read_prompt_file(team_id, f"{mode}.txt") if mode else ("", "")

    prompts = {"system": sys_txt, "mode": mode_txt}
    prompt_id = mode_id or sys_id or ""
    return prompts, prompt_id


def resolve_team_context(team_id: str, mode: str) -> TeamContext:
    """
    Deterministic resolver:
      - validates team_id exists in app/teams.json
      - validates mode allowed for that team
      - returns policy + prompts bundle
    Raises ValueError on invalid team/mode.
    """
    mode_u = (mode or "").strip().upper()
    team_u = (team_id or "").strip().upper()
    cfg = _load_teams()
    teams = cfg.get("teams", {})

    if team_u not in teams:
        raise ValueError(f"Unknown team_id: {team_u}")

    tcfg = teams[team_u]
    allowed = tcfg.get("allowed_modes", [])
    if isinstance(allowed, list) and allowed:
        if mode_u not in [str(x).upper() for x in allowed]:
            raise ValueError(f"Team {team_u} cannot run mode {mode_u}")

    dp = tcfg.get("default_policy", {}) if isinstance(tcfg, dict) else {}
    policy = TeamPolicy(
        policy_id=str(dp.get("policy_id") or f"POLICY_{team_u}_v1"),
        model=str(dp.get("model") or "gpt-4.1-mini"),
        temperature=float(dp.get("temperature") if dp.get("temperature") is not None else 0.7),
        max_tokens=int(dp.get("max_tokens") if dp.get("max_tokens") is not None else 1200),
    )

    prompts, prompt_id = get_team_prompts(team_u, mode_u)
    return TeamContext(team_id=team_u, mode=mode_u, policy=policy, prompts=prompts, prompt_id=prompt_id)


def try_resolve_team_context(team_id: str, mode: str) -> Optional[TeamContext]:
    try:
        return resolve_team_context(team_id, mode)
    except Exception:
        return None

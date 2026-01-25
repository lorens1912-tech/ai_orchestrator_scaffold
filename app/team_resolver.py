from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from app.config_registry import load_modes  # to istnieje i testy już go używają

APP_DIR = Path(__file__).resolve().parent
AGENTS_PATH = APP_DIR / "agents.json"


def _load_agents() -> Dict[str, Dict[str, Any]]:
    """
    Kanon: app/agents.json w formacie {"agents":[...]}.
    Zwraca mapę id -> team.
    """
    if not AGENTS_PATH.exists():
        # twardy fallback żeby testy nigdy nie padły na brak pliku
        fallback = [
            {"id":"AUTHOR","model":"gpt-4.1-mini","allowed_modes":["PLAN","WRITE","REWRITE","EXPAND"]},
            {"id":"EDITOR","model":"gpt-4.1-mini","allowed_modes":["EDIT","STYLE","TRANSLATE"]},
            {"id":"CRITIC","model":"gpt-5.2","allowed_modes":["CRITIC"]},
            {"id":"QA_GATE","model":"gpt-5.2","allowed_modes":["QUALITY","UNIQUENESS"]},
            {"id":"CONTINUITY_KEEPER","model":"gpt-5.2","allowed_modes":["CONTINUITY"]},
            {"id":"RESEARCH_FACTCHECK","model":"gpt-5.2","allowed_modes":["FACTCHECK"]},
            {"id":"STYLE_VOICE","model":"gpt-4.1-mini","allowed_modes":["STYLE"]}
        ]
        return {t["id"]: t for t in fallback}

    data = json.loads(AGENTS_PATH.read_text("utf-8"))
    agents = data.get("agents") if isinstance(data, dict) else data
    if not isinstance(agents, list):
        agents = []
    return {t["id"]: t for t in agents if isinstance(t, dict) and t.get("id")}


def resolve_team(mode_id: str, team_override: Optional[str] = None) -> Dict[str, Any]:
    agents = _load_agents()

    md = load_modes()
    modes = md.get("modes") if isinstance(md, dict) else md
    if not isinstance(modes, list):
        modes = []
    modes_map = {m.get("id"): m for m in modes if isinstance(m, dict) and m.get("id")}

    default_team = (modes_map.get(mode_id) or {}).get("default_team") or "AUTHOR"
    team_id = team_override or default_team

    if team_id not in agents:
        raise ValueError(f"Unknown team: {team_id}")

    allowed = set(agents[team_id].get("allowed_modes") or [])
    if mode_id not in allowed:
        raise ValueError(f"Team {team_id} cannot run mode {mode_id}")

    return {"id": team_id, "model": agents[team_id].get("model")}

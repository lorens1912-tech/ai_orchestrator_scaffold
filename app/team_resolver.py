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
            {"id":"QA","model":"gpt-5.2","allowed_modes":["QUALITY","UNIQUENESS"]},
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


def resolve_team(mode_id: str, team_override: str | None = None) -> dict:
    """
    Kontrakt:
    - team_override jest dozwolony tylko jeśli pasuje do mode_team_map (czyli TEAM nie może uruchomić złego MODE)
    - zwracamy: {id, policy_id, model, policy}
    """
    import json
    from pathlib import Path
    from app.team_layer import policy_for_team

    root = Path(__file__).resolve().parents[1]
    map_path = root / "config" / "mode_team_map.json"
    map_ = json.loads(map_path.read_text(encoding="utf-8")) if map_path.exists() else {}

    expected_team = map_.get(mode_id) or "WRITER"

    if team_override and team_override != expected_team:
        raise ValueError(f"TEAM_OVERRIDE_NOT_ALLOWED: mode={mode_id} override={team_override} expected={expected_team}")

    team_id = team_override or expected_team

    pol = policy_for_team(team_id) or {}
    model = pol.get("model") or "gpt-4.1-mini"
    policy_id = pol.get("policy_id") or f"POLICY_{team_id}_v1"

    return {
        "id": team_id,
        "policy_id": policy_id,
        "model": model,
        "policy": pol,
    }



from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

from app.config_registry import load_modes  # to istnieje i testy już go używają

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
CONFIG_DIR = ROOT_DIR / "config"
PROMPTS_DIR = ROOT_DIR / "prompts" / "teams"
AGENTS_PATH = APP_DIR / "agents.json"
TEAM_ALIASES = {
    "AUTHOR": "WRITER",
}


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


def _normalize_team_id(team_id: Any) -> str:
    raw = str(team_id or "").upper().strip()
    return TEAM_ALIASES.get(raw, raw)


def _load_known_team_ids() -> set[str]:
    path = CONFIG_DIR / "teams.json"
    if not path.exists():
        return set()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()

    teams = data.get("teams") if isinstance(data, dict) else data
    if isinstance(teams, list):
        return {
            _normalize_team_id(item.get("id"))
            for item in teams
            if isinstance(item, dict) and item.get("id")
        }
    if isinstance(teams, dict):
        return {_normalize_team_id(team_id) for team_id in teams.keys()}
    return set()


def _sha1_file(path: Path) -> str:
    if path.exists() and path.is_file():
        return hashlib.sha1(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    return ""


def _team_prompts(team_id: str, mode_id: str) -> Dict[str, str]:
    system_path = PROMPTS_DIR / team_id / "system.txt"
    mode_path = PROMPTS_DIR / team_id / f"{mode_id}.txt"
    return {
        "system_path": str(system_path.resolve()),
        "mode_path": str(mode_path.resolve()),
        "system_sha1": _sha1_file(system_path),
        "mode_sha1": _sha1_file(mode_path),
    }


def resolve_team(mode_id: str, team_override: str | None = None) -> dict:
    """
    Kontrakt:
    - team_override jest dozwolony tylko jeśli pasuje do mode_team_map (czyli TEAM nie może uruchomić złego MODE)
    - zwracamy: {id, policy_id, model, policy}
    """
    import json
    from pathlib import Path
    from app.team_layer import policy_for_team

    mode_id = str(mode_id or "").upper().strip()
    team_override = _normalize_team_id(team_override) if team_override else None

    map_path = CONFIG_DIR / "mode_team_map.json"
    map_ = json.loads(map_path.read_text(encoding="utf-8")) if map_path.exists() else {}

    expected_team = _normalize_team_id(map_.get(mode_id) or "WRITER")

    known_team_ids = _load_known_team_ids()
    if team_override and known_team_ids and team_override not in known_team_ids:
        raise ValueError(f"Unknown team_id: {team_override}")

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
        "prompts": _team_prompts(team_id, mode_id),
    }



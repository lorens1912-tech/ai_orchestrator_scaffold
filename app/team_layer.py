from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from .config_registry import ConfigError

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"

TEAMS_JSON = CONFIG_DIR / "teams.json"
POLICIES_JSON = CONFIG_DIR / "policies.json"
CONTEXT_JSON = CONFIG_DIR / "context_access.json"


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Missing config file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"Config must be an object: {path}")
    return data


@lru_cache(maxsize=1)
def _teams_cfg() -> Dict[str, Any]:
    data = _load_json(TEAMS_JSON)
    if "mode_team_map" not in data or not isinstance(data["mode_team_map"], dict):
        raise ConfigError("teams.json missing mode_team_map")
    return data


@lru_cache(maxsize=1)
def _policies_cfg() -> Dict[str, Any]:
    data = _load_json(POLICIES_JSON)
    tp = data.get("team_policy")
    if tp is None or not isinstance(tp, dict):
        raise ConfigError("policies.json missing team_policy")
    return data


@lru_cache(maxsize=1)
def _context_cfg() -> Dict[str, Any]:
    data = _load_json(CONTEXT_JSON)
    tca = data.get("team_context_access")
    if tca is None or not isinstance(tca, dict):
        raise ConfigError("context_access.json missing team_context_access")
    return data


def team_for_mode(mode_id: str) -> str:
    m = _teams_cfg()["mode_team_map"]
    t = m.get(mode_id)
    if not isinstance(t, str) or not t.strip():
        raise ConfigError(f"No TEAM mapping for MODE={mode_id}")
    return t.strip().upper()


def policy_for_team(team_id: str) -> Dict[str, Any]:
    tp = _policies_cfg()["team_policy"]
    pol = tp.get(team_id)
    if pol is None:
        return {}
    if not isinstance(pol, dict):
        raise ConfigError(f"Invalid policy for TEAM={team_id}")
    return dict(pol)


def context_access_for_team(team_id: str) -> List[str]:
    tca = _context_cfg()["team_context_access"]
    acc = tca.get(team_id)
    if acc is None:
        return []
    if not isinstance(acc, list) or not all(isinstance(x, str) for x in acc):
        raise ConfigError(f"Invalid context list for TEAM={team_id}")
    return [x for x in acc if x.strip()]


def enforce_caller_team(caller_team: str | None, exec_team: str, mode_id: str) -> None:
    if not caller_team:
        return
    ct = caller_team.strip().upper()
    if ct and ct != exec_team:
        raise ConfigError(f"TEAM={ct} cannot run MODE={mode_id}")


def filter_payload_for_context(current: Dict[str, Any], allow: List[str]) -> Dict[str, Any]:
    """
    allow = lista symbolicznych nazw kontekstu.
    Mapujemy je na realne klucze w payloadzie.
    """
    out: Dict[str, Any] = {}

    # zawsze przenosimy techniczne
    for k in ("_run_id", "_book_id", "_last_step_path", "_last_mode"):
        if k in current:
            out[k] = current[k]

    # "topic/constraints/max_tokens" jako część wejścia autora
    for k in ("topic", "constraints", "max_tokens", "model", "input", "prompt"):
        if k in current:
            out[k] = current[k]

    def put(sym: str, key: str) -> None:
        if sym in allow and key in current:
            out[key] = current[key]

    # standardowe
    put("kernel", "kernel")
    put("project_profile", "project_profile")
    put("book_bible", "book_bible")
    put("series_bible", "series_bible")
    put("scene_list", "scene_list")
    put("claims", "claims")

    # “last_text” mapujemy na realny klucz "text"
    if "last_text" in allow and "text" in current:
        out["text"] = current["text"]

    # “issues” mapujemy na ISSUES
    if "issues" in allow:
        if "ISSUES" in current:
            out["ISSUES"] = current["ISSUES"]
        if "issues" in current:
            out["issues"] = current["issues"]

    # task (dla ORCHESTRATOR)
    if "task" in allow and "task" in current:
        out["task"] = current["task"]

    return out

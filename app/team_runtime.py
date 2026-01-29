from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TEAMS_JSON = ROOT / "app" / "teams.json"
PROMPTS_DIR = ROOT / "prompts" / "teams"


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def _load_teams_cfg() -> Any:
    if not TEAMS_JSON.exists():
        raise ValueError(f"teams.json missing: {TEAMS_JSON.as_posix()}")
    try:
        return json.loads(TEAMS_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"teams.json invalid JSON: {e}")


def _normalize_teams(cfg: Any) -> Dict[str, Dict[str, Any]]:
    """
    Obsługuje różne formaty:
    - {"teams":[{...},{...}]}
    - {"WRITER":{...}, "CRITIC":{...}}
    - [{...},{...}]
    Zwraca mapę: TEAM_ID -> team_dict
    """
    teams_obj = cfg
    if isinstance(cfg, dict) and "teams" in cfg:
        teams_obj = cfg["teams"]

    out: Dict[str, Dict[str, Any]] = {}

    if isinstance(teams_obj, dict):
        for k, v in teams_obj.items():
            if isinstance(v, dict):
                tid = str(v.get("id") or k).strip().upper()
                out[tid] = dict(v, id=tid)
        return out

    if isinstance(teams_obj, list):
        for it in teams_obj:
            if not isinstance(it, dict):
                continue
            tid = str(it.get("id") or "").strip().upper()
            if tid:
                out[tid] = dict(it, id=tid)
        return out

    raise ValueError("teams.json has unsupported structure")


def _extract_policy(team: Dict[str, Any]) -> Tuple[str, str, float, int]:
    """
    Zwraca: (policy_id, model, temperature, max_tokens)
    Obsługuje:
    - team["policy"] jako dict
    - team["model"]/["temperature"]/["max_tokens"]
    """
    policy = team.get("policy")
    if isinstance(policy, dict):
        policy_id = str(policy.get("policy_id") or policy.get("id") or team.get("policy_id") or "").strip() or f"POLICY_{team['id']}_v1"
        model = str(policy.get("model") or team.get("model") or "gpt-4.1-mini").strip()
        temperature = float(policy.get("temperature", team.get("temperature", 0.7)))
        max_tokens = int(policy.get("max_tokens", team.get("max_tokens", 1200)))
        return policy_id, model, temperature, max_tokens

    policy_id = str(team.get("policy_id") or "").strip() or f"POLICY_{team['id']}_v1"
    model = str(team.get("model") or "gpt-4.1-mini").strip()
    temperature = float(team.get("temperature", 0.7))
    max_tokens = int(team.get("max_tokens", 1200))
    return policy_id, model, temperature, max_tokens


def _allowed_modes(team: Dict[str, Any]) -> Optional[list]:
    am = team.get("allowed_modes")
    if isinstance(am, list):
        return [str(x).strip().upper() for x in am if str(x).strip()]
    return None


def _prompt_paths(team_id: str, mode: str) -> Tuple[Path, Path]:
    base = PROMPTS_DIR / team_id
    system_p = base / "system.txt"
    mode_p = base / f"{mode}.txt"
    return system_p, mode_p


def resolve_team_context(*, team_id: str, mode: str, strict: bool = False) -> Dict[str, Any]:
    """
    Deterministycznie rozwiązuje team → policy + prompty.
    strict=True => jeśli allowed_modes jest zdefiniowane i mode nie pasuje, podnosimy błąd.
    """
    tid = str(team_id or "").strip().upper() or "WRITER"
    m = str(mode or "").strip().upper() or "PRESET"

    cfg = _load_teams_cfg()
    teams = _normalize_teams(cfg)

    if tid not in teams:
        raise ValueError(f"unknown team_id: {tid}")

    team = teams[tid]
    allowed = _allowed_modes(team)

    if strict and allowed is not None and m not in allowed:
        raise ValueError(f"mode {m} not allowed for team {tid}")

    policy_id, model, temperature, max_tokens = _extract_policy(team)

    system_p, mode_p = _prompt_paths(tid, m)
    system_txt = _read_text(system_p)
    mode_txt = _read_text(mode_p)

    prompts = {
        "system_path": system_p.as_posix(),
        "mode_path": mode_p.as_posix(),
        "system_sha1": _sha1(system_txt),
        "mode_sha1": _sha1(mode_txt),
        "system_exists": system_p.exists(),
        "mode_exists": mode_p.exists(),
    }

    return {
        "id": tid,
        "mode": m,
        "strict": bool(strict),
        "allowed_modes": allowed or [],
        "policy_id": policy_id,
        "model": model,
        "policy": {
            "policy_id": policy_id,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        "prompts": prompts,
    }

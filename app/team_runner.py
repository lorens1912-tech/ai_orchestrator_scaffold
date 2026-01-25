from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]

def _read_text(rel_path: str) -> str:
    p = (ROOT / rel_path).resolve()
    return p.read_text("utf-8")

def _load_teams_cfg() -> Dict[str, Any]:
    p = ROOT / "config" / "teams.json"
    if not p.exists():
        return {"teams": []}
    return json.loads(p.read_text("utf-8"))

def _team_cfg(team_id: str) -> Dict[str, Any]:
    cfg = _load_teams_cfg()
    for t in (cfg.get("teams") or []):
        if isinstance(t, dict) and t.get("id") == team_id:
            return t
    return {}

def _validate_contract(contract: str, data: Dict[str, Any]) -> None:
    # Minimalna walidacja, żeby nie przepuszczać śmieci.
    if contract == "CRITIC_JSON_V1":
        if "ISSUES" not in data or not isinstance(data["ISSUES"], list):
            raise ValueError("CRITIC contract: missing ISSUES[]")
        if "SUMMARY" not in data or not isinstance(data["SUMMARY"], str):
            raise ValueError("CRITIC contract: missing SUMMARY")
    elif contract == "QA_JSON_V1":
        if data.get("DECISION") not in {"ACCEPT","REVISE","REJECT"}:
            raise ValueError("QA contract: DECISION invalid/missing")
        if "REASONS" not in data or not isinstance(data["REASONS"], list):
            raise ValueError("QA contract: missing REASONS[]")
        if "MUST_FIX" not in data or not isinstance(data["MUST_FIX"], list):
            raise ValueError("QA contract: missing MUST_FIX[]")

def run_team_llm(team_id: str, user_text: str, *, model: Optional[str]=None, temperature: Optional[float]=None) -> Dict[str, Any]:
    from app.llm_provider_openai import call_text  # lokalny import (unikamy cykli)

    tcfg = _team_cfg(team_id)
    sys_path = tcfg.get("system_prompt_path")
    contract = tcfg.get("output_contract")
    if not sys_path or not contract:
        raise ValueError(f"Missing team config for {team_id}")

    sys_prompt = _read_text(sys_path).strip()
    req_model = model or tcfg.get("default_model") or "gpt-5"

    prompt = sys_prompt + "\n\nTEXT:\n" + (user_text or "")
    r = call_text(prompt=prompt, model=req_model, temperature=temperature)

    raw = (r.get("text") or "").strip()
    try:
        data = json.loads(raw)
    except Exception as e:
        raise ValueError(f"LLM output not JSON for {team_id}: {e}; raw={raw[:200]}")

    _validate_contract(contract, data)
    # meta zawsze
    data["_meta"] = {
        "team_id": team_id,
        "requested_model": req_model,
        "effective_model": r.get("effective_model") or req_model,
    }
    return data

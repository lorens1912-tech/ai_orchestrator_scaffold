from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config_registry import load_presets, load_modes
from app.team_resolver import resolve_team
from app.tools import TOOLS

ROOT = Path(__file__).resolve().parents[1]

TEXT_MODES = {
    "CRITIC", "EDIT", "REWRITE", "QUALITY", "UNIQUENESS",
    "CONTINUITY", "FACTCHECK", "STYLE", "TRANSLATE", "EXPAND",
}

def _iso() -> str:
    return datetime.utcnow().isoformat()

def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

def _preset_list() -> List[Dict[str, Any]]:
    pd = load_presets()
    presets = pd.get("presets") if isinstance(pd, dict) else pd
    if not isinstance(presets, list):
        raise ValueError("presets must be a list")
    out: List[Dict[str, Any]] = []
    for p in presets:
        if isinstance(p, dict) and p.get("id"):
            out.append(p)
    return out

def _find_preset_obj(preset_id: str) -> Optional[Dict[str, Any]]:
    pid = str(preset_id or "").strip()
    if not pid:
        return None
    for p in _preset_list():
        if str(p.get("id")) == pid:
            return p
    return None

def _preset_modes(preset_id: str) -> List[str]:
    p = _find_preset_obj(preset_id)
    if not isinstance(p, dict):
        raise ValueError(f"Unknown preset: {preset_id}")
    modes = p.get("modes") or []
    if not isinstance(modes, list):
        raise ValueError("preset.modes must be a list")
    return [str(x).upper() for x in modes]

def _known_mode_ids() -> set:
    md = load_modes()
    modes = md.get("modes") if isinstance(md, dict) else md
    if not isinstance(modes, list):
        return set()
    return {m.get("id") for m in modes if isinstance(m, dict) and m.get("id")}

def resolve_modes(arg1: Any, arg2: Any = None) -> Tuple[List[str], Optional[str], Dict[str, Any]]:
    """
    Testy używają: resolve_modes(None, "WRITING_STANDARD")
    """
    if isinstance(arg2, str) and arg1 is None:
        preset_id = arg2
        payload: Dict[str, Any] = {}
        seq = _preset_modes(preset_id)
        return seq, preset_id, payload

    payload = arg1 if isinstance(arg1, dict) else arg2
    if not isinstance(payload, dict):
        raise TypeError("resolve_modes expects payload dict (or None, preset_id)")

    preset_id = payload.get("preset")
    if preset_id:
        seq = _preset_modes(str(preset_id))
        return seq, str(preset_id), payload

    mode = payload.get("mode")
    if not mode:
        raise ValueError("No mode or preset specified")

    known = _known_mode_ids()
    if known and mode not in known:
        raise ValueError(f"Unknown mode: {mode}")

    return [str(mode).upper()], None, payload

def _deep_quality_decision(result: Any) -> Optional[str]:
    # expected: {"payload":{"decision":"ACCEPT|REVISE|REJECT", ...}}
    if isinstance(result, dict):
        pl = result.get("payload")
        if isinstance(pl, dict):
            d = pl.get("decision")
            if isinstance(d, str) and d.strip():
                return d.strip().upper()
    return None

def _quality_retry_cfg(preset_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not preset_id:
        return None
    p = _find_preset_obj(str(preset_id))
    if not isinstance(p, dict):
        return None
    cfg = p.get("quality_retry")
    return cfg if isinstance(cfg, dict) else None

def execute_stub(
    run_id: str,
    book_id: str,
    modes: List[str],
    payload: Dict[str, Any],
    steps: Optional[List[Any]] = None,
) -> List[str]:
    run_dir = ROOT / "runs" / run_id
    steps_dir = run_dir / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)

    state_path = run_dir / "state.json"
    state = {"run_id": run_id, "latest_text": "", "last_step": 0, "created_at": _iso()}

    latest_text = ""
    artifact_paths: List[str] = []

    preset_id = (payload or {}).get("preset")
    retry_cfg = _quality_retry_cfg(str(preset_id)) if preset_id else None

    max_attempts = 0
    retry_on = {"REVISE", "REJECT"}
    edit_mode = "EDIT"
    if isinstance(retry_cfg, dict):
        try:
            max_attempts = int(retry_cfg.get("max_attempts") or 0)
        except Exception:
            max_attempts = 0
        on_list = retry_cfg.get("on")
        if isinstance(on_list, list) and on_list:
            retry_on = {str(x).upper() for x in on_list if isinstance(x, str)}
        em = retry_cfg.get("edit_mode")
        if isinstance(em, str) and em.strip():
            edit_mode = em.strip().upper()

    queue: List[str] = [str(m).upper() for m in (modes or [])]
    step_index = 0
    quality_attempts_used = 0

    while queue:
        mode_id = queue.pop(0).upper()
        step_index += 1

        team_override = (payload or {}).get("team_id")
        team = resolve_team(mode_id, team_override=team_override)

        tool_in = dict(payload or {})
        tool_in.setdefault("book_id", book_id)
        tool_in["_requested_model"] = team.get("model")

        if mode_id in TEXT_MODES:
            # IMPORTANT: after WRITE/EDIT, use latest_text (overwrite), but keep payload.text on first step
            if latest_text:
                tool_in["text"] = latest_text
            else:
                tool_in.setdefault("text", "")

        result = TOOLS[mode_id](tool_in)
        out_pl = (result.get("payload") or {})
        if isinstance(out_pl, dict) and out_pl.get("text"):
            latest_text = str(out_pl["text"])

        step_doc = {
            "run_id": run_id,
            "index": step_index,
            "mode": mode_id,
            "team": team,
            "input": tool_in,
            "result": result,
            "created_at": _iso(),
        }
        step_path = steps_dir / f"{step_index:03d}_{mode_id}.json"
        _atomic_write_json(step_path, step_doc)
        artifact_paths.append(str(step_path))

        if mode_id == "QUALITY" and max_attempts > 0:
            decision = _deep_quality_decision(result) or _deep_quality_decision(out_pl)
            if decision and decision in retry_on and quality_attempts_used < max_attempts:
                quality_attempts_used += 1
                queue = [edit_mode, "QUALITY"] + queue

    state["last_step"] = step_index
    state["latest_text"] = latest_text
    state["status"] = "DONE"
    state["completed_steps"] = step_index
    _atomic_write_json(state_path, state)

    book_dir = ROOT / "books" / book_id / "draft"
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "latest.txt").write_text(latest_text, encoding="utf-8")

    return artifact_paths

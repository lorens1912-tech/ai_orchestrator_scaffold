from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config_registry import load_presets, load_modes
from app.team_resolver import resolve_team
from app.tools import TOOLS

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path(__file__).resolve().parent
PRESETS_FILE = APP_DIR / "presets.json"

TEXT_MODES = {
    "CRITIC","EDIT","REWRITE","QUALITY","UNIQUENESS",
    "CONTINUITY","FACTCHECK","STYLE","TRANSLATE","EXPAND"
}

def _iso() -> str:
    return datetime.utcnow().isoformat()

def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

def _known_mode_ids() -> set:
    md = load_modes()
    modes = md.get("modes") if isinstance(md, dict) else md
    if not isinstance(modes, list):
        return set()
    return {m.get("id") for m in modes if isinstance(m, dict) and m.get("id")}

def _preset_modes(preset_id: str) -> List[str]:
    pd = load_presets()
    presets = pd.get("presets") if isinstance(pd, dict) else pd
    if not isinstance(presets, list):
        raise ValueError("presets must be a list")
    for p in presets:
        if isinstance(p, dict) and str(p.get("id")) == str(preset_id):
            return [str(x).upper() for x in (p.get("modes") or [])]
    raise ValueError(f"Unknown preset: {preset_id}")

def _load_presets_file_raw() -> Any:
    try:
        return json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _find_preset_raw(preset_id: str) -> Optional[Dict[str, Any]]:
    pid = str(preset_id or "").strip()
    if not pid:
        return None
    raw = _load_presets_file_raw()
    presets = raw.get("presets") if isinstance(raw, dict) else raw
    if not isinstance(presets, list):
        return None
    for p in presets:
        if isinstance(p, dict) and str(p.get("id")) == pid:
            return p
    return None

def _quality_retry_cfg_from_file(preset_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not preset_id:
        return None
    p = _find_preset_raw(str(preset_id))
    if not isinstance(p, dict):
        return None
    cfg = p.get("quality_retry")
    return cfg if isinstance(cfg, dict) else None

def resolve_modes(arg1: Any, arg2: Any = None) -> Tuple[List[str], Optional[str], Dict[str, Any]]:
    if isinstance(arg2, str) and arg1 is None:
        preset_id = arg2
        payload: Dict[str, Any] = {"preset": preset_id, "_preset_id": preset_id}
        seq = _preset_modes(preset_id)
        return seq, preset_id, payload

    payload = arg1 if isinstance(arg1, dict) else arg2
    if not isinstance(payload, dict):
        raise TypeError("resolve_modes expects payload dict (or None, preset_id)")

    preset_id = payload.get("preset")
    if preset_id:
        payload.setdefault("_preset_id", preset_id)
        seq = _preset_modes(str(preset_id))
        return seq, str(preset_id), payload

    mode = payload.get("mode")
    if not mode:
        raise ValueError("No mode or preset specified")

    known = _known_mode_ids()
    if known and mode not in known:
        raise ValueError(f"Unknown mode: {mode}")

    return [str(mode).upper()], None, payload

def _decision_from_quality_result(result: Any) -> Optional[str]:
    # Expected from tool_quality: {"payload": {"decision": "..."}}
    if isinstance(result, dict):
        pl = result.get("payload")
        if isinstance(pl, dict):
            d = pl.get("decision")
            if isinstance(d, str) and d.strip():
                return d.strip().upper()
    return None

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

    preset_id = None
    if isinstance(payload, dict):
        preset_id = payload.get("_preset_id") or payload.get("preset")

    retry_cfg = _quality_retry_cfg_from_file(str(preset_id)) if preset_id else None
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
    used = 0

    while queue:
        mode_id = queue.pop(0).upper()
        step_index += 1

        team_override = (payload or {}).get("team_id")
        team = resolve_team(mode_id, team_override=team_override)

        tool_in = dict(payload or {})
        tool_in.setdefault("book_id", book_id)
        tool_in["_requested_model"] = team.get("model")

        if mode_id in TEXT_MODES:
            tool_in["text"] = latest_text if latest_text else str(tool_in.get("text") or "")

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
            decision = _decision_from_quality_result(result)
            if decision and (decision in retry_on) and (used < max_attempts):
                used += 1
                queue = [edit_mode, "QUALITY"] + queue

    state["last_step"] = step_index
    state["completed_steps"] = step_index
    state["latest_text"] = latest_text
    state["status"] = "DONE"
    _atomic_write_json(state_path, state)

    book_dir = ROOT / "books" / book_id / "draft"
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "latest.txt").write_text(latest_text, encoding="utf-8")

    return artifact_paths

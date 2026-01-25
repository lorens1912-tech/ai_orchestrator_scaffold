from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config_registry import load_presets, load_modes
from app.team_resolver import resolve_team
from app.tools import TOOLS

ROOT = Path(__file__).resolve().parents[1]

def _iso() -> str:
    return datetime.utcnow().isoformat()

def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

def _preset_modes(preset_id: str) -> List[str]:
    pd = load_presets()
    presets = pd.get("presets") if isinstance(pd, dict) else pd
    if not isinstance(presets, list):
        raise ValueError("presets must be a list")
    for p in presets:
        if isinstance(p, dict) and p.get("id") == preset_id:
            return list(p.get("modes") or [])
    raise ValueError(f"Unknown preset: {preset_id}")

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
        payload = {}
        seq = _preset_modes(preset_id)
        return seq, preset_id, payload

    payload = arg1 if isinstance(arg1, dict) else arg2
    if not isinstance(payload, dict):
        raise TypeError("resolve_modes expects payload dict (or None, preset_id)")

    preset_id = payload.get("preset")
    if preset_id:
        seq = _preset_modes(preset_id)
        return seq, preset_id, payload

    mode = payload.get("mode")
    if not mode:
        raise ValueError("No mode or preset specified")

    known = _known_mode_ids()
    if known and mode not in known:
        raise ValueError(f"Unknown mode: {mode}")

    return [mode], None, payload

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

    for idx, mode_id in enumerate(modes, start=1):
        team_override = (payload or {}).get("team_id")
        team = resolve_team(mode_id, team_override=team_override)

        tool_in = dict(payload or {})
        tool_in.setdefault("book_id", book_id)
        tool_in["_requested_model"] = team.get("model")

        if mode_id in ("CRITIC","EDIT","REWRITE","QUALITY","UNIQUENESS","CONTINUITY","FACTCHECK","STYLE","TRANSLATE","EXPAND"):
            tool_in.setdefault("text", latest_text)

        result = TOOLS[mode_id](tool_in)
        out_pl = (result.get("payload") or {})
        if isinstance(out_pl, dict) and out_pl.get("text"):
            latest_text = str(out_pl["text"])

        step_doc = {
            "run_id": run_id,
            "index": idx,
            "mode": mode_id,
            "team": team,
            "input": tool_in,
            "result": result,
            "created_at": _iso(),
        }
        step_path = steps_dir / f"{idx:03d}_{mode_id}.json"
        _atomic_write_json(step_path, step_doc)
        artifact_paths.append(str(step_path))

    state["last_step"] = len(modes)
    state["latest_text"] = latest_text
    state["status"] = "DONE"
    _atomic_write_json(state_path, state)

    book_dir = ROOT / "books" / book_id / "draft"
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "latest.txt").write_text(latest_text, encoding="utf-8")

    return artifact_paths

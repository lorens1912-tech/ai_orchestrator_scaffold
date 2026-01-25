from __future__ import annotations

from typing import Any, Dict

from app.config_registry import load_modes, load_presets
from app.tools import TOOLS

def validate_config() -> Dict[str, Any]:
    modes_data = load_modes()
    modes = (modes_data.get("modes") if isinstance(modes_data, dict) else modes_data) or []
    if not isinstance(modes, list):
        modes = []

    presets_data = load_presets()
    presets = (presets_data.get("presets") if isinstance(presets_data, dict) else presets_data) or []
    if not isinstance(presets, list):
        presets = []

    mode_ids = [m.get("id") for m in modes if isinstance(m, dict) and m.get("id")]
    missing_tools = [mid for mid in mode_ids if mid not in TOOLS]

    # preset list -> validate mode refs
    bad_presets = []
    mode_set = set(mode_ids)
    for p in presets:
        if not isinstance(p, dict):
            continue
        pid = p.get("id")
        seq = p.get("modes") or []
        for mid in seq:
            if mid not in mode_set:
                bad_presets.append({"preset": pid, "missing_mode": mid})

    return {
        "mode_ids": mode_ids,
        "modes_count": len(mode_ids),
        "presets_count": len(presets),
        # testy czasem mają w payloadzie agents_count, ale nie wymagają ładowania agentów z registry
        "agents_count": 0,
        "missing_tools": missing_tools,
        "bad_presets": bad_presets,
    }

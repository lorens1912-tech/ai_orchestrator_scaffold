from __future__ import annotations
from typing import Any, Dict, Tuple

def _d(v: Any) -> Dict[str, Any]:
    return dict(v) if isinstance(v, dict) else {}

def _norm_key(v: Any) -> str:
    return str(v).strip().upper()

def _to_bool(v: Any, default: bool = True) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default

def resolve_policy_for_scope(
    current_policy: Dict[str, Any] | None,
    preset: str | None,
    mode: str | None,
    flags: Dict[str, Any] | None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    cp = _d(current_policy)
    fl = _d(flags)

    preset_key = _norm_key(preset) if preset else ""
    mode_key = _norm_key(mode) if mode else ""

    enabled_global = _to_bool(fl.get("enabled_global", True), True)

    enabled_by_preset_raw = _d(fl.get("enabled_by_preset"))
    enabled_by_mode_raw = _d(fl.get("enabled_by_mode"))
    enabled_by_preset = {_norm_key(k): _to_bool(v, True) for k, v in enabled_by_preset_raw.items()}
    enabled_by_mode = {_norm_key(k): _to_bool(v, True) for k, v in enabled_by_mode_raw.items()}

    enabled_preset = enabled_by_preset.get(preset_key, True) if preset_key else True
    enabled_mode = enabled_by_mode.get(mode_key, True) if mode_key else True
    enabled = enabled_global and enabled_preset and enabled_mode

    overrides_global = _d(fl.get("overrides_global"))
    overrides_by_preset_all = _d(fl.get("overrides_by_preset"))
    overrides_by_mode_all = _d(fl.get("overrides_by_mode"))

    overrides_preset = _d(overrides_by_preset_all.get(preset_key))
    overrides_mode = _d(overrides_by_mode_all.get(mode_key))

    resolved = dict(cp)
    source = "current_policy"

    if overrides_global:
        resolved.update(overrides_global)
        source = "global"

    if overrides_preset:
        resolved.update(overrides_preset)
        source = "preset"

    if overrides_mode:
        resolved.update(overrides_mode)
        source = "mode"

    telemetry = {
        "enabled": enabled,
        "policy_source": source,
        "preset": preset_key or None,
        "mode": mode_key or None,
        "resolution_order": ["current_policy", "global", "preset", "mode"],
        "applied_values": {
            "global": overrides_global,
            "preset": overrides_preset,
            "mode": overrides_mode,
        },
        "enabled_checks": {
            "global": enabled_global,
            "preset": enabled_preset,
            "mode": enabled_mode,
        },
    }

    return resolved, telemetry

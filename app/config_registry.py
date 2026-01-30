from __future__ import annotations

from pathlib import Path
import json
from typing import Any, Dict, List

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


class ConfigError(Exception):
    pass


APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
CONFIG_DIR = ROOT_DIR / "config"

APP_KERNEL_JSON = APP_DIR / "kernel.json"
APP_MODES_JSON = APP_DIR / "modes.json"
APP_PRESETS_JSON = APP_DIR / "presets.json"

CFG_KERNEL_YAML = CONFIG_DIR / "kernel.yaml"


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise ConfigError(f"Missing JSON config file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}") from e


def _load_yaml(path: Path) -> Any:
    if yaml is None:
        raise ConfigError("PyYAML not available but YAML config requested.")
    if not path.exists():
        raise ConfigError(f"Missing YAML config file: {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e


def load_kernel() -> Dict[str, Any]:
    # kernel może być z YAML jeśli jest i mamy pyyaml, inaczej JSON
    if CFG_KERNEL_YAML.exists() and yaml is not None:
        data = _load_yaml(CFG_KERNEL_YAML)
    else:
        data = _load_json(APP_KERNEL_JSON)

    if not isinstance(data, dict):
        raise ConfigError("kernel must be an object/dict")
    return data


def load_modes() -> Dict[str, Any]:
    data = _load_json(APP_MODES_JSON)

    # wspiera {"modes":[...]} oraz listę
    if isinstance(data, dict) and "modes" in data:
        modes = data["modes"]
    else:
        modes = data

    if not isinstance(modes, list):
        raise ConfigError("modes must be a list")

    ids: List[str] = []
    for m in modes:
        if not isinstance(m, dict) or "id" not in m:
            raise ConfigError("each mode must be an object with 'id'")
        ids.append(str(m["id"]))

    if len(ids) != len(set(ids)):
        raise ConfigError("Duplicate MODE id detected")

    return {"modes": modes, "mode_ids": ids, "modes_count": len(modes)}


def load_presets() -> Dict[str, Any]:
    data = _load_json(APP_PRESETS_JSON)

    # wspiera {"presets":[...]} oraz listę
    if isinstance(data, dict) and "presets" in data:
        presets = data["presets"]
    else:
        presets = data

    if not isinstance(presets, list):
        raise ConfigError("presets must be a list")

    ids: List[str] = []
    for p in presets:
        if not isinstance(p, dict) or "id" not in p:
            raise ConfigError("each preset must be an object with 'id'")
        ids.append(str(p["id"]))

    if len(ids) != len(set(ids)):
        raise ConfigError("Duplicate PRESET id detected")

    return {"presets": presets, "preset_ids": ids, "presets_count": len(presets)}


def validate_all() -> Dict[str, Any]:
    """
    Validation bundle for configs.
    Always includes:
      - ok: bool
      - errors: list[str]
    """
    try:
        kernel = load_kernel()
        modes = load_modes()
        presets_raw = load_presets()

        # modes normalize
        mode_ids = []
        if isinstance(modes, dict):
            mm = modes.get("modes")
            if isinstance(mm, list):
                mode_ids = [str(x.get("id")).upper() for x in mm if isinstance(x, dict) and x.get("id")]
            else:
                mode_ids = [str(x).upper() for x in (modes.get("mode_ids") or [])]

        # presets normalize -> list of dicts with "id"
        if isinstance(presets_raw, dict) and isinstance(presets_raw.get("presets"), list):
            plist = presets_raw["presets"]
        elif isinstance(presets_raw, list):
            plist = presets_raw
        elif isinstance(presets_raw, dict):
            plist = [{"id": k, **(v if isinstance(v, dict) else {"value": v})} for k, v in presets_raw.items()]
        else:
            plist = []

        preset_ids = [str(x.get("id")) for x in plist if isinstance(x, dict) and x.get("id")]

        return {
            "ok": True,
            "errors": [],
            "modes_count": len(mode_ids) if mode_ids else (modes.get("modes_count") if isinstance(modes, dict) else None),
            "presets_count": len(preset_ids),
            "mode_ids": mode_ids,
            "preset_ids": preset_ids,
            "kernel": kernel,
            "modes": modes,
            "presets": {"presets": plist, "preset_ids": preset_ids, "presets_count": len(preset_ids)},
        }
    except Exception as e:
        return {"ok": False, "errors": [f"{type(e).__name__}: {e}"]}

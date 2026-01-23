from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, Dict, List


class ConfigError(RuntimeError):
    pass


BASE_DIR = Path(__file__).resolve().parent

KERNEL_PATH = BASE_DIR / "kernel.json"
MODES_PATH = BASE_DIR / "modes.json"
PRESETS_PATH = BASE_DIR / "presets.json"

EXPECTED_MODES_COUNT = int(os.getenv("EXPECTED_MODES_COUNT", "13"))


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise ConfigError(f"{path.name}: file not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ConfigError(f"{path.name}: invalid json: {e}")


def load_kernel() -> Dict[str, Any]:
    if not KERNEL_PATH.exists():
        return {"kernel": {"id": "KERNEL", "version": 1}}
    data = _read_json(KERNEL_PATH)
    if isinstance(data, dict):
        return data
    raise ConfigError("kernel.json: expected object")


def load_modes() -> Dict[str, Any]:
    data = _read_json(MODES_PATH)

    if isinstance(data, dict) and "modes" in data:
        modes = data.get("modes") or []
    elif isinstance(data, list):
        modes = data
    else:
        raise ConfigError("modes.json: invalid format")

    ids: List[str] = []

    for m in modes:
        if isinstance(m, str):
            mid = m.strip()
            if mid:
                ids.append(mid)
            continue

        if isinstance(m, dict):
            mid = str(m.get("id") or "").strip()
            if not mid:
                raise ConfigError("modes.json: mode missing id")
            ids.append(mid)
            continue

        raise ConfigError("modes.json: mode must be object or string")

    if len(set(ids)) != len(ids):
        raise ConfigError("modes.json: duplicate mode ids")

    if len(ids) != EXPECTED_MODES_COUNT:
        raise ConfigError(f"modes.json: expected {EXPECTED_MODES_COUNT} modes, got {len(ids)}")

    return {"modes": [{"id": x} for x in ids]}


def load_presets() -> Dict[str, Any]:
    data = _read_json(PRESETS_PATH)
    if not (isinstance(data, dict) and isinstance(data.get("presets"), list)):
        raise ConfigError("presets.json: invalid format (expected {'presets': [...]})")

    known_modes = {m["id"] for m in load_modes()["modes"]}
    presets = data["presets"]

    seen: set[str] = set()
    for p in presets:
        if not isinstance(p, dict):
            raise ConfigError("presets.json: preset must be object")

        pid = str(p.get("id") or "").strip()
        if not pid:
            raise ConfigError("presets.json: preset missing id")
        if pid in seen:
            raise ConfigError("presets.json: duplicate preset ids")
        seen.add(pid)

        pmodes = p.get("modes")
        if not isinstance(pmodes, list) or not pmodes:
            raise ConfigError(f"presets.json: preset {pid} has no modes")

        for mid in pmodes:
            if not isinstance(mid, str) or not mid.strip():
                raise ConfigError(f"presets.json: preset {pid} has invalid mode id")
            if mid not in known_modes:
                raise ConfigError(f"presets.json: preset {pid} references unknown mode: {mid}")

    return data


def validate_all() -> Dict[str, Any]:
    kernel = load_kernel()
    modes = load_modes()
    presets = load_presets()

    mode_ids = [m.get("id") for m in (modes.get("modes") or []) if isinstance(m, dict) and m.get("id")]
    preset_ids = [p.get("id") for p in (presets.get("presets") or []) if isinstance(p, dict) and p.get("id")]

    modes_count = len(mode_ids)
    presets_count = len(preset_ids)

    return {
        "ok": True,
        "kernel": kernel,
        "modes": modes,
        "presets": presets,
        "modes_count": int(modes_count),
        "presets_count": int(presets_count),
        "mode_ids": mode_ids,
        "preset_ids": preset_ids,
    }

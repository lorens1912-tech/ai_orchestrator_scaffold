from __future__ import annotations

from typing import Any, Dict, List, Tuple
from pathlib import Path

from .config_registry import load_modes, load_presets, ConfigError
from .run_store import run_dir, ensure_dir, atomic_write_json, relpath, read_json_if_exists
from .run_state import init_state, load_state, save_state, set_status
from .run_lock import acquire_book_lock
from .tools import TOOLS


def _mode_ids() -> List[str]:
    return [m["id"] for m in load_modes()["modes"]]


def resolve_modes(mode: str | None, preset: str | None) -> Tuple[List[str], str | None, str | None]:
    if not mode and not preset:
        raise ConfigError("mode or preset required")

    known = set(_mode_ids())

    if mode:
        if mode not in known:
            raise ConfigError(f"Unknown mode: {mode}")
        return [mode], mode, None

    presets = load_presets()["presets"]
    p = next((x for x in presets if x["id"] == preset), None)
    if not p:
        raise ConfigError(f"Unknown preset: {preset}")

    return p["modes"], None, preset


def _manifest_path(run_id: str) -> Path:
    return run_dir(run_id) / "run.json"


def write_manifest(run_id: str, book_id: str, modes: List[str], payload: Dict[str, Any]) -> None:
    base = run_dir(run_id)
    ensure_dir(base)
    atomic_write_json(
        _manifest_path(run_id),
        {
            "run_id": run_id,
            "book_id": book_id,
            "resolved_modes": modes,
            "payload": payload or {},
        },
    )


def load_manifest(run_id: str) -> Dict[str, Any]:
    m = read_json_if_exists(_manifest_path(run_id))
    if not m:
        raise ConfigError("Missing manifest")
    return m


def execute_stub(run_id: str, book_id: str, modes: List[str], payload: Dict[str, Any]) -> List[str]:
    with acquire_book_lock(book_id):
        base = run_dir(run_id)
        ensure_dir(base)

        st = load_state(run_id)
        if not st:
            st = init_state(run_id, len(modes))

        set_status(run_id, st, "RUNNING")

        artifacts: List[str] = []
        steps_dir = base / "steps"
        ensure_dir(steps_dir)

        current_payload: Dict[str, Any] = dict(payload or {})
        current_payload.setdefault("_run_id", run_id)
        current_payload.setdefault("_book_id", book_id)

        for idx, mode in enumerate(modes, start=1):
            tool = TOOLS.get(mode)
            if not tool:
                raise RuntimeError(f"No tool for mode {mode}")

            result = tool(current_payload)

            step_path = steps_dir / f"{idx:03d}_{mode}.json"
            atomic_write_json(step_path, {"index": idx, "mode": mode, "result": result})

            # meta o ostatnim kroku (przydaje się np. UNIQUENESS)
            current_payload["_last_step_path"] = relpath(step_path)
            current_payload["_last_mode"] = mode

            # MERGE: result.payload -> current_payload (kolejny krok widzi wynik poprzedniego)
            try:
                if isinstance(result, dict):
                    rp = result.get("payload")
                    if isinstance(rp, dict) and rp:
                        for k, v in rp.items():
                            if k == "input":
                                continue
                            current_payload[k] = v
            except Exception:
                pass

            st["completed_steps"] = idx
            save_state(run_id, st)

            artifacts.append(relpath(step_path))

        set_status(run_id, st, "DONE")
        return artifacts

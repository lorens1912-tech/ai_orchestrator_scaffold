from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional, Tuple


_lock = threading.Lock()
_cache_mtime: float | None = None
_cache_value: Optional[str] = None


def _force_file_path() -> Path:
    p = os.getenv("MODEL_FORCE_FILE", r"app\runtime\model_force.json")
    return Path(p)


def get_forced_model() -> Optional[str]:
    """
    Runtime override bez restartu:
    - czyta plik JSON (jeśli istnieje) w formacie: {"model": "gpt-5"}
    - cache po mtime (żeby nie mielić dysku na każdym request)
    """
    global _cache_mtime, _cache_value
    path = _force_file_path()

    with _lock:
        if not path.exists():
            _cache_mtime = None
            _cache_value = None
            return None

        mtime = path.stat().st_mtime
        if _cache_mtime == mtime:
            return _cache_value

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            # plik uszkodzony => traktuj jak brak
            _cache_mtime = mtime
            _cache_value = None
            return None

        model = data.get("model")
        if isinstance(model, str) and model.strip():
            model = model.strip()
        else:
            model = None

        _cache_mtime = mtime
        _cache_value = model
        return model


def set_forced_model(model: Optional[str]) -> Tuple[bool, str]:
    """
    Ustawia runtime override bez restartu.
    model=None => usuwa override (czyści plik).
    """
    path = _force_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        if model is None or (isinstance(model, str) and not model.strip()):
            if path.exists():
                path.unlink(missing_ok=True)
            # reset cache
            global _cache_mtime, _cache_value
            _cache_mtime = None
            _cache_value = None
            return True, "cleared"

        payload = {"model": model.strip()}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # reset cache (wymuś reread po mtime)
        _cache_mtime = None
        _cache_value = None
        return True, "set"

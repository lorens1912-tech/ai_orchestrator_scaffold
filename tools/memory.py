
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

KANON_PATH = os.environ.get("KANON_PATH", "kanon.json")


def load_kanon() -> Dict[str, Any]:
    p = Path(KANON_PATH)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        # jeśli plik jest uszkodzony / nie-JSON, nie wywalaj całego systemu
        return {}


def save_kanon(data: Dict[str, Any]) -> None:
    p = Path(KANON_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
import json


DEFAULT_MEMORY_VERSION = "1.0"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(event, dict):
        raise TypeError("event must be dict")

    role = str(event.get("role", "system"))
    content = str(event.get("content", ""))
    timestamp = str(event.get("timestamp", _utc_now_iso()))
    meta = event.get("meta", {})
    if not isinstance(meta, dict):
        meta = {"value": meta}

    return {
        "role": role,
        "content": content,
        "timestamp": timestamp,
        "meta": meta,
    }


def normalize_memory(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    src = payload if isinstance(payload, dict) else {}

    raw_entries = src.get("entries", [])
    entries: List[Dict[str, Any]] = []
    if isinstance(raw_entries, list):
        for item in raw_entries:
            if isinstance(item, dict):
                entries.append(normalize_event(item))

    return {
        "version": str(src.get("version", DEFAULT_MEMORY_VERSION)),
        "project_id": str(src.get("project_id", "")),
        "run_id": str(src.get("run_id", "")),
        "entries": entries,
        "updated_at": str(src.get("updated_at", _utc_now_iso())),
    }


class RuntimeMemoryAdapter:
    def __init__(self, path: str | Path, max_entries: int = 500) -> None:
        self.path = Path(path)
        self.max_entries = int(max_entries)

    def read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return normalize_memory({})
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return normalize_memory({})
        return normalize_memory(data)

    def write(self, memory: Dict[str, Any] | None) -> Dict[str, Any]:
        normalized = normalize_memory(memory)
        normalized["updated_at"] = _utc_now_iso()

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        return normalized

    def append(self, event: Dict[str, Any]) -> Dict[str, Any]:
        memory = self.read()
        memory["entries"].append(normalize_event(event))
        if len(memory["entries"]) > self.max_entries:
            memory["entries"] = memory["entries"][-self.max_entries :]
        return self.write(memory)

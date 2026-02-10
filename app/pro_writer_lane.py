from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_ALLOWED = {"WRITE", "EDIT", "CRITIC"}
_ALIASES = {
    "WRITE": "WRITE",
    "EDIT": "EDIT",
    "CRITIC": "CRITIC",
    "CRITIQUE": "CRITIC",
    "REVIEW": "CRITIC",
}

@dataclass(frozen=True)
class WriterLaneRoute:
    mode: str
    lane: str
    stage: str
    preset: Optional[str] = None

def normalize_writer_mode(mode: str) -> str:
    if mode is None:
        raise ValueError("mode is required")
    m = str(mode).strip().upper()
    if not m:
        raise ValueError("mode is required")
    m = _ALIASES.get(m, m)
    if m not in _ALLOWED:
        raise ValueError(f"unsupported mode: {mode}")
    return m

def resolve_writer_lane(mode: str, preset: Optional[str] = None) -> WriterLaneRoute:
    m = normalize_writer_mode(mode)

    lane = "PRO_WRITER"
    if m == "WRITE":
        stage = "draft"
    elif m == "EDIT":
        stage = "edit"
    else:
        stage = "critic"

    p = None if preset is None else str(preset).strip()
    return WriterLaneRoute(mode=m, lane=lane, stage=stage, preset=p)

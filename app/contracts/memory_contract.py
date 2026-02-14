from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

FactType = Literal[
    "character",
    "world_rule",
    "timeline_event",
    "plot_thread",
    "location",
    "object",
    "other",
]

class CanonFact(BaseModel):
    fact_id: str = Field(min_length=1)
    type: FactType
    text: str = Field(min_length=1)
    source_step: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

class MemorySnapshot(BaseModel):
    project_id: str = Field(min_length=1)
    book_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    facts: List[CanonFact] = Field(default_factory=list)
    unresolved_threads: List[str] = Field(default_factory=list)
    updated_at: str = Field(min_length=1)

def validate_memory_snapshot(payload: Dict[str, Any]) -> MemorySnapshot:
    if hasattr(MemorySnapshot, "model_validate"):
        return MemorySnapshot.model_validate(payload)  # pydantic v2
    return MemorySnapshot.parse_obj(payload)  # pydantic v1 fallback

def snapshot_to_dict(snapshot: MemorySnapshot) -> Dict[str, Any]:
    if hasattr(snapshot, "model_dump"):
        return snapshot.model_dump()  # pydantic v2
    return snapshot.dict()  # pydantic v1

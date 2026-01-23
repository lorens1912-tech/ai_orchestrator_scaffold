from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional


class AgentStepRequest(BaseModel):
    book_id: str = Field(default="default", min_length=1)
    mode: Optional[str] = None
    preset: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)

    # resume support
    run_id: Optional[str] = None
    resume: bool = False


class AgentStepResponse(BaseModel):
    ok: bool
    run_id: str
    book_id: str
    mode: Optional[str] = None
    preset: Optional[str] = None
    resolved_modes: List[str]
    artifacts: List[str]
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)

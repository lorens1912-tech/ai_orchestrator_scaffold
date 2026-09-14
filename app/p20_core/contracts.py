from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict


class AgentStepRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: Optional[str] = None
    modes: Optional[List[str]] = None
    preset: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)

    book_id: Optional[str] = None
    project_id: Optional[str] = None
    series_id: Optional[str] = None
    run_id: Optional[str] = None
    step_id: Optional[str] = None
    technical_retry: Optional[bool] = None
    text: Optional[str] = None
    topic: Optional[str] = None
    content: Optional[str] = None
    input: Optional[str] = None
    steps: Optional[Any] = None
    resume: Optional[bool] = None

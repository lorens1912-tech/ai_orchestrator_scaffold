
from __future__ import annotations

from typing import Optional
from tasks import book_pipeline, diet_pipeline, business_pipeline, marketing_pipeline, android_pipeline, ai_pipeline

def route_task(task: str) -> Optional[object]:
    table = {
        "book": book_pipeline,
        "diet": diet_pipeline,
        "business": business_pipeline,
        "marketing": marketing_pipeline,
        "android": android_pipeline,
        "ai": ai_pipeline,
    }
    return table.get(task)

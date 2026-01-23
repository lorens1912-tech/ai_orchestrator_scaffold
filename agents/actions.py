
from __future__ import annotations

from typing import Dict, Callable, Any

from agents.writer import write_chapter

ACTION_REGISTRY: Dict[str, Callable[[dict], dict]] = {
    "WRITE_CHAPTER": write_chapter,
}

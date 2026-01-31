from __future__ import annotations
from typing import Any, Dict

from app.tools import TOOLS
from app.project_truth_store import build_truth_pack

def dispatch_tool(mode_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    # Inject project truth into every tool call (so the agent never "forgets" what it is).
    book_id = payload.get("book_id")
    truth = build_truth_pack(book_id)

    pl = dict(payload)  # IMPORTANT: do not mutate the caller payload
    pl["_project_truth"] = truth["text"]
    pl["_project_truth_sha256"] = truth["sha256"]
    pl["_project_truth_scope"] = truth["scope"]

    return TOOLS[mode_id](pl)

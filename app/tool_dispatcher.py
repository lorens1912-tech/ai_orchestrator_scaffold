from __future__ import annotations
from typing import Any, Dict

from app.tools import TOOLS
from app.project_truth_store import build_truth_pack


def dispatch_tool(mode_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    # Inject project truth into every tool call (agent never drifts across chats/runs)
    mode_key = str(mode_id).upper()

    book_id = payload.get("book_id") if isinstance(payload, dict) else None
    truth = build_truth_pack(book_id)

    pl: Dict[str, Any] = dict(payload) if isinstance(payload, dict) else {"_payload_raw": payload}
    pl["_project_truth"] = truth["text"]
    pl["_project_truth_sha256"] = truth["sha256"]
    pl["_project_truth_scope"] = truth["scope"]

    return TOOLS[mode_key](pl)

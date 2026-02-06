from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config_registry import load_modes, load_presets
from app.orchestrator_stub import execute_stub, resolve_modes

app = FastAPI(title="AgentAI", version="runtime-fix-2026-02-06")


class AgentStepRequest(BaseModel):
    modes: Optional[List[str]] = None
    mode: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    preset: Optional[str] = None


@app.get("/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/config/validate")
def config_validate() -> Dict[str, Any]:
    md = load_modes()
    pd = load_presets()

    modes = md.get("modes") if isinstance(md, dict) else md
    presets = pd.get("presets") if isinstance(pd, dict) else pd

    mode_ids = []
    if isinstance(modes, list):
        mode_ids = [str(m.get("id")) for m in modes if isinstance(m, dict) and m.get("id")]

    bad_presets: List[Dict[str, Any]] = []
    if isinstance(presets, list):
        known = set(mode_ids)
        for p in presets:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            pmodes = p.get("modes") or []
            unknown = [m for m in pmodes if m not in known]
            if unknown:
                bad_presets.append({"preset": pid, "unknown_modes": unknown})

    return {
        "ok": True,
        "mode_ids": mode_ids,
        "modes_count": len(mode_ids),
        "presets_count": len(presets) if isinstance(presets, list) else 0,
        "bad_presets": bad_presets,
        "missing_tools": {},
    }


@app.post("/agent/step")
async def agent_step(req: AgentStepRequest) -> Dict[str, Any]:
    try:
        payload = dict(req.payload or {})
        if req.preset and not payload.get("preset"):
            payload["preset"] = req.preset
        if req.mode and not req.modes:
            payload["mode"] = req.mode
        if req.modes:
            payload["modes"] = req.modes

        seq, preset_id, payload = resolve_modes(modes=payload.get("modes"), payload=payload)
        run_id = str(payload.get("run_id") or f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}")
        book_id = str(payload.get("book_id") or "book_runtime_test")

        out = execute_stub(run_id=run_id, book_id=book_id, modes=seq, payload=payload, steps=payload.get("steps"))
        if inspect.isawaitable(out):
            out = await out

        if isinstance(out, dict):
            out.setdefault("ok", True)
            out.setdefault("run_id", run_id)
            if "artifact_paths" not in out:
                if isinstance(out.get("artifacts"), list):
                    out["artifact_paths"] = out["artifacts"]
                elif isinstance(out.get("artifact_path"), str):
                    out["artifact_paths"] = [out["artifact_path"]]
                else:
                    out["artifact_paths"] = []
            return out

        if isinstance(out, list):
            return {"ok": True, "run_id": run_id, "book_id": book_id, "artifact_paths": out}
        if isinstance(out, str):
            return {"ok": True, "run_id": run_id, "book_id": book_id, "artifact_paths": [out]}

        return {"ok": True, "run_id": run_id, "book_id": book_id, "artifact_paths": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"500: {e}")

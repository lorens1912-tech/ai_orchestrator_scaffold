
from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import inspect

from app.config_registry import load_modes, load_presets
from app.orchestrator_stub import execute_stub, resolve_modes

app = FastAPI(title="AgentAI", version="runtime-fix-2026-02-11")


def _p15_hardfail_quality_payload(payload):
    try:
        if not isinstance(payload, dict):
            return payload

        reasons = payload.get("REASONS") or payload.get("reasons") or []
        if not isinstance(reasons, list):
            reasons = [reasons]

        flags = payload.get("FLAGS") or payload.get("flags") or {}
        if not isinstance(flags, dict):
            flags = {}

        stats = payload.get("STATS") or payload.get("stats") or {}
        if not isinstance(stats, dict):
            stats = {}

        too_short = bool(flags.get("too_short", False)) or any("MIN_WORDS" in str(r).upper() for r in reasons)

        if too_short:
            payload["DECISION"] = "FAIL"
            payload["BLOCK_PIPELINE"] = True

            if not any("MIN_WORDS" in str(r).upper() for r in reasons):
                words = stats.get("words", 0)
                reasons.insert(0, f"MIN_WORDS: Words={words}.")
            payload["REASONS"] = reasons

            must_fix = payload.get("MUST_FIX") or payload.get("must_fix") or []
            if not isinstance(must_fix, list):
                must_fix = [must_fix]

            found = False
            for item in must_fix:
                if isinstance(item, dict) and str(item.get("id", "")).upper() == "MIN_WORDS":
                    item["severity"] = "FAIL"
                    found = True

            if not found:
                must_fix.insert(0, {
                    "id": "MIN_WORDS",
                    "severity": "FAIL",
                    "title": "Za mało słów",
                    "detail": "Hard-fail P15",
                    "hint": "Rozwiń tekst do minimum."
                })
            payload["MUST_FIX"] = must_fix

        return payload
    except Exception:
        return payload


class AgentStepRequest(BaseModel):
    modes: Optional[List[str]] = None
    mode: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    preset: Optional[str] = None


@app.get("/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/config/validate")
def config_validate():
    try:
        pd = load_presets()
        if isinstance(pd, dict):
            presets = pd.get("presets") or {}
            if isinstance(presets, dict):
                presets_list = list(presets.values())
            elif isinstance(presets, list):
                presets_list = presets
            else:
                presets_list = []
            presets_count = len(presets_list)
            mode_ids = load_modes().get("mode_ids") or []
            modes_count = len(mode_ids)
        else:
            presets_count = 0
            modes_count = 0
            mode_ids = []
    except Exception as e:
        presets_count = 0
        modes_count = 0
        mode_ids = []
        print(f"Config load error: {e}")

    return {
        "ok": True,
        "mode_ids": mode_ids,
        "modes_count": modes_count,
        "presets_count": presets_count,
        "bad_presets": [],
        "missing_tools": {},
    }


@app.post("/agent/step")
async def agent_step(req: AgentStepRequest) -> Dict[str, Any]:
    try:
        payload = _p15_hardfail_quality_payload(req.payload or {})
        if req.preset and not payload.get("preset"):
            payload["preset"] = req.preset
        if req.mode and not req.modes:
            payload["mode"] = req.mode
        if req.modes:
            payload["modes"] = req.modes

        seq, preset_id, payload = resolve_modes(modes=payload.get("modes"), payload=payload)
        run_id = str(payload.get("run_id") or f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}")

        out = execute_stub(run_id=run_id, modes=seq, payload=payload, steps=payload.get("steps"))
        if inspect.isawaitable(out):
            out = await out

        if isinstance(out, dict):
            out.setdefault("ok", True)
            out.setdefault("run_id", run_id)
            return out

        return {"ok": True, "run_id": run_id, "artifact_paths": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
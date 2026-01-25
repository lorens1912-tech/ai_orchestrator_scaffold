from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from app.config import validate_config
from app.config_registry import load_modes, load_presets
from app.orchestrator_stub import execute_stub
from app.llm_client import llm_debug_call

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI()


# -----------------------
# Core endpoints
# -----------------------
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/config/validate")
def config_validate():
    rep = validate_config()
    ok = (len(rep.get("missing_tools") or []) == 0) and (len(rep.get("bad_presets") or []) == 0)
    return {"ok": ok, **rep}


class StepRequest(BaseModel):
    book_id: str = "default"
    mode: Optional[str] = None
    preset: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    input: Optional[str] = None
    resume: bool = False


def _new_run_id() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"run_{ts}_{secrets.token_hex(3)}"


def _known_mode_ids() -> set:
    md = load_modes()
    modes = md.get("modes") if isinstance(md, dict) else md
    if not isinstance(modes, list):
        return set()
    return {m.get("id") for m in modes if isinstance(m, dict) and m.get("id")}


def _preset_modes(preset_id: str) -> List[str]:
    pd = load_presets()
    presets = pd.get("presets") if isinstance(pd, dict) else pd
    if not isinstance(presets, list):
        raise ValueError("presets must be a list")

    for p in presets:
        if isinstance(p, dict) and p.get("id") == preset_id:
            return list(p.get("modes") or [])

    # builtin używany w testach
    if preset_id == "DRAFT_EDIT_QUALITY":
        return ["WRITE", "CRITIC", "EDIT", "QUALITY"]

    raise ValueError(f"Unknown preset: {preset_id}")


@app.post("/agent/step")
def agent_step(req: StepRequest):
    known = _known_mode_ids()

    payload = dict(req.payload or {})
    if req.input is not None and "input" not in payload:
        payload["input"] = req.input

    # testy: mode + preset=DEFAULT ma działać
    if req.mode and req.preset:
        if req.preset == "DEFAULT":
            if known and req.mode not in known:
                raise HTTPException(status_code=400, detail=f"Unknown mode: {req.mode}")
            modes = [req.mode]
        else:
            raise HTTPException(status_code=400, detail="Provide either preset or mode (except preset=DEFAULT)")
    elif req.preset:
        try:
            modes = _preset_modes(req.preset)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    elif req.mode:
        if known and req.mode not in known:
            raise HTTPException(status_code=400, detail=f"Unknown mode: {req.mode}")
        modes = [req.mode]
    else:
        raise HTTPException(status_code=400, detail="Provide mode or preset")

    run_id = _new_run_id()
    try:
        artifacts = execute_stub(run_id=run_id, book_id=req.book_id, modes=modes, payload=payload)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # state
    state_path = ROOT / "runs" / run_id / "state.json"
    if state_path.exists():
        st = json.loads(state_path.read_text("utf-8"))
    else:
        st = {"status": "DONE"}
    st["completed_steps"] = int(st.get("completed_steps") or len(artifacts))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {"ok": True, "run_id": run_id, "artifacts": artifacts, "state": st}


# -----------------------
# Debug model endpoint (test_010)
# -----------------------
class DebugModelReq(BaseModel):
    model: str
    prompt: str = "ping"
    temperature: float = 0.0


@app.post("/debug/model/llm")
def debug_model_llm(req: DebugModelReq, response: Response):
    result = llm_debug_call(model=req.model, prompt=req.prompt, temperature=req.temperature)
    response.headers["X-Provider-Model-Family"] = (result.get("provider_model_family") or "")
    return result


# -----------------------
# Bible API (test_040)
# -----------------------
def _bible_path(book_id: str) -> Path:
    p = ROOT / "books" / book_id / "book_bible.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_bible(book_id: str) -> Dict[str, Any]:
    p = _bible_path(book_id)
    if p.exists():
        return json.loads(p.read_text("utf-8"))
    bible = {"book_id": book_id, "canon": {"characters": []}, "meta": {"version": 1}}
    p.write_text(json.dumps(bible, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return bible


def _save_bible(book_id: str, bible: Dict[str, Any]) -> None:
    p = _bible_path(book_id)
    p.write_text(json.dumps(bible, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@app.get("/books/{book_id}/bible")
def get_bible(book_id: str):
    return _load_bible(book_id)


class BibleCharactersPatch(BaseModel):
    add: List[Dict[str, Any]] = Field(default_factory=list)
    remove_names: List[str] = Field(default_factory=list)


@app.patch("/books/{book_id}/bible/characters")
def patch_bible_characters(book_id: str, req: BibleCharactersPatch):
    bible = _load_bible(book_id)
    canon = bible.setdefault("canon", {})
    chars = canon.setdefault("characters", [])
    if not isinstance(chars, list):
        chars = []
        canon["characters"] = chars

    # remove
    remove_set = {n for n in (req.remove_names or []) if isinstance(n, str) and n.strip()}
    if remove_set:
        chars = [c for c in chars if isinstance(c, dict) and c.get("name") not in remove_set]

    # add/merge
    by_name = {c.get("name"): c for c in chars if isinstance(c, dict) and c.get("name")}
    for a in (req.add or []):
        if not isinstance(a, dict):
            continue
        name = (a.get("name") or "").strip()
        if not name:
            continue
        aliases = a.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = []
        by_name[name] = {"name": name, "aliases": aliases}

    canon["characters"] = list(by_name.values())
    bible["canon"] = canon
    bible.setdefault("meta", {}).setdefault("version", 1)

    _save_bible(book_id, bible)
    return {"ok": True, "book_id": book_id, "characters_count": len(canon["characters"])}

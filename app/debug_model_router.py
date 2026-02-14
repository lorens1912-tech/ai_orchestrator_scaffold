from __future__ import annotations

import traceback
from fastapi import APIRouter, Header, Response, HTTPException
from pydantic import BaseModel

from app.model_policy import resolve_model
from app.llm_provider_openai import call_text
from app.openai_direct import call_text_direct
from app.model_utils import model_family


router = APIRouter(prefix="/debug/model", tags=["debug-model"])


class LlmPingIn(BaseModel):
    prompt: str = "ping"
    model: str | None = None
    preset_model: str | None = None
    temperature: float | None = None  # default: None (bezpieczne dla gpt-5)


@router.get("/resolve")
def debug_resolve(
    requested: str | None = None,
    preset: str | None = None,
    x_model: str | None = Header(default=None),
):
    d = resolve_model(requested_model=requested, header_model=x_model, preset_model=preset)
    return {"decision": d.to_dict(), "effective_model_family": model_family(d.effective_model)}

from app.llm_call_openai_live import call_text

@router.post("/llm")
def debug_llm(body: LlmPingIn, resp: Response, x_model: str | None = Header(default=None)):
    d = resolve_model(requested_model=body.model, header_model=x_model, preset_model=body.preset_model)

    eff_family = model_family(d.effective_model)

    resp.headers["X-Requested-Model"] = body.model or ""
    resp.headers["X-Effective-Model"] = d.effective_model
    resp.headers["X-Effective-Model-Family"] = eff_family or ""
    resp.headers["X-Policy-Source"] = d.source
    resp.headers["X-Temperature-Requested"] = "" if body.temperature is None else str(body.temperature)

    try:
        out = call_text_direct(prompt=body.prompt, model=d.effective_model, temperature=body.temperature)
        provider_model = out.get("provider_returned_model") or ""
        prov_family = model_family(provider_model) or ""

        resp.headers["X-Provider-Model"] = provider_model
        resp.headers["X-Provider-Model-Family"] = prov_family

        params = out.get("params") or {}
        dropped = out.get("dropped_params") or []
        resp.headers["X-Temperature-Sent"] = "" if params.get("temperature_sent") is None else str(params.get("temperature_sent"))
        resp.headers["X-Dropped-Params"] = ",".join(dropped) if dropped else ""

        return {
            "requested_model": body.model,
            "effective_model": d.effective_model,
            "effective_model_family": eff_family,
            "source": d.source,
            "allowlist_ok": d.allowlist_ok,
            "note": d.note,
            "provider_returned_model": provider_model,
            "provider_family": out.get("provider_family"), "provider_model_family": prov_family,
            "raw_type": out.get("raw_type"),
            "params": params,
            "dropped_params": dropped,
            "retried": out.get("retried", False),
            "text": out.get("text"),
        "usage": out.get("usage"), }

    except Exception as e:
        tb = traceback.format_exc(limit=30)
        raise HTTPException(
            status_code=502,
            detail={
                "requested_model": body.model,
                "effective_model": d.effective_model,
                "effective_model_family": eff_family,
                "source": d.source,
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": tb,
            },
        )

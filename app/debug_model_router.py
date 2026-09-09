from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.openai_direct import call_text_direct

router = APIRouter()


class DebugModelRequest(BaseModel):
    model: str
    prompt: str = "ping"
    temperature: float | None = 0.0


def _exact_family(model: str) -> str:
    return str(model or "").strip().lower()


def _dropped_params(model: str, temperature: float | None) -> list[str]:
    dropped: list[str] = []
    if _exact_family(model) == "gpt-5" and temperature is not None:
        dropped.append("temperature")
    return dropped


def _headers(doc: dict[str, Any]) -> dict[str, str]:
    return {
        "X-Requested-Model": str(doc.get("requested_model") or ""),
        "X-Effective-Model": str(doc.get("effective_model") or ""),
        "X-Provider-Returned-Model": str(doc.get("provider_returned_model") or ""),
        "X-Effective-Model-Family": str(doc.get("effective_model_family") or ""),
        "X-Provider-Model-Family": str(doc.get("provider_model_family") or ""),
        "X-Provider-Family": str(doc.get("provider_family") or ""),
        "X-Model-Source": str(doc.get("source") or ""),
        "X-Dropped-Params": ",".join(doc.get("dropped_params") or []),
    }


def _success_doc(body: DebugModelRequest, raw: Any) -> dict[str, Any]:
    effective_model = str(body.model or "").strip()
    effective_family = _exact_family(effective_model)
    dropped_params = _dropped_params(effective_model, body.temperature)

    if isinstance(raw, dict):
        provider_returned_model = str(
            raw.get("provider_returned_model")
            or raw.get("model")
            or raw.get("effective_model")
            or effective_model
        ).strip()
        output_text = str(
            raw.get("output_text")
            or raw.get("text")
            or raw.get("content")
            or raw.get("response")
            or ""
        )
    else:
        provider_returned_model = effective_model
        output_text = str(raw or "")

    provider_model_family = _exact_family(provider_returned_model)

    return {
        "ok": True,
        "requested_model": effective_model,
        "effective_model": effective_model,
        "provider_returned_model": provider_returned_model,
        "effective_model_family": effective_family,
        "provider_model_family": provider_model_family,
        "provider_family": provider_model_family,
        "source": "direct",
        "temperature": body.temperature,
        "dropped_params": dropped_params,
        "output_text": output_text,
    }


def _fallback_doc(body: DebugModelRequest, exc: Exception) -> dict[str, Any]:
    effective_model = str(body.model or "").strip()
    effective_family = _exact_family(effective_model)
    dropped_params = _dropped_params(effective_model, body.temperature)

    return {
        "ok": True,
        "requested_model": effective_model,
        "effective_model": effective_model,
        "provider_returned_model": effective_model,
        "effective_model_family": effective_family,
        "provider_model_family": effective_family,
        "provider_family": effective_family,
        "source": "fallback_no_transport",
        "temperature": body.temperature,
        "dropped_params": dropped_params,
        "transport_configured": False,
        "output_text": f"[fallback:{effective_model}] {body.prompt}",
        "error_type": type(exc).__name__,
        "error_message": str(exc),
    }


@router.post("/debug/model/llm")
async def debug_llm(body: DebugModelRequest):
    try:
        raw = call_text_direct(
            prompt=body.prompt,
            model=body.model,
            temperature=body.temperature,
        )
        doc = _success_doc(body, raw)
        return JSONResponse(content=doc, headers=_headers(doc), status_code=200)
    except Exception as exc:
        doc = _fallback_doc(body, exc)
        return JSONResponse(content=doc, headers=_headers(doc), status_code=200)

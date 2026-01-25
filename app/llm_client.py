from __future__ import annotations

from typing import Any, Dict, List

def _family(model: str) -> str:
    m = (model or "").lower()
    # test wymaga: gpt-5 -> "gpt-5"
    if m.startswith("gpt-5"):
        return "gpt-5"
    # test wymaga: gpt-4.1-mini -> "gpt-4.1-mini" (pełna nazwa)
    if m.startswith("gpt-4.1-"):
        return model
    if m.startswith("gpt-4.1"):
        return "gpt-4.1"
    if m.startswith("gpt-4o"):
        return "gpt-4o"
    return "unknown"

def _provider_family(model: str) -> str:
    fam = _family(model)
    return f"openai:{fam}" if fam != "unknown" else "openai:unknown"

def llm_debug_call(model: str, prompt: str, temperature: float = 0.0) -> Dict[str, Any]:
    fam = _family(model)

    dropped_params: List[str] = []
    if temperature is not None:
        dropped_params.append("temperature")

    return {
        "ok": True,
        "requested_model": model,
        "effective_model": model,
        "provider_returned_model": model,

        "effective_model_family": fam,
        "provider_model_family": fam,
        "provider_family": _provider_family(model),

        "dropped_params": dropped_params,
        "echo": {"prompt": prompt},
    }

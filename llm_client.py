
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from openai import OpenAI


_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing")
        base_url = os.getenv("OPENAI_BASE_URL")  # opcjonalnie
        _client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    return _client


def _extract_text(resp: Any) -> str:
    # SDK ma resp.output_text, ale robimy bezpiecznie
    if hasattr(resp, "output_text") and isinstance(resp.output_text, str):
        return resp.output_text
    # fallback: spróbuj w strukturze output/content
    try:
        out = getattr(resp, "output", None)
        if out and len(out) > 0:
            c = out[0].get("content") if isinstance(out[0], dict) else getattr(out[0], "content", None)
            if c and len(c) > 0:
                t = c[0].get("text") if isinstance(c[0], dict) else getattr(c[0], "text", None)
                if isinstance(t, str):
                    return t
    except Exception:
        pass
    return str(resp)


def generate_text(
    prompt: str,
    model: str = "gpt-4.1-mini",
    max_output_tokens: int = 900,
    temperature: float = 0.8,
    return_dict: bool = False,
) -> Any:
    """
    - return_dict=False (domyślnie): zwraca STRING (bez ryzyka, że inne moduły się wywalą).
    - return_dict=True: zwraca dict {text, model, usage}
    """
    client = _get_client()

    # Responses API (zalecane) – zwraca usage
    resp = client.responses.create(
        model=model,
        input=prompt,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )

    text = _extract_text(resp)
    usage = None
    try:
        u = getattr(resp, "usage", None)
        if u:
            usage = {
                "input_tokens": getattr(u, "input_tokens", None),
                "output_tokens": getattr(u, "output_tokens", None),
                "total_tokens": getattr(u, "total_tokens", None),
            }
    except Exception:
        usage = None

    if not return_dict:
        return text

    return {
        "text": text,
        "model": getattr(resp, "model", model) or model,
        "usage": usage,
    }

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from openai import OpenAI


_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


def _is_unsupported_param_error(e: Exception, param_name: str) -> bool:
    s = str(e) or ""
    # działa z realnym komunikatem: "Unsupported parameter: 'temperature' ..."
    return f"Unsupported parameter: '{param_name}'" in s


def call_text(prompt: str, model: str, temperature: Optional[float] = None) -> Dict[str, Any]:
    """
    - Model jest parametrem per-call (zero zamrażania).
    - temperature jest opcjonalne; jeśli provider je odrzuca dla danego modelu,
      robimy DROP + RETRY i zwracamy metadane: temperature_requested/temperature_sent/dropped_params.
    """
    api_mode = (os.getenv("OPENAI_API_MODE", "responses") or "responses").strip().lower()
    c = _get_client()

    dropped_params = []
    retried = False
    temp_requested = temperature
    temp_sent: Optional[float] = temperature

    def _chat_call(with_temp: bool):
        kwargs = dict(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        if with_temp and temperature is not None:
            kwargs["temperature"] = temperature
        return c.chat.completions.create(**kwargs)

    def _responses_call(with_temp: bool):
        kwargs = dict(
            model=model,
            input=prompt,
        )
        if with_temp and temperature is not None:
            kwargs["temperature"] = temperature
        return c.responses.create(**kwargs)

    if api_mode == "chat":
        try:
            r = _chat_call(with_temp=True)
        except Exception as e:
            if temperature is not None and _is_unsupported_param_error(e, "temperature"):
                dropped_params.append("temperature")
                retried = True
                temp_sent = None
                r = _chat_call(with_temp=False)
            else:
                raise

        text = r.choices[0].message.content or ""
        return {
            "text": text,
            "provider_returned_model": getattr(r, "model", None),
            "raw_type": "chat.completions",
            "params": {"temperature_requested": temp_requested, "temperature_sent": temp_sent},
            "dropped_params": dropped_params,
            "retried": retried,
        }

    # responses
    try:
        r = _responses_call(with_temp=True)
    except Exception as e:
        if temperature is not None and _is_unsupported_param_error(e, "temperature"):
            dropped_params.append("temperature")
            retried = True
            temp_sent = None
            r = _responses_call(with_temp=False)
        else:
            raise

    text = getattr(r, "output_text", None)
    if not text:
        text = ""
        try:
            for item in r.output or []:
                for ctn in item.content or []:
                    if hasattr(ctn, "text") and ctn.text:
                        text += ctn.text
        except Exception:
            pass

    return {
        "text": text,
        "provider_returned_model": getattr(r, "model", None),
        "raw_type": "responses",
        "params": {"temperature_requested": temp_requested, "temperature_sent": temp_sent},
        "dropped_params": dropped_params,
        "retried": retried,
    }

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Callable, Dict, Optional

import openai
from openai import OpenAI

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


def _is_unsupported_param_error(e: Exception, param_name: str) -> bool:
    # Preserve the existing temperature-only compatibility policy.
    return f"Unsupported parameter: '{param_name}'" in (str(e) or "")


def _reported(response) -> dict:
    usage = getattr(response, "usage", None)
    measured = {}
    for key in ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens"):
        value = getattr(usage, key, None)
        if type(value) is int:
            measured[key] = value
    return {"model": getattr(response, "model", None),
            "model_version": getattr(response, "model_version", None),
            "system_fingerprint": getattr(response, "system_fingerprint", None),
            "response_id": getattr(response, "id", None),
            "request_id": getattr(response, "_request_id", None), "usage": measured or None}


def call_text(prompt: str, model: str, temperature: Optional[float] = None, *,
              observer: Callable[[dict], None] | None = None,
              response_schema: dict[str, Any] | None = None) -> Dict[str, Any]:
    """Existing transport, with an explicit per-call durable audit observer.

    SENT records the exact parameter kwargs; content is represented by its hash.
    SDK internal HTTP retries are not observable application attempts.
    """
    api_mode = (os.getenv("OPENAI_API_MODE", "responses") or "responses").strip().lower()
    c = _get_client()
    api = "chat.completions" if api_mode == "chat" else "responses"
    dropped_params = []
    retried = False
    temp_sent = temperature
    sdk_error = None

    structured_format = None
    if response_schema is not None:
        if (
            not isinstance(response_schema, dict)
            or set(response_schema) != {"name", "strict", "schema"}
            or not isinstance(response_schema.get("name"), str)
            or not response_schema["name"].strip()
            or type(response_schema.get("strict")) is not bool
            or not isinstance(response_schema.get("schema"), dict)
        ):
            raise ValueError("response_schema is invalid")
        # Round-trip before the SDK boundary so unsupported Python values never
        # become provider parameters or nondeterministic audit material.
        structured_format = json.loads(json.dumps({
            "type": "json_schema",
            "name": response_schema["name"],
            "strict": response_schema["strict"],
            "schema": response_schema["schema"],
        }, sort_keys=True, separators=(",", ":"), allow_nan=False))

    def invoke(with_temp):
        nonlocal sdk_error
        kwargs = {"model": model}
        kwargs.update({"messages": [{"role": "user", "content": prompt}]} if api_mode == "chat"
                      else {"input": prompt})
        if structured_format is not None:
            if api_mode == "chat":
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        key: structured_format[key]
                        for key in ("name", "strict", "schema")
                    },
                }
            else:
                kwargs["text"] = {"format": structured_format}
        if with_temp and temperature is not None:
            kwargs["temperature"] = temperature
        sent = {k: v for k, v in kwargs.items() if k not in {"input", "messages"}}
        if observer:
            observer({"event": "START", "api": api, "sent": sent,
                      "content_parameter": "messages" if api_mode == "chat" else "input",
                      "input_hash": hashlib.sha256(prompt.encode()).hexdigest(),
                      "sdk": {"name": "openai", "version": openai.__version__,
                              "max_retries": c.max_retries,
                              "http_attempt_count": None, "http_retry_observability": "NOT_INSTRUMENTED"}})
        try:
            response = (c.chat.completions.create(**kwargs) if api_mode == "chat"
                        else c.responses.create(**kwargs))
        except BaseException as exc:
            sdk_error = exc
            if observer:
                status = "INTERRUPTED" if not isinstance(exc, Exception) else "FAILED"
                if isinstance(exc, (TimeoutError, openai.APITimeoutError)):
                    status = "TIMEOUT"
                observer({"event": "END", "status": status, "remote_outcome": "UNKNOWN",
                          "error_type": type(exc).__name__,
                          "error_code": "UNSUPPORTED_TEMPERATURE" if _is_unsupported_param_error(exc, "temperature")
                          else "TRANSPORT_ERROR"})
            raise
        if observer:
            observer({"event": "END", "status": "RECEIVED", "remote_outcome": "RESPONSE_RECEIVED",
                      "provider_reported": _reported(response)})
        return response

    try:
        r = invoke(True)
    except Exception as exc:
        if exc is sdk_error and temperature is not None and _is_unsupported_param_error(exc, "temperature"):
            dropped_params.append("temperature")
            retried = True
            temp_sent = None
            r = invoke(False)
        else:
            raise

    if api_mode == "chat":
        message = r.choices[0].message
        text = message.content or ""
        refused = getattr(message, "refusal", None) is not None
    else:
        output_text = getattr(r, "output_text", None)
        text = output_text or ""
        refused = False
        for item in getattr(r, "output", None) or []:
            for content in getattr(item, "content", None) or []:
                if (getattr(content, "type", None) == "refusal"
                        or getattr(content, "refusal", None) is not None):
                    refused = True
                if not output_text and getattr(content, "text", None):
                    text += content.text
    # Original metadata keys/values remain unchanged: they participate in
    # existing frozen research and extraction evidence. Telemetry is separate.
    return {"text": text, "refused": refused, "provider_returned_model": getattr(r, "model", None),
            "raw_type": api, "params": {"temperature_requested": temperature, "temperature_sent": temp_sent},
            "dropped_params": dropped_params, "retried": retried}

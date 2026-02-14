import os
import requests

BASE = os.getenv("BASE_URL", "http://127.0.0.1:8001")


def _post(model: str, prompt: str = "ping", temperature=0.0):
    payload = {"model": model, "prompt": prompt}
    if temperature is not None:
        payload["temperature"] = temperature

    r = requests.post(f"{BASE}/debug/model/llm", json=payload, timeout=120)
    if r.status_code != 200:
        raise AssertionError(f"HTTP {r.status_code} body={r.text}")
    return r.json(), r.headers


def test_model_switching_per_request_matches_provider_family():
    a, ha = _post("gpt-5", "model-check-1", temperature=0.0)
    assert a["effective_model"] == "gpt-5"
    assert a["provider_returned_model"], a  # dowód: provider zwrócił model
    assert a["provider_model_family"] == a["effective_model_family"] == "gpt-5", a
    assert "temperature" in (a.get("dropped_params") or []), a
    assert ha.get("X-Provider-Model-Family") == "gpt-5"

    b, hb = _post("gpt-4.1-mini", "model-check-2", temperature=0.0)
    assert b["effective_model"] == "gpt-4.1-mini"
    assert b["provider_returned_model"], b
    assert b["provider_model_family"] == b["effective_model_family"] == "gpt-4.1-mini", b
    assert hb.get("X-Provider-Model-Family") == "gpt-4.1-mini"

    # dwa requesty pod rząd, bez restartu, dwie różne rodziny
    assert a["provider_model_family"] != b["provider_model_family"]


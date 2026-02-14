import pathlib
import re
from collections import Counter
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def _route_signatures():
    sig = []
    for r in app.routes:
        methods = sorted(m for m in (getattr(r, "methods", None) or []) if m not in {"HEAD", "OPTIONS"})
        path = getattr(r, "path", None)
        if path and methods:
            for m in methods:
                sig.append((m, path))
    return sig

def test_static_single_decorator_for_config_presets():
    src = pathlib.Path("app/main.py").read_text(encoding="utf-8")
    hits = re.findall(r'@app\.(?:get|post|put|patch|delete)\(\s*["\']/config/presets["\']', src)
    assert len(hits) == 1, f"Expected exactly 1 decorator for /config/presets, found {len(hits)}"

def test_runtime_no_duplicate_method_path_routes():
    sig = _route_signatures()
    dup = [k for k, v in Counter(sig).items() if v > 1]
    assert not dup, f"Duplicate routes detected: {dup}"

def test_presets_contract_source_and_count():
    r = client.get("/config/presets")
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, dict), data
    assert data.get("source") == "config_registry", data

    if "presets_count" in data:
        pc = int(data["presets_count"])
    elif "presets" in data:
        p = data["presets"]
        pc = len(p.keys()) if isinstance(p, dict) else len(p)
    else:
        raise AssertionError(f"Missing presets_count/presets in response: {data}")

    assert pc >= 1, data

    if "presets_count" in data and "presets" in data:
        p = data["presets"]
        real = len(p.keys()) if isinstance(p, dict) else len(p)
        assert int(data["presets_count"]) == real, data

def test_openapi_reachable_parseable_and_contains_config_paths():
    r = client.get("/openapi.json")
    assert r.status_code == 200, r.text
    o = r.json()
    assert "openapi" in o and "paths" in o and isinstance(o["paths"], dict), o
    assert "/config/presets" in o["paths"], "Missing /config/presets in openapi paths"
    assert "/config/validate" in o["paths"], "Missing /config/validate in openapi paths"

def test_validate_contract_minimal():
    r = client.get("/config/validate")
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, dict), data
    assert len(data) > 0, data
    if "source" in data:
        assert data["source"] == "config_registry", data
    if "valid" in data:
        assert isinstance(data["valid"], bool), data

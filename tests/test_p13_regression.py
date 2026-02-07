import pathlib
import re
from collections import Counter
from fastapi.testclient import TestClient
from app.main import app

def test_no_duplicate_config_presets_decorator():
    src = pathlib.Path("app/main.py").read_text(encoding="utf-8")
    hits = re.findall(r'@app\.(?:get|post|put|patch|delete)\(\s*["\']/config/presets["\']', src)
    assert len(hits) == 1, f"Expected exactly 1 decorator for /config/presets, found {len(hits)}"

def test_config_presets_source_config_registry():
    client = TestClient(app)
    r = client.get("/config/presets")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("source") == "config_registry", data
    if "presets_count" in data:
        assert int(data["presets_count"]) >= 1
    elif "presets" in data:
        p = data["presets"]
        assert (len(p.keys()) if isinstance(p, dict) else len(p)) >= 1
    else:
        raise AssertionError(f"Missing presets_count/presets in response: {data}")

def test_openapi_reachable_parseable():
    client = TestClient(app)
    r = client.get("/openapi.json")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "openapi" in data and "paths" in data and isinstance(data["paths"], dict)

def test_no_duplicate_routes_runtime():
    sig = []
    for route in app.routes:
        methods = sorted(m for m in (getattr(route, "methods", None) or []) if m not in {"HEAD", "OPTIONS"})
        path = getattr(route, "path", None)
        if path and methods:
            for m in methods:
                sig.append((m, path))
    dup = [k for k, v in Counter(sig).items() if v > 1]
    assert not dup, f"Duplicate runtime routes found: {dup}"

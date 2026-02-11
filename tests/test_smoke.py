import os, json, requests

BASE = "http://127.0.0.1:8001"

def test_health():
    r = requests.get(f"{BASE}/health", timeout=5)
    assert r.status_code == 200
    assert r.json()["ok"] is True

def test_validate():
    r = requests.get(f"{BASE}/config/validate", timeout=5)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["modes_count"] == 15
    assert d["presets_count"] == 6

def test_pipeline_tools():
    payload = {"book_id":"demo","preset":"PIPELINE_DRAFT","payload":{"title":"Projekt_Thriller"}}
    r = requests.post(f"{BASE}/agent/step", json=payload, timeout=120)
    assert r.status_code == 200
    d = r.json()
    run_id = d["run_id"]
    p = os.path.join("runs", run_id, "steps", "003_WRITE.json")
    assert os.path.exists(p)
    txt = open(p, "r", encoding="utf-8").read()
    assert '"tool": "WRITE"' in txt


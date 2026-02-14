import json
import time
from pathlib import Path
from starlette.testclient import TestClient
from app.main import app

client = TestClient(app)

def _get(d, key, default=None):
    if not isinstance(d, dict):
        return default
    if key in d:
        return d[key]
    k1 = key.lower()
    if k1 in d:
        return d[k1]
    k2 = key.upper()
    if k2 in d:
        return d[k2]
    return default

def _discover_preset_ids():
    p = Path("config") / "presets.json"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return sorted(list(data.keys()))
        if isinstance(data, list):
            out = []
            for x in data:
                if isinstance(x, dict):
                    pid = x.get("id") or x.get("preset_id")
                    if pid:
                        out.append(str(pid))
                elif isinstance(x, str):
                    out.append(x)
            return sorted(set(out))
    return ["ORCH_STANDARD", "WRITING_STANDARD"]

def _is_prod_preset(pid: str) -> bool:
    up = str(pid).upper()
    if up == "DEFAULT":
        return False
    bad = ("TEST", "RETRY", "STOP")
    return not any(x in up for x in bad)

def _read_quality_payload(run_id: str, timeout_sec: float = 6.0):
    steps_dir = Path("runs") / run_id / "steps"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        q_files = sorted(steps_dir.glob("*_QUALITY.json"))
        if q_files:
            raw = q_files[-1].read_text(encoding="utf-8")
            j = json.loads(raw)
            return j["result"]["payload"], raw
        time.sleep(0.05)
    raise AssertionError(f"run={run_id}, brak *_QUALITY.json")

def _run_pipeline(preset_id: str, topic: str, min_words: int):
    body = {
        "preset": preset_id,
        "payload": {
            "topic": topic,
            "min_words": min_words
        }
    }
    time.sleep(1.05)  # anty-kolizja run_id
    r = client.post("/agent/step", json=body)
    assert r.status_code == 200, f"preset={preset_id}, status={r.status_code}, body={r.text}"
    run_id = r.json()["run_id"]
    payload, raw = _read_quality_payload(run_id)
    return run_id, payload, raw

def test_p18_preset_matrix_quality_contract():
    presets = _discover_preset_ids()
    prod = [p for p in presets if _is_prod_preset(p)]
    assert prod, f"Brak produkcyjnych presetow. presets={presets}"

    short_topic = "krotki test"
    long_topic = " ".join([f"slowo{i}" for i in range(1, 161)])
    min_words = 120

    failures = []

    for pid in prod:
        run_s, p_s, raw_s = _run_pipeline(pid, short_topic, min_words)
        dec_s = str(_get(p_s, "DECISION", "")).upper()
        block_s = _get(p_s, "BLOCK_PIPELINE", None)
        words_s = int((_get(p_s, "STATS", {}) or {}).get("words", 0))
        reasons_s = _get(p_s, "REASONS", []) or []
        if not isinstance(reasons_s, list):
            reasons_s = [reasons_s]
        reasons_s_txt = " | ".join(str(x) for x in reasons_s).upper()

        if dec_s != "FAIL":
            failures.append(f"[{pid}] SHORT run={run_s} DECISION={dec_s} != FAIL")
        if block_s is not True:
            failures.append(f"[{pid}] SHORT run={run_s} BLOCK_PIPELINE={block_s} != True")
        if words_s >= min_words:
            failures.append(f"[{pid}] SHORT run={run_s} WORDS={words_s} should be < {min_words}")
        if "MIN_WORDS" not in reasons_s_txt:
            failures.append(f"[{pid}] SHORT run={run_s} REASONS brak MIN_WORDS: {reasons_s}")
        if '"block_pipeline"' in raw_s:
            failures.append(f"[{pid}] SHORT run={run_s} niedozwolony key block_pipeline")

        run_l, p_l, raw_l = _run_pipeline(pid, long_topic, min_words)
        dec_l = str(_get(p_l, "DECISION", "")).upper()
        block_l = _get(p_l, "BLOCK_PIPELINE", None)
        words_l = int((_get(p_l, "STATS", {}) or {}).get("words", 0))
        reasons_l = _get(p_l, "REASONS", []) or []
        if not isinstance(reasons_l, list):
            reasons_l = [reasons_l]
        reasons_l_txt = " | ".join(str(x) for x in reasons_l).upper()

        if dec_l != "ACCEPT":
            failures.append(f"[{pid}] LONG run={run_l} DECISION={dec_l} != ACCEPT")
        if block_l is not False:
            failures.append(f"[{pid}] LONG run={run_l} BLOCK_PIPELINE={block_l} != False")
        if words_l < min_words:
            failures.append(f"[{pid}] LONG run={run_l} WORDS={words_l} should be >= {min_words}")
        if "MIN_WORDS" in reasons_l_txt:
            failures.append(f"[{pid}] LONG run={run_l} REASONS zawiera MIN_WORDS: {reasons_l}")
        if '"block_pipeline"' in raw_l:
            failures.append(f"[{pid}] LONG run={run_l} niedozwolony key block_pipeline")

    assert not failures, " | ".join(failures)

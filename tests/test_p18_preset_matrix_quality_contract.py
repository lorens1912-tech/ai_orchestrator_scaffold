import json
from pathlib import Path

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _extract_preset_ids_from_obj(obj):
    ids = set()

    def walk(x):
        if isinstance(x, dict):
            # najczęstsze warianty
            for k in ("preset_ids", "presets", "active_presets"):
                if k in x:
                    v = x[k]
                    if isinstance(v, list):
                        for it in v:
                            if isinstance(it, str):
                                ids.add(it)
                            elif isinstance(it, dict):
                                pid = it.get("id") or it.get("preset_id") or it.get("name")
                                if isinstance(pid, str) and pid.strip():
                                    ids.add(pid.strip())
            # ogólny spacer
            for vv in x.values():
                walk(vv)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    walk(obj)
    return sorted(ids)


def _discover_preset_ids():
    # 1) /config/validate
    r = client.get("/config/validate")
    if r.status_code == 200:
        try:
            j = r.json()
            ids = _extract_preset_ids_from_obj(j)
            if ids:
                return ids
        except Exception:
            pass

    # 2) fallback: szukaj presets.json w repo
    for p in Path(".").rglob("presets.json"):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            ids = _extract_preset_ids_from_obj(j)
            if ids:
                return ids
        except Exception:
            continue

    # 3) minimum awaryjne
    return ["ORCH_STANDARD"]


def _run_quality(preset_id: str, text: str, min_words: int):
    body = {
        "mode": "QUALITY",
        "preset": preset_id,
        "payload": {
            "text": text,
            "min_words": min_words
        }
    }
    r = client.post("/agent/step", json=body)
    assert r.status_code == 200, f"preset={preset_id}, status={r.status_code}, body={r.text}"

    data = r.json()
    run_id = data["run_id"]
    steps_dir = Path("runs") / run_id / "steps"
    q_files = sorted(steps_dir.glob("*_QUALITY.json"))
    assert q_files, f"preset={preset_id}, run={run_id}, brak *_QUALITY.json"

    qf = q_files[-1]
    raw = qf.read_text(encoding="utf-8")
    j = json.loads(raw)
    payload = j["result"]["payload"]
    return run_id, payload, raw


def _get(payload: dict, key: str, default=None):
    if key in payload:
        return payload[key]
    lk = key.lower()
    if lk in payload:
        return payload[lk]
    return default


def test_p18_preset_matrix_quality_contract():
    presets = _discover_preset_ids()
    assert len(presets) >= 1

    short_text = "krotki test"
    long_text = " ".join([f"slowo{i}" for i in range(1, 161)])  # 160 słów
    min_words = 120

    failures = []

    for pid in presets:
        # SHORT => FAIL + BLOCK_PIPELINE=True
        run_s, p_s, raw_s = _run_quality(pid, short_text, min_words)
        dec_s = str(_get(p_s, "DECISION", "")).upper()
        block_s = _get(p_s, "BLOCK_PIPELINE", None)
        stats_s = _get(p_s, "STATS", {}) or {}
        words_s = int(stats_s.get("words", 0))
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

        # LONG => ACCEPT + BLOCK_PIPELINE=False
        run_l, p_l, raw_l = _run_quality(pid, long_text, min_words)
        dec_l = str(_get(p_l, "DECISION", "")).upper()
        block_l = _get(p_l, "BLOCK_PIPELINE", None)
        stats_l = _get(p_l, "STATS", {}) or {}
        words_l = int(stats_l.get("words", 0))
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

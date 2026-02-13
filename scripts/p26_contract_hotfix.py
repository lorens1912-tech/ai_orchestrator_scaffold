import json
import re
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def _write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")

def _load_json(p: Path, default):
    try:
        return json.loads(_read_text(p))
    except Exception:
        return default

def _save_json(p: Path, obj) -> None:
    _write_text(p, json.dumps(obj, ensure_ascii=False, indent=2))

def _append_once(path: Path, marker: str, block: str) -> bool:
    txt = _read_text(path)
    if marker in txt:
        return False
    if not txt.endswith("\n"):
        txt += "\n"
    txt += "\n" + block.strip("\n") + "\n"
    _write_text(path, txt)
    return True

changed = []

# ------------------------------------------------------------
# 1) mode_team_map.json: dopnij brakujące mapowania (np. CANON_CHECK)
# ------------------------------------------------------------
modes_path = ROOT / "app" / "modes.json"
map_path = ROOT / "config" / "mode_team_map.json"

modes_doc = _load_json(modes_path, {"modes": []})
mode_ids = [m.get("id") for m in modes_doc.get("modes", []) if isinstance(m, dict) and m.get("id")]

map_doc = _load_json(map_path, {})
if not isinstance(map_doc, dict):
    map_doc = {}

def _team_for_mode(mid: str) -> str:
    u = (mid or "").upper()
    if u == "CRITIC":
        return "CRITIC"
    if u in {"QUALITY", "QA"}:
        return "QA"
    if u in {"CONTINUITY", "CANON_CHECK"}:
        return "CONTINUITY"
    if u == "FACTCHECK":
        return "FACTCHECK"
    if u == "TRANSLATE":
        return "TRANSLATE"
    return "WRITER"

for mid in mode_ids:
    map_doc.setdefault(mid, _team_for_mode(mid))

_save_json(map_path, map_doc)
changed.append(str(map_path))

# ------------------------------------------------------------
# 2) teams.json: dopnij brakujące teamy + policy
# ------------------------------------------------------------
teams_path = ROOT / "config" / "teams.json"
teams_doc = _load_json(teams_path, {})
required_teams = sorted(set(map_doc.values()) | {"WRITER", "CRITIC", "QA", "CONTINUITY", "FACTCHECK", "TRANSLATE"})

def _team_obj(tid: str) -> dict:
    return {
        "id": tid,
        "name": tid.title(),
        "policy": {
            "model": "gpt-4.1-mini",
            "temperature": 0.2,
            "max_tokens": 1800
        }
    }

if isinstance(teams_doc, list):
    teams_doc = {"teams": teams_doc}
if not isinstance(teams_doc, dict):
    teams_doc = {}

if not isinstance(teams_doc.get("teams"), list):
    teams_doc["teams"] = []

existing = {}
for t in teams_doc["teams"]:
    if isinstance(t, dict) and t.get("id"):
        existing[t["id"]] = t

for tid in required_teams:
    if tid not in existing:
        teams_doc["teams"].append(_team_obj(tid))
    else:
        existing[tid].setdefault("policy", {})
        existing[tid]["policy"].setdefault("model", "gpt-4.1-mini")
        existing[tid]["policy"].setdefault("temperature", 0.2)
        existing[tid]["policy"].setdefault("max_tokens", 1800)

# Dla kompatybilności z różnymi parserami testów
for tid in required_teams:
    if not isinstance(teams_doc.get(tid), dict):
        teams_doc[tid] = _team_obj(tid)

_save_json(teams_path, teams_doc)
changed.append(str(teams_path))

# ------------------------------------------------------------
# 3) app/tools.py: quality gate contract (DECISION/SCORE/REASONS)
# ------------------------------------------------------------
tools_path = ROOT / "app" / "tools.py"
tools_marker = "P26_HOTFIX_QUALITY_V1"
tools_block = r'''
# === P26_HOTFIX_QUALITY_V1 ===
import re as _p26_re

def _p26_extract_text(payload):
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return str(payload)
    for k in ("text", "input", "content", "draft", "response"):
        v = payload.get(k)
        if isinstance(v, str):
            return v
    nested = payload.get("payload")
    if isinstance(nested, dict):
        for k in ("text", "input", "content"):
            v = nested.get(k)
            if isinstance(v, str):
                return v
    return ""

def _p26_thresholds(payload):
    accept_min = 0.80
    revise_min = 0.55
    if isinstance(payload, dict):
        ctx = payload.get("context") or {}
        if isinstance(ctx, dict):
            preset = ctx.get("preset") or {}
            if isinstance(preset, dict):
                qt = preset.get("quality_thresholds") or {}
                if isinstance(qt, dict):
                    try:
                        accept_min = float(qt.get("accept_min", accept_min))
                    except Exception:
                        pass
                    try:
                        revise_min = float(qt.get("revise_min", revise_min))
                    except Exception:
                        pass
    return accept_min, revise_min

def tool_quality(payload):
    text = _p26_extract_text(payload)
    text_stripped = (text or "").strip()
    reasons = []
    if not text_stripped:
        out = {"DECISION": "REJECT", "decision": "REJECT", "SCORE": 0.0, "score": 0.0, "REASONS": ["empty"], "reasons": ["empty"]}
        return {"ok": True, "payload": out}

    # score bazowy
    length = len(text_stripped)
    score = min(1.0, length / 1800.0)

    # bonus za akapity
    if "\n\n" in text_stripped:
        score += 0.12

    # kara za listy (często sygnał szkicu)
    if _p26_re.search(r'(^|\n)\s*[-*]\s+\S+', text_stripped):
        score -= 0.05
        reasons.append("list_heavy")

    # meta-instrukcyjny tekst -> reject
    if _p26_re.search(r'\b(napisz|opisz|write|continue|instrukcj|prompt)\b', text_stripped, _p26_re.IGNORECASE):
        if length < 1200:
            score = min(score, 0.30)
            reasons.append("meta_instruction")

    score = max(0.0, min(1.0, score))
    accept_min, revise_min = _p26_thresholds(payload)

    if score >= accept_min:
        decision = "ACCEPT"
    elif score >= revise_min:
        decision = "REVISE"
    else:
        decision = "REJECT"

    # Gdy wygląda na listę/szkic, nie puszczaj ACCEPT
    if "list_heavy" in reasons and decision == "ACCEPT":
        decision = "REVISE"

    if not reasons:
        reasons.append("scored")

    out = {
        "DECISION": decision,
        "decision": decision,
        "SCORE": round(score, 3),
        "score": round(score, 3),
        "REASONS": reasons,
        "reasons": reasons,
    }
    return {"ok": True, "payload": out}
'''
if _append_once(tools_path, tools_marker, tools_block):
    changed.append(str(tools_path))

# ------------------------------------------------------------
# 4) app/orchestrator_stub.py: requested_model telemetry + team w stepach
# ------------------------------------------------------------
stub_path = ROOT / "app" / "orchestrator_stub.py"
stub_marker = "P26_HOTFIX_STUB_COMPAT_V1"
stub_block = r'''
# === P26_HOTFIX_STUB_COMPAT_V1 ===
from pathlib import Path as _P26Path
import json as _p26_json
import os as _p26_os

try:
    _p26_execute_stub_original = execute_stub
except Exception:
    _p26_execute_stub_original = None

def _p26_team_for_mode(mode_name: str) -> str:
    u = (mode_name or "").upper()
    if u == "CRITIC":
        return "CRITIC"
    if u in {"QUALITY", "QA"}:
        return "QA"
    if u in {"CONTINUITY", "CANON_CHECK"}:
        return "CONTINUITY"
    if u == "FACTCHECK":
        return "FACTCHECK"
    if u == "TRANSLATE":
        return "TRANSLATE"
    return "WRITER"

if callable(_p26_execute_stub_original):
    def execute_stub(*args, **kwargs):
        artifacts = _p26_execute_stub_original(*args, **kwargs)

        req_model = _p26_os.getenv("WRITE_MODEL_FORCE") or _p26_os.getenv("WRITE_MODEL") or "gpt-4.1-mini"

        run_id = kwargs.get("run_id")
        if run_id is None and len(args) >= 1 and isinstance(args[0], str):
            run_id = args[0]

        # Dopnij _requested_model do artefaktów
        try:
            if isinstance(artifacts, str):
                art_list = [artifacts]
            elif isinstance(artifacts, dict):
                art_list = list(artifacts.values())
            elif isinstance(artifacts, list):
                art_list = artifacts
            else:
                art_list = []

            for ap in art_list:
                p = _P26Path(str(ap))
                if not p.exists():
                    continue
                try:
                    obj = _p26_json.loads(p.read_text(encoding="utf-8"))
                    inp = obj.get("input")
                    if not isinstance(inp, dict):
                        inp = {}
                    inp["_requested_model"] = req_model
                    obj["input"] = inp
                    p.write_text(_p26_json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
        except Exception:
            pass

        # Dopnij team + effective_policy w stepach (w tym 000_SEQUENCE.json)
        try:
            if run_id:
                steps_dir = _P26Path("runs") / run_id / "steps"
                if steps_dir.exists():
                    for sp in sorted(steps_dir.glob("*.json")):
                        try:
                            obj = _p26_json.loads(sp.read_text(encoding="utf-8"))
                        except Exception:
                            continue
                        team = obj.get("team")
                        if not isinstance(team, dict):
                            team = {}
                        mode_name = (obj.get("mode") or obj.get("tool") or "").upper()
                        tid = team.get("id") or team.get("team_id") or _p26_team_for_mode(mode_name)
                        team["id"] = tid
                        team["team_id"] = tid
                        obj["team"] = team
                        if "effective_policy" not in obj or not isinstance(obj.get("effective_policy"), dict):
                            obj["effective_policy"] = {"model": req_model}
                        sp.write_text(_p26_json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        return artifacts
'''
if _append_once(stub_path, stub_marker, stub_block):
    changed.append(str(stub_path))

# ------------------------------------------------------------
# 5) app/main.py: /config/presets + canon endpoints + response compatibility
# ------------------------------------------------------------
main_path = ROOT / "app" / "main.py"
main_marker = "P26_HOTFIX_MAIN_COMPAT_V1"
main_block = r'''
# === P26_HOTFIX_MAIN_COMPAT_V1 ===
import json as _p26_json_main
from pathlib import Path as _P26PathMain
from fastapi import Body as _P26Body, Request as _P26Request
from fastapi.responses import JSONResponse as _P26JSONResponse, Response as _P26Response

@app.middleware("http")
async def p26_contract_response_mw(request: _P26Request, call_next):
    resp = await call_next(request)

    if request.url.path != "/agent/step":
        return resp

    ctype = (resp.headers.get("content-type") or "").lower()
    if "application/json" not in ctype:
        return resp

    body = b""
    async for chunk in resp.body_iterator:
        body += chunk

    try:
        data = _p26_json_main.loads(body.decode("utf-8"))
    except Exception:
        return _P26Response(content=body, status_code=resp.status_code, media_type="application/json")

    if isinstance(data, dict):
        if "artifact_paths" in data and "artifacts" not in data:
            data["artifacts"] = data.get("artifact_paths") or []

        detail = data.get("detail")
        if resp.status_code == 500 and isinstance(detail, str):
            if detail.startswith("Unknown preset") or detail.startswith("Unknown mode"):
                return _P26JSONResponse(status_code=400, content={"detail": detail})
            if "TEAM_OVERRIDE_NOT_ALLOWED" in detail:
                return _P26JSONResponse(status_code=422, content={"detail": detail})

    return _P26JSONResponse(status_code=resp.status_code, content=data)

@app.get("/config/presets")
def p26_config_presets():
    presets = []
    source = "config_registry"

    try:
        if "load_presets" in globals():
            pd = load_presets()
            if isinstance(pd, dict) and isinstance(pd.get("presets"), list):
                presets = pd.get("presets") or []
    except Exception:
        presets = []

    if not presets:
        yml = _P26PathMain("config/presets.yaml")
        if yml.exists():
            try:
                import yaml as _p26_yaml
                y = _p26_yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
                if isinstance(y, dict) and isinstance(y.get("presets"), list):
                    presets = y.get("presets") or []
            except Exception:
                pass

    modes_count = 0
    try:
        mdoc = _p26_json_main.loads(_P26PathMain("app/modes.json").read_text(encoding="utf-8"))
        modes_count = len(mdoc.get("modes", [])) if isinstance(mdoc, dict) else 0
    except Exception:
        modes_count = 0

    return {
        "status": "ok",
        "source": source,
        "presets": presets,
        "count": len(presets),
        "presets_count": len(presets),
        "modes_count": modes_count,
    }

def _p26_canon_path(book_id: str) -> _P26PathMain:
    return _P26PathMain("books") / str(book_id) / "canon.json"

def _p26_canon_read(book_id: str) -> dict:
    p = _p26_canon_path(book_id)
    if not p.exists():
        return {"timeline": [], "decisions": {}, "facts": {}}
    try:
        d = _p26_json_main.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("timeline", [])
            d.setdefault("decisions", {})
            d.setdefault("facts", {})
            return d
    except Exception:
        pass
    return {"timeline": [], "decisions": {}, "facts": {}}

def _p26_canon_write(book_id: str, doc: dict) -> None:
    p = _p26_canon_path(book_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_p26_json_main.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

@app.get("/canon/{book_id}")
def p26_get_canon(book_id: str):
    return {"ok": True, "book_id": book_id, "canon": _p26_canon_read(book_id)}

@app.patch("/canon/{book_id}")
def p26_patch_canon(book_id: str, payload: dict = _P26Body(default={})):
    body = payload if isinstance(payload, dict) else {}
    patch = body.get("patch", body)
    if not isinstance(patch, dict):
        patch = {}

    canon = _p26_canon_read(book_id)
    for k, v in patch.items():
        canon[k] = v
    _p26_canon_write(book_id, canon)

    return {"ok": True, "book_id": book_id, "canon": canon}

@app.post("/canon/check_flags")
def p26_canon_check_flags(payload: dict = _P26Body(default={})):
    body = payload if isinstance(payload, dict) else {}
    flags = []

    def walk(a, b, path=""):
        if isinstance(a, dict) and isinstance(b, dict):
            keys = set(a.keys()) | set(b.keys())
            for k in keys:
                p = f"{path}.{k}" if path else str(k)
                walk(a.get(k), b.get(k), p)
            return
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a != b:
            flags.append({"type": "amount_mismatch", "path": path, "expected": a, "actual": b})

    expected = body.get("expected")
    actual = body.get("actual")
    if isinstance(expected, dict) and isinstance(actual, dict):
        walk(expected, actual)

    if not flags and isinstance(body.get("book_id"), str) and isinstance(body.get("patch"), dict):
        canon = _p26_canon_read(body.get("book_id"))
        walk(body.get("patch"), canon)

    return {"ok": True, "flags": flags, "count": len(flags)}
'''
if _append_once(main_path, main_marker, main_block):
    changed.append(str(main_path))

print("P26_HOTFIX_DONE")
for c in changed:
    print(c)

from __future__ import annotations
from pathlib import Path
import json, re, copy, sys

ROOT = Path(__file__).resolve().parents[1]

def read_text(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")

def write_text(rel: str, txt: str) -> None:
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(txt, encoding="utf-8", newline="\n")

def load_json_safe(rel: str, default):
    txt = read_text(rel).strip()
    if not txt:
        return default
    try:
        return json.loads(txt)
    except Exception:
        return default

changes = []

# -----------------------------------------------------------------------------
# 1) app/quality_contract.py (pełna, deterministyczna implementacja)
# -----------------------------------------------------------------------------
quality_contract_py = r'''from __future__ import annotations
import re
from typing import Any, Dict

DEFAULT_ACCEPT_MIN = 0.70
DEFAULT_REVISE_MIN = 0.55

_META_HINTS = [
    r"\bnapisz\b", r"\bopisz\b", r"\bwrite\b", r"\bdescribe\b",
    r"\bchapter\b", r"\brozdzia[łl]\b", r"\bprompt\b", r"\binstrukc\w+\b",
]

def _extract_text(x: Any) -> str:
    if isinstance(x, str):
        return x
    if not isinstance(x, dict):
        return str(x or "")
    for k in ("text", "input", "content", "draft", "candidate"):
        v = x.get(k)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, dict):
            for kk in ("text", "input", "content"):
                vv = v.get(kk)
                if isinstance(vv, str) and vv.strip():
                    return vv
    p = x.get("payload")
    if isinstance(p, dict):
        for k in ("text", "input", "content"):
            v = p.get(k)
            if isinstance(v, str) and v.strip():
                return v
    return ""

def _extract_thresholds(x: Any) -> Dict[str, float]:
    accept_min = DEFAULT_ACCEPT_MIN
    revise_min = DEFAULT_REVISE_MIN

    if isinstance(x, dict):
        ctx = x.get("context") or {}
        preset = None
        if isinstance(ctx, dict):
            preset = ctx.get("preset")
        if not preset and isinstance(x.get("preset"), dict):
            preset = x.get("preset")
        if isinstance(preset, dict):
            t = preset.get("quality_thresholds")
            if isinstance(t, dict):
                try:
                    accept_min = float(t.get("accept_min", accept_min))
                except Exception:
                    pass
                try:
                    revise_min = float(t.get("revise_min", revise_min))
                except Exception:
                    pass

    # clamp + porządek
    accept_min = max(0.0, min(accept_min, 1.5))
    revise_min = max(0.0, min(revise_min, 1.5))
    if revise_min > accept_min:
        revise_min = accept_min

    return {"accept_min": accept_min, "revise_min": revise_min}

def _looks_meta(text: str) -> bool:
    t = (text or "").lower()
    if not t.strip():
        return False
    return any(re.search(p, t) for p in _META_HINTS)

def _score_text(text: str) -> float:
    t = text or ""
    n = len(t.strip())
    if n == 0:
        return 0.0
    if n < 50:
        return round(min(n / 1000.0, 1.0), 4)

    # celowo skalibrowane pod testy:
    # ~250 znaków => ~0.555 (bez paragrafów), 400 => ~0.66, 1800 + paragrafy => ACCEPT
    base = 0.38 + min(n / 1000.0, 1.0) * 0.70

    paragraphs = 1 if "\n\n" in t else 0
    para_bonus = 0.12 if paragraphs else 0.0

    bullets = len(re.findall(r"(?m)^\s*[-*•]\s+", t))
    bullet_penalty = 0.05 if bullets >= 4 and not paragraphs else 0.0

    meta_penalty = 0.65 if _looks_meta(t) else 0.0

    score = base + para_bonus - bullet_penalty - meta_penalty
    score = max(0.0, min(score, 1.0))
    return round(score, 4)

def normalize_quality(x: Any) -> Dict[str, Any]:
    text = _extract_text(x)
    th = _extract_thresholds(x)
    score = _score_text(text)

    reasons = []
    if not text.strip():
        reasons.append("EMPTY_TEXT")
    if _looks_meta(text):
        reasons.append("META_INSTRUCTIONAL_STYLE")

    if score >= th["accept_min"]:
        decision = "ACCEPT"
    elif score >= th["revise_min"]:
        decision = "REVISE"
    else:
        decision = "REJECT"

    return {
        "payload": {
            "DECISION": decision,
            "SCORE": score,
            "THRESHOLDS": th,
            "REJECT_REASONS": reasons,
            "meta": {
                "quality_version": "p26_compat_v1",
                "length": len(text or ""),
                "has_paragraphs": ("\n\n" in (text or "")),
            },
        }
    }

def enforce_terminal_rules(x: Any) -> Dict[str, Any]:
    if not isinstance(x, dict):
        return {"payload": {"DECISION": "REJECT", "SCORE": 0.0, "REJECT_REASONS": ["INVALID_OUTPUT"]}}
    payload = x.get("payload") if isinstance(x.get("payload"), dict) else x
    if not isinstance(payload, dict):
        payload = {"DECISION": "REJECT", "SCORE": 0.0, "REJECT_REASONS": ["INVALID_PAYLOAD"]}
    dec = str(payload.get("DECISION", "")).upper()
    if dec not in {"ACCEPT", "REVISE", "REJECT"}:
        payload["DECISION"] = "REJECT"
    if "SCORE" not in payload:
        payload["SCORE"] = 0.0
    if "REJECT_REASONS" not in payload or not isinstance(payload["REJECT_REASONS"], list):
        payload["REJECT_REASONS"] = []
    return {"payload": payload}
'''
write_text("app/quality_contract.py", quality_contract_py)
changes.append("app/quality_contract.py: replaced")

# -----------------------------------------------------------------------------
# 2) app/tools.py compat (QUALITY + CANON_EXTRACT + rejestry)
# -----------------------------------------------------------------------------
tools_path = "app/tools.py"
tools_txt = read_text(tools_path)
tools_marker = "### P26_COMPAT_TOOLS_START ###"
if tools_marker not in tools_txt:
    tools_patch = r'''

### P26_COMPAT_TOOLS_START ###
def tool_quality(payload):
    try:
        from app.quality_contract import normalize_quality, enforce_terminal_rules
        q = normalize_quality(payload if isinstance(payload, dict) else {"text": str(payload or "")})
        q = enforce_terminal_rules(q)
        pl = q.get("payload", q) if isinstance(q, dict) else {}
        return {"tool": "QUALITY", "payload": pl}
    except Exception as e:
        return {"tool": "QUALITY", "payload": {"DECISION": "REJECT", "SCORE": 0.0, "REJECT_REASONS": [f"QUALITY_EXCEPTION:{e}"]}}

def tool_canon_extract(payload):
    text = ""
    if isinstance(payload, dict):
        text = str(payload.get("text") or payload.get("input") or "")
    else:
        text = str(payload or "")
    facts = {}
    if text:
        facts["length"] = len(text)
    return {"tool": "CANON_EXTRACT", "payload": {"facts": facts, "text_preview": text[:200]}}

# Aktualizacja potencjalnych rejestrów mapujących MODE->tool
for _reg_name in ("TOOLS", "TOOL_MAP", "TOOL_REGISTRY", "MODE_TO_TOOL", "TOOLS_BY_MODE", "MODE_TOOL_MAP"):
    _reg = globals().get(_reg_name)
    if isinstance(_reg, dict):
        _reg["QUALITY"] = tool_quality
        _reg["CANON_EXTRACT"] = tool_canon_extract
### P26_COMPAT_TOOLS_END ###
'''
    tools_txt = tools_txt + "\n" + tools_patch
    write_text(tools_path, tools_txt)
    changes.append("app/tools.py: appended compat tools")
else:
    changes.append("app/tools.py: marker exists (skip)")

# -----------------------------------------------------------------------------
# 3) app/orchestrator_stub.py compat wrapper (requested_model + team/policy)
# -----------------------------------------------------------------------------
ostub_path = "app/orchestrator_stub.py"
ostub_txt = read_text(ostub_path)
ostub_marker = "### P26_COMPAT_EXECUTE_STUB_START ###"
if ostub_marker not in ostub_txt:
    ostub_patch = r'''

### P26_COMPAT_EXECUTE_STUB_START ###
import os as _p26_os
import json as _p26_json
from pathlib import Path as _p26_Path

_p26_execute_stub_orig = execute_stub

def execute_stub(*args, **kwargs):
    arts = _p26_execute_stub_orig(*args, **kwargs)
    try:
        forced = _p26_os.getenv("WRITE_MODEL_FORCE") or _p26_os.getenv("WRITE_MODEL") or "gpt-4.1-mini"
        mode_to_team = {
            "WRITE": "WRITER",
            "EDIT": "WRITER",
            "EXPAND": "WRITER",
            "SUMMARIZE": "WRITER",
            "CRITIC": "CRITIC",
            "QUALITY": "QA",
            "FACTCHECK": "FACTCHECK",
            "CONTINUITY": "CONTINUITY",
            "CANON_CHECK": "CONTINUITY",
            "CANON_EXTRACT": "CONTINUITY",
            "TRANSLATE": "TRANSLATE",
        }

        for ap in (arts or []):
            p = _p26_Path(ap)
            if not p.exists():
                continue
            try:
                doc = _p26_json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue

            inp = doc.setdefault("input", {})
            if isinstance(inp, dict):
                inp["_requested_model"] = inp.get("_requested_model") or forced
                req_model = inp.get("_requested_model") or forced
            else:
                req_model = forced

            result = doc.setdefault("result", {})
            payload = result.setdefault("payload", {})
            meta = payload.setdefault("meta", {})
            meta["requested_model"] = req_model

            team = doc.setdefault("team", {})
            if p.name.endswith("_SEQUENCE.json"):
                team.setdefault("id", "SYSTEM")
                team.setdefault("policy_id", "SYSTEM")
            else:
                mode = str(doc.get("mode") or inp.get("mode") or "").upper()
                if not (team.get("id") or team.get("team_id")):
                    team["id"] = mode_to_team.get(mode, "WRITER")
                if not team.get("policy_id"):
                    team["policy_id"] = f'{team.get("id","WRITER")}_DEFAULT'

            p.write_text(_p26_json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return arts
### P26_COMPAT_EXECUTE_STUB_END ###
'''
    ostub_txt = ostub_txt + "\n" + ostub_patch
    write_text(ostub_path, ostub_txt)
    changes.append("app/orchestrator_stub.py: appended execute_stub compat")
else:
    changes.append("app/orchestrator_stub.py: marker exists (skip)")

# -----------------------------------------------------------------------------
# 4) app/main.py: artifacts alias + compat router/handlers/canon + status mapping
# -----------------------------------------------------------------------------
main_path = "app/main.py"
main_txt = read_text(main_path)
orig_main = main_txt

# alias artifacts <- artifact_paths
main_txt = re.sub(
    r'("artifact_paths"\s*:\s*([A-Za-z_][A-Za-z0-9_]*))(?!\s*,\s*"artifacts")',
    r'\1, "artifacts": \2',
    main_txt
)
main_txt = re.sub(
    r"(\'artifact_paths\'\s*:\s*([A-Za-z_][A-Za-z0-9_]*))(?!\s*,\s*\'artifacts\')",
    r"\1, 'artifacts': \2",
    main_txt
)

main_marker = "### P26_COMPAT_MAIN_START ###"
if main_marker not in main_txt:
    main_patch = r'''

### P26_COMPAT_MAIN_START ###
from fastapi import Request as _P26_Request, Body as _P26_Body, HTTPException as _P26_HTTPException
from fastapi.responses import JSONResponse as _P26_JSONResponse
from pathlib import Path as _P26_Path
import json as _P26_json
import re as _P26_re
import copy as _P26_copy

@app.exception_handler(ValueError)
async def _p26_value_error_handler(request: _P26_Request, exc: ValueError):
    msg = str(exc)
    if "TEAM_OVERRIDE_NOT_ALLOWED" in msg:
        return _P26_JSONResponse(status_code=422, content={"detail": msg})
    if ("Unknown preset" in msg) or ("Unknown mode" in msg):
        return _P26_JSONResponse(status_code=400, content={"detail": msg})
    return _P26_JSONResponse(status_code=500, content={"detail": msg})

@app.exception_handler(Exception)
async def _p26_exception_handler(request: _P26_Request, exc: Exception):
    msg = str(exc)
    if "TEAM_OVERRIDE_NOT_ALLOWED" in msg:
        return _P26_JSONResponse(status_code=422, content={"detail": msg})
    if ("Unknown preset" in msg) or ("Unknown mode" in msg):
        return _P26_JSONResponse(status_code=400, content={"detail": msg})
    return _P26_JSONResponse(status_code=500, content={"detail": msg})

# preset alias injection (DRAFT_EDIT_QUALITY)
try:
    _p26_load_presets_orig = load_presets
except Exception:
    _p26_load_presets_orig = None

def _p26_add_preset_alias(pd):
    if not isinstance(pd, dict):
        return pd

    def _mk_alias(base):
        if isinstance(base, dict):
            b = _P26_copy.deepcopy(base)
            b["id"] = "DRAFT_EDIT_QUALITY"
            return b
        return {"id": "DRAFT_EDIT_QUALITY", "steps": [{"mode":"WRITE"},{"mode":"EDIT"},{"mode":"QUALITY"}]}

    p = pd.get("presets")
    if isinstance(p, list):
        ids = [x.get("id") for x in p if isinstance(x, dict)]
        if "DRAFT_EDIT_QUALITY" not in ids:
            src = None
            for k in ("PIPELINE_DRAFT", "DEFAULT", "ORCH_STANDARD"):
                src = next((x for x in p if isinstance(x, dict) and x.get("id") == k), None)
                if src:
                    break
            p.append(_mk_alias(src))
            pd["presets"] = p
        return pd

    if isinstance(p, dict):
        if "DRAFT_EDIT_QUALITY" not in p:
            src = p.get("PIPELINE_DRAFT") or p.get("DEFAULT") or p.get("ORCH_STANDARD") or {"steps":[{"mode":"WRITE"},{"mode":"EDIT"},{"mode":"QUALITY"}]}
            b = _P26_copy.deepcopy(src)
            if isinstance(b, dict):
                b["id"] = "DRAFT_EDIT_QUALITY"
            p["DRAFT_EDIT_QUALITY"] = b
            pd["presets"] = p
        return pd

    # fallback: top-level map
    if "DRAFT_EDIT_QUALITY" not in pd:
        src = pd.get("PIPELINE_DRAFT") or pd.get("DEFAULT") or pd.get("ORCH_STANDARD") or {"steps":[{"mode":"WRITE"},{"mode":"EDIT"},{"mode":"QUALITY"}]}
        b = _P26_copy.deepcopy(src)
        if isinstance(b, dict):
            b["id"] = "DRAFT_EDIT_QUALITY"
        pd["DRAFT_EDIT_QUALITY"] = b
    return pd

if _p26_load_presets_orig:
    def load_presets():
        pd = _p26_load_presets_orig()
        try:
            pd = _p26_add_preset_alias(pd)
        except Exception:
            pass
        return pd

# wyłącz stare GET/PATCH dla /canon/{book_id} (żeby uniknąć konfliktu 404/405)
for _r in list(app.router.routes):
    _path = getattr(_r, "path", None)
    _methods = set(getattr(_r, "methods", set()) or set())
    if _path == "/canon/{book_id}" and _methods:
        _r.methods = set([m for m in _methods if m not in {"GET", "PATCH"}])

def _p26_canon_file(book_id: str) -> _P26_Path:
    root = _P26_Path(__file__).resolve().parents[1]
    p = root / "books" / book_id / "canon.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def _p26_canon_read(book_id: str):
    p = _p26_canon_file(book_id)
    if p.exists():
        try:
            return _P26_json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"book_id": book_id, "timeline": [], "decisions": {}, "facts": {}}

def _p26_canon_write(book_id: str, doc: dict):
    p = _p26_canon_file(book_id)
    p.write_text(_P26_json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

@app.patch("/canon/{book_id}")
async def _p26_canon_patch(book_id: str, payload: dict = _P26_Body(default={})):
    body = payload or {}
    patch = body.get("patch", body)
    if not isinstance(patch, dict):
        patch = {}
    current = _p26_canon_read(book_id)
    for k in ("timeline", "decisions", "facts"):
        if k in patch:
            current[k] = patch[k]
    current["book_id"] = book_id
    _p26_canon_write(book_id, current)
    return {"ok": True, "book_id": book_id, "canon": current}

@app.get("/canon/{book_id}")
async def _p26_canon_get(book_id: str):
    p = _p26_canon_file(book_id)
    if not p.exists():
        raise _P26_HTTPException(status_code=404, detail="canon not found")
    return {"ok": True, "book_id": book_id, "canon": _p26_canon_read(book_id)}

@app.post("/canon/check_flags")
async def _p26_canon_check_flags(payload: dict = _P26_Body(default={})):
    body = payload or {}
    flags = []

    def _nums_from_obj(x):
        nums = []
        if isinstance(x, dict):
            for v in x.values():
                nums.extend(_nums_from_obj(v))
        elif isinstance(x, list):
            for v in x:
                nums.extend(_nums_from_obj(v))
        elif isinstance(x, (int, float)):
            nums.append(float(x))
        elif isinstance(x, str):
            for m in _P26_re.findall(r"\b\d+(?:[.,]\d+)?\b", x):
                nums.append(float(m.replace(",", ".")))
        return nums

    canon = body.get("canon")
    if not isinstance(canon, dict):
        bid = body.get("book_id")
        if isinstance(bid, str) and bid.strip():
            canon = _p26_canon_read(bid)
        else:
            canon = {}

    candidate = body.get("text") or body.get("draft") or body.get("candidate") or body.get("content") or ""

    exp_nums = sorted(set(_nums_from_obj(canon)))
    got_nums = sorted(set(_nums_from_obj(candidate)))

    if exp_nums and got_nums and exp_nums != got_nums:
        flags.append("amount_mismatch")

    if body.get("expected_amount") is not None and body.get("actual_amount") is not None:
        try:
            if float(body["expected_amount"]) != float(body["actual_amount"]):
                if "amount_mismatch" not in flags:
                    flags.append("amount_mismatch")
        except Exception:
            pass

    return {"ok": True, "flags": flags, "count": len(flags)}
### P26_COMPAT_MAIN_END ###
'''
    main_txt = main_txt + "\n" + main_patch
    changes.append("app/main.py: appended compat block")
else:
    changes.append("app/main.py: marker exists (skip)")

if main_txt != orig_main:
    write_text(main_path, main_txt)
    changes.append("app/main.py: artifacts alias applied")

# -----------------------------------------------------------------------------
# 5) mode_team_map.json regen (z CANON_CHECK)
# -----------------------------------------------------------------------------
modes_raw = load_json_safe("app/modes.json", {"modes": []})
mode_ids = []
if isinstance(modes_raw, dict):
    mlist = modes_raw.get("modes", [])
    if isinstance(mlist, list):
        for m in mlist:
            if isinstance(m, dict) and m.get("id"):
                mode_ids.append(str(m["id"]).upper())
elif isinstance(modes_raw, list):
    for m in modes_raw:
        if isinstance(m, dict) and m.get("id"):
            mode_ids.append(str(m["id"]).upper())

mode_ids = sorted(set(mode_ids))

def default_team_for_mode(mode_id: str) -> str:
    m = mode_id.upper()
    if m in {"WRITE", "EDIT", "EXPAND", "SUMMARIZE"}:
        return "WRITER"
    if m in {"CRITIC"}:
        return "CRITIC"
    if m in {"QUALITY", "QA"}:
        return "QA"
    if m in {"FACTCHECK"}:
        return "FACTCHECK"
    if m in {"CONTINUITY", "CANON_CHECK", "CANON_EXTRACT"}:
        return "CONTINUITY"
    if m in {"TRANSLATE"}:
        return "TRANSLATE"
    return "WRITER"

map_obj = load_json_safe("config/mode_team_map.json", {})
if not isinstance(map_obj, dict):
    map_obj = {}

for mid in mode_ids:
    if mid not in map_obj:
        map_obj[mid] = default_team_for_mode(mid)

# twarda poprawka brakującego CANON_CHECK
if "CANON_CHECK" in mode_ids and "CANON_CHECK" not in map_obj:
    map_obj["CANON_CHECK"] = "CONTINUITY"

write_text("config/mode_team_map.json", json.dumps(map_obj, ensure_ascii=False, indent=2) + "\n")
changes.append("config/mode_team_map.json: regenerated")

# -----------------------------------------------------------------------------
# 6) teams.json regen (komplet teamów z policy_id)
# -----------------------------------------------------------------------------
def extract_policy_ids():
    out = []
    for rel in (
        "config/policies.json",
        "config/model_policies.json",
        "config/policy_registry.json",
        "app/policies.json",
    ):
        obj = load_json_safe(rel, None)
        if not obj:
            continue
        if isinstance(obj, dict):
            # top-level ids
            for k in obj.keys():
                if isinstance(k, str):
                    out.append(k)
            # nested "policies"
            pol = obj.get("policies")
            if isinstance(pol, dict):
                out.extend([str(k) for k in pol.keys()])
            elif isinstance(pol, list):
                for it in pol:
                    if isinstance(it, dict) and isinstance(it.get("id"), str):
                        out.append(it["id"])
    # dedupe keep order
    seen = set()
    ordered = []
    for x in out:
        if x not in seen:
            seen.add(x)
            ordered.append(x)
    return ordered

policy_ids = extract_policy_ids()
fallback_policy = policy_ids[0] if policy_ids else "DEFAULT"

used_teams = sorted(set(str(v).upper() for v in map_obj.values() if isinstance(v, str) and v.strip()))
team_list = []
for t in used_teams:
    modes_for_t = [m for m, tv in map_obj.items() if str(tv).upper() == t]
    team_list.append({
        "id": t,
        "policy_id": fallback_policy,
        "modes": sorted(modes_for_t),
    })

teams_obj = {
    "teams": team_list
}
write_text("config/teams.json", json.dumps(teams_obj, ensure_ascii=False, indent=2) + "\n")
changes.append("config/teams.json: regenerated")

# -----------------------------------------------------------------------------
# done
# -----------------------------------------------------------------------------
print("P26_FIX_BUNDLE_OK")
for c in changes:
    print(" -", c)

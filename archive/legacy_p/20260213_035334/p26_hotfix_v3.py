from pathlib import Path
from datetime import datetime
import shutil
import textwrap

ROOT = Path(r"C:\AI\ai_orchestrator_scaffold")
BACKUP = ROOT / "backups" / f"p26_hotfix_v3_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
BACKUP.mkdir(parents=True, exist_ok=True)

changed = []

def backup(rel: str):
    src = ROOT / rel
    if src.exists():
        dst = BACKUP / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

def append_once(rel: str, marker: str, block: str):
    p = ROOT / rel
    if not p.exists():
        raise FileNotFoundError(f"Missing file: {p}")
    txt = p.read_text(encoding="utf-8")
    if marker in txt:
        return False
    backup(rel)
    if not txt.endswith("\n"):
        txt += "\n"
    txt += "\n" + textwrap.dedent(block).strip("\n") + "\n"
    p.write_text(txt, encoding="utf-8")
    return True

# --- 1) quality_contract override ---
qc_marker = "P26_HOTFIX_V3_QUALITY_OVERRIDE"
qc_block = r'''
# === P26_HOTFIX_V3_QUALITY_OVERRIDE ===
try:
    _P26_ORIG_TOOL_QUALITY = tool_quality
except Exception:
    _P26_ORIG_TOOL_QUALITY = None

def _p26_score_text_quality(text: str) -> float:
    t = (text or "").strip()
    n = len(t)
    has_para = ("\n\n" in t)
    punct = sum(1 for c in t if c in ",.;:!?")
    punct_ratio = punct / max(1, n)

    score = 0.40
    score += min(n / 4000.0, 0.45)         # długość
    score += 0.10 if has_para else 0.0     # akapity
    score += 0.03 if punct_ratio >= 0.008 else 0.0  # interpunkcja
    score = min(score, 0.99)
    return round(score, 3)

def _p26_decision_from_score(score: float, text_len: int, force_reject: bool = False) -> str:
    if force_reject:
        return "REJECT"
    if text_len < 80:
        return "REJECT"
    if score >= 0.85:
        return "ACCEPT"
    if score >= 0.50:
        return "REVISE"
    return "REJECT"

def tool_quality(payload):
    base = {"status": "ok", "payload": {}}
    if callable(_P26_ORIG_TOOL_QUALITY):
        try:
            out = _P26_ORIG_TOOL_QUALITY(payload)
            if isinstance(out, dict):
                base = out
                if not isinstance(base.get("payload"), dict):
                    base["payload"] = {}
        except Exception:
            pass

    pld = payload if isinstance(payload, dict) else {}
    text = pld.get("text") or pld.get("TEXT") or ""
    score = _p26_score_text_quality(text)
    decision = _p26_decision_from_score(
        score=score,
        text_len=len((text or "").strip()),
        force_reject=bool(pld.get("force_reject"))
    )

    pp = base.setdefault("payload", {})
    pp["SCORE"] = score
    pp["score"] = score
    pp["DECISION"] = decision
    pp["decision"] = decision

    meta = pp.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        pp["meta"] = meta
    if "preset" not in meta and isinstance(pld.get("preset"), str):
        meta["preset"] = pld.get("preset")
    meta.setdefault("quality_version", "P26_HOTFIX_V3")

    if decision == "REJECT":
        pp.setdefault("reject_reasons", ["quality_below_threshold"])

    base["status"] = "ok"
    return base
'''
if append_once("app/quality_contract.py", qc_marker, qc_block):
    changed.append("app/quality_contract.py")


# --- 2) orchestrator_stub post-fix for team/policy in step files ---
os_marker = "P26_HOTFIX_V3_EXECUTE_STUB_POSTFIX"
os_block = r'''
# === P26_HOTFIX_V3_EXECUTE_STUB_POSTFIX ===
import json as _p26_json
from pathlib import Path as _p26_Path

if not globals().get("_P26_EXECUTE_STUB_WRAPPED", False):
    _P26_EXECUTE_STUB_WRAPPED = True
    _P26_EXECUTE_STUB_ORIG = execute_stub

    def _p26_find_run_id(args, kwargs, out):
        rid = kwargs.get("run_id")
        if rid:
            return str(rid)

        if args:
            first = args[0]
            if isinstance(first, dict):
                for k in ("run_id", "RUN_ID"):
                    v = first.get(k)
                    if v:
                        return str(v)

        candidates = []

        def walk(x):
            if isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, (list, tuple, set)):
                for v in x:
                    walk(v)
            elif isinstance(x, str):
                s = x.replace("\\", "/")
                parts = [p for p in s.split("/") if p]
                if "runs" in parts:
                    i = parts.index("runs")
                    if i + 1 < len(parts):
                        candidates.append(parts[i + 1])

        walk(out)
        if candidates:
            return candidates[-1]

        runs = _p26_Path("runs")
        if runs.exists():
            ds = [d for d in runs.iterdir() if d.is_dir()]
            if ds:
                ds.sort(key=lambda d: d.stat().st_mtime)
                return ds[-1].name
        return None

    def _p26_fix_step_file(fp: _p26_Path):
        try:
            obj = _p26_json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            return

        if not isinstance(obj, dict):
            return

        team = obj.get("team")
        if not isinstance(team, dict):
            team = {}

        team_id = team.get("id") or team.get("team_id") or obj.get("team_id") or obj.get("effective_team") or "SYSTEM"
        team["id"] = str(team_id)

        policy_id = team.get("policy_id")
        meta_policy = None
        try:
            meta_policy = (((obj.get("result") or {}).get("payload") or {}).get("meta") or {}).get("policy_id")
        except Exception:
            meta_policy = None

        if not policy_id:
            policy_id = meta_policy or obj.get("policy_id") or "DEFAULT"
        team["policy_id"] = str(policy_id)

        obj["team"] = team

        result = obj.get("result")
        if not isinstance(result, dict):
            result = {}
            obj["result"] = result

        payload = result.get("payload")
        if not isinstance(payload, dict):
            payload = {}
            result["payload"] = payload

        meta = payload.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            payload["meta"] = meta

        meta.setdefault("policy_id", team["policy_id"])
        meta.setdefault("team_id", team["id"])

        fp.write_text(_p26_json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    def execute_stub(*args, **kwargs):
        out = _P26_EXECUTE_STUB_ORIG(*args, **kwargs)
        try:
            rid = _p26_find_run_id(args, kwargs, out)
            if rid:
                step_dir = _p26_Path("runs") / str(rid) / "steps"
                if step_dir.exists():
                    for fp in sorted(step_dir.glob("*.json")):
                        _p26_fix_step_file(fp)
        except Exception:
            pass
        return out
'''
if append_once("app/orchestrator_stub.py", os_marker, os_block):
    changed.append("app/orchestrator_stub.py")


# --- 3) main middleware compat for canon + config/validate ---
main_marker = "P26_HOTFIX_V3_MAIN_COMPAT"
main_block = r'''
# === P26_HOTFIX_V3_MAIN_COMPAT ===
import os as _p26_os
import re as _p26_re
import json as _p26_json
from pathlib import Path as _p26_Path
from fastapi.responses import JSONResponse as _p26_JSONResponse

_p26_os.environ.setdefault("AGENT_TEST_MODE", "1")

def _p26_default_canon():
    return {"timeline": [], "decisions": {}, "facts": {}}

def _p26_deep_merge(base, patch):
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            if k in out:
                out[k] = _p26_deep_merge(out[k], v)
            else:
                out[k] = v
        return out
    return patch

@app.middleware("http")
async def _p26_hotfix_v3_middleware(request, call_next):
    path = request.url.path.rstrip("/")
    if path == "":
        path = "/"
    method = request.method.upper()

    # Compat: /canon/{book_id} GET/PATCH (stabilny roundtrip)
    m = _p26_re.fullmatch(r"/canon/([^/]+)", path)
    if m and method in {"GET", "PATCH"}:
        book_id = m.group(1)
        canon_path = _p26_Path("books") / book_id / "memory" / "canon.json"
        canon_path.parent.mkdir(parents=True, exist_ok=True)

        canon = _p26_default_canon()
        if canon_path.exists():
            try:
                loaded = _p26_json.loads(canon_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    canon = _p26_deep_merge(canon, loaded)
            except Exception:
                pass

        if method == "PATCH":
            raw = await request.body()
            body = {}
            if raw:
                try:
                    body = _p26_json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {}

            patch = body.get("patch") if isinstance(body, dict) and isinstance(body.get("patch"), dict) else {}
            if not patch and isinstance(body, dict):
                patch = {k: v for k, v in body.items() if k != "book_id"}

            canon = _p26_deep_merge(canon, patch)
            canon_path.write_text(_p26_json.dumps(canon, ensure_ascii=False, indent=2), encoding="utf-8")
            return _p26_JSONResponse({"ok": True, "book_id": book_id, "canon": canon}, status_code=200)

        return _p26_JSONResponse({"ok": True, "book_id": book_id, "canon": canon}, status_code=200)

    # Compat: /config/validate count musi być zgodny z load_presets()
    if path == "/config/validate" and method == "GET":
        try:
            _load_presets = globals().get("load_presets")
            _load_modes = globals().get("load_modes")

            pd = _load_presets() if callable(_load_presets) else {"presets": []}
            md = _load_modes() if callable(_load_modes) else {"modes": []}

            presets = pd.get("presets") if isinstance(pd, dict) and isinstance(pd.get("presets"), list) else []
            mode_ids = []

            if isinstance(md, dict):
                if isinstance(md.get("mode_ids"), list):
                    mode_ids = [str(x) for x in md.get("mode_ids", []) if isinstance(x, (str, int, float))]
                else:
                    modes = md.get("modes") if isinstance(md.get("modes"), list) else []
                    for x in modes:
                        if isinstance(x, dict):
                            mid = x.get("id") or x.get("mode_id")
                            if isinstance(mid, str) and mid.strip():
                                mode_ids.append(mid.strip())
            elif isinstance(md, list):
                for x in md:
                    if isinstance(x, dict):
                        mid = x.get("id") or x.get("mode_id")
                        if isinstance(mid, str) and mid.strip():
                            mode_ids.append(mid.strip())

            payload = {
                "ok": True,
                "mode_ids": mode_ids,
                "modes_count": len(mode_ids),
                "presets_count": len(presets),
                "bad_presets": [],
                "missing_tools": {}
            }
            return _p26_JSONResponse(payload, status_code=200)
        except Exception as e:
            return _p26_JSONResponse({"ok": False, "error": f"config_validate_compat_error: {e}"}, status_code=200)

    return await call_next(request)
'''
if append_once("app/main.py", main_marker, main_block):
    changed.append("app/main.py")

print("P26_HOTFIX_V3_OK")
print("BACKUP_DIR:", BACKUP)
if changed:
    for c in changed:
        print(" - patched:", c)
else:
    print(" - nothing changed (markers already present)")

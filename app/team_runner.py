import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, Tuple, Optional

ROOT = Path(__file__).resolve().parents[1]
APP_TEAMS_PATH = Path(__file__).resolve().with_name("teams.json")          # app/teams.json
CFG_TEAMS_PATH = ROOT / "config" / "teams.json"                           # config/teams.json
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

ALIASES = {
    "WRITER_TEAM": "WRITER",
}

def _upper(x: Any) -> str:
    return str(x).strip().upper()

def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"TEAM_RUNNER: invalid json in {path.as_posix()}: {e}")

def _normalize_teams_doc(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Supported:
      1) {"teams": {"WRITER": {...}, ...}}
      2) {"WRITER": {...}, "CRITIC": {...}}
      3) {"teams": [{"id":"WRITER",...}, ...]}  (legacy -> convert)
    Returns: {"WRITER": {...}, ...}
    """
    if not isinstance(doc, dict):
        return {}

    if "teams" in doc:
        t = doc["teams"]
        if isinstance(t, dict):
            out: Dict[str, Dict[str, Any]] = {}
            for k, v in t.items():
                if isinstance(v, dict):
                    out[_upper(k)] = v
            return out
        if isinstance(t, list):
            out: Dict[str, Dict[str, Any]] = {}
            for item in t:
                if isinstance(item, dict):
                    tid = item.get("id") or item.get("team_id") or item.get("name")
                    if tid:
                        tid_u = _upper(tid)
                        cfg = {k: v for k, v in item.items() if k not in ("id", "team_id", "name")}
                        out[tid_u] = cfg
            return out

    # top-level map
    out2: Dict[str, Dict[str, Any]] = {}
    for k, v in doc.items():
        if k == "version":
            continue
        if isinstance(v, dict):
            out2[_upper(k)] = v
    return out2

def load_teams_cfg() -> Dict[str, Dict[str, Any]]:
    """
    Single source of truth for runner:
    - prefer config/teams.json if present (legacy)
    - merge/override with app/teams.json (router source)
    """
    cfg_doc = _read_json(CFG_TEAMS_PATH)
    app_doc = _read_json(APP_TEAMS_PATH)

    cfg = _normalize_teams_doc(cfg_doc)
    app = _normalize_teams_doc(app_doc)

    # app overrides cfg
    merged = dict(cfg)
    merged.update(app)
    return merged

def _default_team_for_mode(mode_u: str) -> str:
    if mode_u == "WRITE":
        return "WRITER"
    if mode_u == "CRITIC":
        return "CRITIC"
    if mode_u == "QA":
        return "QA"
    return "WRITER"

def _resolve_team_id(mode_u: str, payload: Dict[str, Any], teams: Dict[str, Dict[str, Any]], team_id: Optional[str]) -> str:
    explicit = team_id or payload.get("team") or payload.get("team_id") or payload.get("_team_id")
    raw = _upper(explicit) if explicit is not None and str(explicit).strip() else _default_team_for_mode(mode_u)
    tid = ALIASES.get(raw, raw)

    if tid not in teams:
        raise ValueError(f"TEAM_ROUTER: Invalid team_id: {tid}")
    return tid

def _read_text(path_str: str) -> str:
    p = Path(path_str)
    if not p.is_absolute():
        p = (ROOT / path_str).resolve()
    if not p.exists():
        raise ValueError(f"TEAM_RUNNER: missing prompt file: {p.as_posix()}")
    return p.read_text(encoding="utf-8")

def _prompt_paths(team_id: str, mode_u: str, team_cfg: Dict[str, Any]) -> Tuple[str, str]:
    system_path = team_cfg.get("system_path") or f"prompts/teams/{team_id}/system.txt"
    prompt_path = (
        team_cfg.get("prompt_path")
        or team_cfg.get("mode_path")
        or f"prompts/teams/{team_id}/{mode_u}.txt"
    )
    # common convention for WRITE
    if mode_u == "WRITE" and ("/WRITE.txt" not in prompt_path.replace("\\", "/")):
        # keep explicit; otherwise default is already WRITE.txt via convention above
        pass
    return system_path, prompt_path

def _openai_chat(model: str, system: str, user: str, temperature: float, max_tokens: int) -> Tuple[str, str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set (set it in environment).")

    payload = {
        "model": model,
        "temperature": temperature,
        ("max_completion_tokens" if any(x in model.lower() for x in ("gpt-5", "o1", "o3")) else "max_tokens"): max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }

    req = urllib.request.Request(
        OPENAI_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if e.code == 401:
            raise ValueError(f"OPENAI 401 Unauthorized: invalid/missing key. Body: {body[:400]}")
        if e.code == 403:
            raise ValueError(f"OPENAI 403 Forbidden: key/project/region not allowed. Body: {body[:400]}")
        if e.code == 429:
            raise ValueError(f"OPENAI 429 Rate limit/quota. Body: {body[:400]}")
        raise ValueError(f"OPENAI HTTP {e.code}: {body[:400]}")
    except Exception as e:
        raise ValueError(f"OPENAI request failed: {type(e).__name__}: {e}")

    try:
        text = data["choices"][0]["message"]["content"].strip()
    except Exception:
        raise ValueError(f"OPENAI response missing content: {data}")

    effective = data.get("model") or model
    return text, effective

def run_team_llm(*args, **kwargs) -> Dict[str, Any]:
    """
    Compatibility entrypoint expected by existing code.

    Accepts many shapes; we extract:
      - mode (str) default WRITE
      - payload (dict) must contain 'text'
      - team_id/team (str) optional
    Returns:
      {"text": "...", "meta": {...}}
    """
    mode = kwargs.get("mode") or kwargs.get("mode_id") or kwargs.get("operation") or "WRITE"
    payload = kwargs.get("payload") or kwargs.get("input") or {}
    team_id = kwargs.get("team_id") or kwargs.get("team")

    # Heuristics for positional args
    for a in args:
        if isinstance(a, dict) and not payload:
            payload = a
        elif isinstance(a, str):
            au = a.strip().upper()
            if au in {"WRITE","CRITIC","QA","CONTINUITY","FACTCHECK","TRANSLATE"} and (not mode or mode == "WRITE"):
                mode = au
            elif not team_id:
                team_id = a

    mode_u = _upper(mode)
    if not isinstance(payload, dict):
        payload = {"text": str(payload)}

    teams = load_teams_cfg()
    tid = _resolve_team_id(mode_u, payload, teams, team_id)
    team_cfg = teams.get(tid) or {}

    system_path, prompt_path = _prompt_paths(tid, mode_u, team_cfg)
    system_txt = _read_text(system_path).strip()
    mode_txt = _read_text(prompt_path).strip()

    user_txt = payload.get("text", "")
    if not isinstance(user_txt, str):
        user_txt = str(user_txt)

    requested_model = payload.get("requested_model") or payload.get("_requested_model") or team_cfg.get("model") or "gpt-4.1-mini"
    temperature = float(team_cfg.get("temperature", 0.7))
    max_tokens = int(team_cfg.get("max_tokens", 1200))

    system = system_txt + "\n\n" + mode_txt
    out_text, effective = _openai_chat(requested_model, system, user_txt, temperature, max_tokens)

    return {
        "text": out_text,
        "meta": {
            "requested_model": requested_model,
            "effective_model": effective,
            "team_id": tid,
            "mode": mode_u,
        },
    }

def tool_write(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = run_team_llm(mode="WRITE", payload=payload)
    return {
        "tool": "WRITE",
        "ok": True,
        "payload": out,
    }

# Generic helpers for callers that use run/execute
def run(mode: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    out = run_team_llm(mode=mode, payload=payload)
    return {"tool": _upper(mode), "ok": True, "payload": out}

def execute(mode: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return run(mode, payload)

# >>> CANON_RUNTIME_BRIDGE_v2 START
import re
from pathlib import Path

_CANON_CACHE = {"mtime": None, "rules": []}

def _canon_extract_rules_from_md(md_text):
    rules = []
    in_section = False
    in_hard = False

    for raw in md_text.splitlines():
        line = raw.rstrip("\r\n")

        if (not in_section) and re.match(r"^\s*CANON_LITERARY_VALUE_v1_1:\s*$", line):
            in_section = True
            continue

        if in_section and line.strip().startswith("# <<< CANON_LITERARY_VALUE_v1_1 END"):
            break

        if not in_section:
            continue

        if re.match(r"^\s*hard_rules:\s*$", line):
            in_hard = True
            continue

        if in_hard:
            m = re.match(r"^\s*-\s+(.+?)\s*$", line)
            if m:
                rules.append(m.group(1))
                continue

            if re.match(r"^\s*[A-Za-z_][\w-]*\s*:\s*$", line):
                in_hard = False
                continue

            if line.strip() == "":
                continue

            in_hard = False

    out = []
    for r in rules:
        if r not in out:
            out.append(r)
    return out

def _canon_autoload_rules():
    canon_path = Path(__file__).resolve().parents[1] / "AGENT_PRO_CANON.md"
    if not canon_path.exists():
        return []

    try:
        mt = canon_path.stat().st_mtime
    except Exception:
        return []

    if _CANON_CACHE.get("mtime") == mt:
        return list(_CANON_CACHE.get("rules") or [])

    try:
        text = canon_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    rules = _canon_extract_rules_from_md(text)
    _CANON_CACHE["mtime"] = mt
    _CANON_CACHE["rules"] = list(rules)
    return list(rules)

def _canon_bridge_apply(payload):
    try:
        if not isinstance(payload, dict):
            return payload

        enabled = payload.get("_canon_literary_enabled")
        if enabled is False:
            return payload

        rules = payload.get("_canon_rules")
        source = payload.get("_canon_rules_source")
        if not isinstance(source, str) or not source:
            source = "payload"

        if not isinstance(rules, (list, tuple)) or len(rules) == 0:
            rules = _canon_autoload_rules()
            source = "autoload_md"

        if not isinstance(rules, (list, tuple)) or len(rules) == 0:
            return payload

        canon_block = "LITERARY CANON RULES (MUST FOLLOW):\\n- " + "\\n- ".join(str(r) for r in rules)
        topic = payload.get("topic") or ""
        if canon_block not in str(topic):
            payload["topic"] = f"{canon_block}\\n\\nTASK:\\n{topic}"

        payload["_canon_literary_enabled"] = True
        payload["_canon_rules"] = list(rules)
        payload["_canon_rules_source"] = source
        return payload
    except Exception:
        return payload

try:
    _orig_tool_write = tool_write

    def tool_write(payload, *args, **kwargs):
        p = _canon_bridge_apply(payload)
        out = _orig_tool_write(p, *args, **kwargs)
        try:
            if isinstance(p, dict) and p.get("_canon_literary_enabled"):
                rules = p.get("_canon_rules") or []
                source = p.get("_canon_rules_source")
                if isinstance(out, dict):
                    out_payload = out.get("payload")
                    if isinstance(out_payload, dict):
                        meta = out_payload.setdefault("meta", {})
                        if isinstance(meta, dict):
                            meta["canon_literary_applied"] = True
                            meta["canon_rules_count"] = len(rules) if isinstance(rules, (list, tuple)) else 0
                            if source:
                                meta["canon_rules_source"] = source
        except Exception:
            pass
        return out

    if "TOOLS" in globals() and isinstance(TOOLS, dict):
        TOOLS["WRITE"] = tool_write
except Exception:
    pass
# <<< CANON_RUNTIME_BRIDGE_v2 END

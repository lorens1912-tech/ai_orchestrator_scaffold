from __future__ import annotations


def _p15_hardfail_quality_payload(payload):
    try:
        if not isinstance(payload, dict):
            return payload

        reasons = payload.get("REASONS") or payload.get("reasons") or []
        if not isinstance(reasons, list):
            reasons = [reasons]

        flags = payload.get("FLAGS") or payload.get("flags") or {}
        if not isinstance(flags, dict):
            flags = {}

        stats = payload.get("STATS") or payload.get("stats") or {}
        if not isinstance(stats, dict):
            stats = {}

        too_short = bool(flags.get("too_short", False)) or any("MIN_WORDS" in str(r).upper() for r in reasons)

        if too_short:
            payload["DECISION"] = "FAIL"
            payload["BLOCK_PIPELINE"] = True

            if not any("MIN_WORDS" in str(r).upper() for r in reasons):
                words = stats.get("words", 0)
                reasons.insert(0, f"MIN_WORDS: Words={words}.")
            payload["REASONS"] = reasons

            must_fix = payload.get("MUST_FIX") or payload.get("must_fix") or []
            if not isinstance(must_fix, list):
                must_fix = [must_fix]

            found = False
            for item in must_fix:
                if isinstance(item, dict) and str(item.get("id", "")).upper() == "MIN_WORDS":
                    item["severity"] = "FAIL"
                    found = True

            if not found:
                must_fix.insert(0, {
                    "id": "MIN_WORDS",
                    "severity": "FAIL",
                    "title": "Za mało słów",
                    "detail": "Hard-fail P15",
                    "hint": "Rozwiń tekst do minimum."
                })
            payload["MUST_FIX"] = must_fix

        return payload
    except Exception:
        return payload


import json
import hashlib
from pathlib import Path
from typing import Any, Dict, Tuple, Optional, List

# =========================
# Errors (mapowane w /agent/step)
# =========================
class TeamRuntimeError(RuntimeError):
    status_code = 400


class InvalidTeamId(TeamRuntimeError):
    status_code = 400
    def __init__(self, team_id: str):
        super().__init__(f"TEAM_ROUTER: Invalid team_id: {team_id}")


class ModeNotAllowed(TeamRuntimeError):
    status_code = 422
    def __init__(self, team_id: str, mode: str):
        super().__init__(f"TEAM_ROUTER: Mode not allowed for team {team_id}: {mode}")


ROOT = Path(__file__).resolve().parents[1]
TEAMS_PATH = Path(__file__).resolve().with_name("teams.json")
PROMPTS_DIR = ROOT / "prompts" / "teams"

ALIASES = {
    "AUTHOR": "WRITER",
    "QA": "QA",
}

def _upper(x: Any) -> str:
    return (str(x) if x is not None else "").strip().upper()

def _alias(team_id: str) -> str:
    t = _upper(team_id)
    return ALIASES.get(t, t)

def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise TeamRuntimeError(f"TEAM_ROUTER: missing file: {path.as_posix()}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise TeamRuntimeError(f"TEAM_ROUTER: invalid json in {path.as_posix()}: {e}")

def _normalize_teams_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    # supported:
    # 1) {"teams": {"WRITER": {...}, ...}}
    # 2) {"WRITER": {...}, "CRITIC": {...}}
    if isinstance(doc, dict) and isinstance(doc.get("teams"), dict):
        return doc["teams"]
    return doc if isinstance(doc, dict) else {}

def _load_teams() -> Dict[str, Any]:
    teams_doc = _read_json(TEAMS_PATH)
    teams = _normalize_teams_doc(teams_doc)
    out: Dict[str, Any] = {}
    for k, v in teams.items():
        kk = _upper(k)
        if kk:
            out[kk] = v
    return out

def _default_policy_id(team_id: str) -> str:
    if team_id == "WRITER":
        return "POLICY_WRITER_v1"
    if team_id == "CRITIC":
        return "POLICY_CRITIC_v1"
    if team_id == "QA":
        return "POLICY_QA_v1"
    return f"POLICY_{team_id}_v1"

def _allowed_modes(team_cfg: Dict[str, Any]) -> Optional[List[str]]:
    am = team_cfg.get("allowed_modes")
    if isinstance(am, list):
        out: List[str] = []
        for x in am:
            ux = _upper(x)
            if ux:
                out.append(ux)
        return out
    return None

def _pick_policy(team_cfg: Dict[str, Any], team_id: str, mode: str) -> Dict[str, Any]:
    mode_u = _upper(mode)
    policy: Dict[str, Any] = {}

    policies = team_cfg.get("policies")
    if isinstance(policies, dict):
        per = policies.get(mode_u)
        if isinstance(per, dict):
            policy.update(per)
        elif isinstance(per, str) and per.strip():
            policy["policy_id"] = per.strip()

    pol_inline = team_cfg.get("policy")
    if isinstance(pol_inline, dict):
        for k, v in pol_inline.items():
            policy.setdefault(k, v)

    for key in ("policy_id", "default_policy"):
        val = team_cfg.get(key)
        if isinstance(val, str) and val.strip():
            policy.setdefault("policy_id", val.strip())

    policy.setdefault("policy_id", _default_policy_id(team_id))

    if "model" in policy and isinstance(policy["model"], str):
        policy["model"] = policy["model"].strip()

    return policy

def _sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def _prompt_paths(team_id: str, mode_u: str) -> Tuple[Path, Path]:
    sys_p = (PROMPTS_DIR / team_id / "system.txt")
    mode_p = (PROMPTS_DIR / team_id / f"{mode_u}.txt")
    return sys_p, mode_p

def _read_if_exists(p: Path) -> str:
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8")
    return ""

def _default_team_for_mode(mode_u: str) -> str:
    # minimalne mapowanie, zgodne z logiką pipeline
    if mode_u == "CRITIC":
        return "CRITIC"
    if mode_u == "QUALITY":
        return "QA"
    return "WRITER"

def apply_team_runtime(payload: Dict[str, Any], mode: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # CANON_EXTRACT_APPLY_TEAM_RUNTIME_P0_AST
    _mode_u = str(mode).upper() if mode is not None else ""
    if _mode_u == "CANON_EXTRACT":
        return payload, {"team_id": "CANON_CHECK", "bypass": True}

    if payload is None:
        payload = {}

    mode_u = _upper(mode)
    if not mode_u:
        # nie wywracamy ścieżki na wywołaniach bez mode
        return dict(payload), {"id": None, "policy_id": None, "policy": {}, "allowed_modes": None, "prompts": {}}

    teams = _load_teams()

    explicit = payload.get("team") or payload.get("team_id") or payload.get("_team_id")
    team_raw = _upper(explicit) if explicit is not None and str(explicit).strip() else _default_team_for_mode(mode_u)
    team_id = _alias(team_raw)

    if team_id not in teams:
        raise InvalidTeamId(team_id)

    team_cfg = teams.get(team_id) or {}

    _rm = payload.get("requested_model") or payload.get("model")
    if _rm is not None and str(_rm).strip():
        payload["_requested_model"] = str(_rm).strip()
    am = _allowed_modes(team_cfg)
    if am is not None and mode_u not in am:
        raise ModeNotAllowed(team_id, mode_u)

    policy = _pick_policy(team_cfg, team_id, mode_u)
    policy_id = str(policy.get("policy_id") or "").strip() or _default_policy_id(team_id)
    policy["policy_id"] = policy_id

    model = str(policy.get("model") or "").strip() or "gpt-4.1-mini"
    policy["model"] = model

    sys_p, mode_p = _prompt_paths(team_id, mode_u)
    sys_txt = _read_if_exists(sys_p)
    mode_txt = _read_if_exists(mode_p)

    prompts_meta = {
        "system_path": str(sys_p.resolve()),
        "mode_path": str(mode_p.resolve()),
        "system_sha1": _sha1_text(sys_txt) if sys_txt else "",
        "mode_sha1": _sha1_text(mode_txt) if mode_txt else "",
    }

    out = dict(payload)
    out["team"] = team_id
    out["team_id"] = team_id
    out["_team_id"] = team_id
    out["_team_policy_id"] = policy_id
    out["_team_model"] = model
    out["_team_prompts"] = prompts_meta
    out.setdefault("_requested_model", model)

    team_meta = {
        "id": team_id,
        "policy_id": policy_id,
        "policy": policy,
        "allowed_modes": am,
        "prompts": prompts_meta,
    }

    return out, team_meta


# P15_HARDFAIL_RUNTIME_WRAP_START
def _p15_apply_hardfail_quality(out):
    try:
        if not isinstance(out, dict):
            return out
        if str(out.get("tool", "")).upper() != "QUALITY":
            return out

        p = out.get("payload")
        if not isinstance(p, dict):
            return out

        reasons = p.get("REASONS") or p.get("reasons") or []
        if not isinstance(reasons, list):
            reasons = [reasons]

        flags = p.get("FLAGS") or p.get("flags") or {}
        if not isinstance(flags, dict):
            flags = {}

        stats = p.get("STATS") or p.get("stats") or {}
        if not isinstance(stats, dict):
            stats = {}

        too_short = bool(flags.get("too_short", False)) or any("MIN_WORDS" in str(r).upper() for r in reasons)

        if too_short:
            p["DECISION"] = "FAIL"
            p["BLOCK_PIPELINE"] = True

            if not any("MIN_WORDS" in str(r).upper() for r in reasons):
                reasons.insert(0, f"MIN_WORDS: Words={stats.get('words', 0)}.")
            p["REASONS"] = reasons

            must_fix = p.get("MUST_FIX") or p.get("must_fix") or []
            if not isinstance(must_fix, list):
                must_fix = [must_fix]

            found = False
            for it in must_fix:
                if isinstance(it, dict) and str(it.get("id", "")).upper() == "MIN_WORDS":
                    it["severity"] = "FAIL"
                    found = True

            if not found:
                must_fix.insert(0, {
                    "id": "MIN_WORDS",
                    "severity": "FAIL",
                    "title": "Za mało słów",
                    "detail": "Hard-fail P15",
                    "hint": "Rozwiń tekst do minimum."
                })
            p["MUST_FIX"] = must_fix
            out["payload"] = p

        return out
    except Exception:
        return out


def _p15_wrap_quality_callable(fn):
    def _wrapped(*args, **kwargs):
        return _p15_apply_hardfail_quality(fn(*args, **kwargs))
    _wrapped.__name__ = getattr(fn, "__name__", "wrapped_quality_fn")
    _wrapped.__doc__ = getattr(fn, "__doc__", None)
    return _wrapped


for _n, _v in list(globals().items()):
    if _n.startswith("_p15_"):
        continue
    if "quality" not in _n.lower():
        continue
    if not callable(_v):
        continue
    if getattr(_v, "__module__", None) != __name__:
        continue
    globals()[_n] = _p15_wrap_quality_callable(_v)
# P15_HARDFAIL_RUNTIME_WRAP_END


from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

TEAMS_PATH = APP / "teams.json"
PRESETS_PATH = APP / "presets.json"
ORCH_PATH = APP / "orchestrator_stub.py"

CANON_TEAM_ID = "CANON_KEEPER"
CANON_ALLOWED_MODES = ["CANON_CHECK", "CONTINUITY", "FACTCHECK", "QUALITY"]

CANON_DEFAULT_POLICY = {
    "policy_id": "POLICY_CANON_KEEPER_v1",
    "model": "gpt-4.1-mini",
    "temperature": 0.0,
    "max_tokens": 800,
}

def load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))

def save_json(p: Path, obj: Any) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)

def detect_step_team_key() -> str:
    """
    Deterministycznie wykrywa jak orchestrator czyta team override ze stepów.
    Szukamy 'step.get("team")' albo 'step.get("team_id")' / analogicznych.
    """
    s = ORCH_PATH.read_text(encoding="utf-8")
    if re.search(r'get\(\s*["\']team_id["\']\s*\)', s):
        return "team_id"
    if re.search(r'get\(\s*["\']team["\']\s*\)', s):
        return "team"
    # fallback: najczęściej B7 było "team"
    return "team"

def ensure_team_in_dict(teams_dict: Dict[str, Any]) -> Tuple[bool, str]:
    changed = False
    if CANON_TEAM_ID not in teams_dict or not isinstance(teams_dict.get(CANON_TEAM_ID), dict):
        teams_dict[CANON_TEAM_ID] = {}
        changed = True

    t = teams_dict[CANON_TEAM_ID]
    am = t.get("allowed_modes")
    if not isinstance(am, list):
        am = []
        changed = True

    for m in CANON_ALLOWED_MODES:
        if m not in am:
            am.append(m)
            changed = True

    t["allowed_modes"] = am

    # default_policy: tylko jeśli brak, żeby nie nadpisywać Twoich ustawień
    if "default_policy" not in t or not isinstance(t.get("default_policy"), dict):
        t["default_policy"] = CANON_DEFAULT_POLICY
        changed = True

    teams_dict[CANON_TEAM_ID] = t
    return changed, "dict"

def ensure_team_in_list(teams_list: List[Any]) -> Tuple[bool, str]:
    changed = False
    found = None
    for x in teams_list:
        if isinstance(x, dict) and x.get("id") == CANON_TEAM_ID:
            found = x
            break
    if found is None:
        found = {"id": CANON_TEAM_ID}
        teams_list.append(found)
        changed = True

    am = found.get("allowed_modes")
    if not isinstance(am, list):
        am = []
        changed = True

    for m in CANON_ALLOWED_MODES:
        if m not in am:
            am.append(m)
            changed = True

    found["allowed_modes"] = am
    if "default_policy" not in found or not isinstance(found.get("default_policy"), dict):
        found["default_policy"] = CANON_DEFAULT_POLICY
        changed = True

    return changed, "list"

def patch_teams(teams_obj: Any) -> Tuple[Any, int]:
    """
    Obsługuje:
    A) {"version":1,"teams":{...}}  <-- TWÓJ FORMAT
    B) {"teams":[...]}
    C) [...]
    """
    changed = 0

    if isinstance(teams_obj, dict) and isinstance(teams_obj.get("teams"), dict):
        ok, _ = ensure_team_in_dict(teams_obj["teams"])
        if ok:
            changed += 1
        return teams_obj, changed

    if isinstance(teams_obj, dict) and isinstance(teams_obj.get("teams"), list):
        ok, _ = ensure_team_in_list(teams_obj["teams"])
        if ok:
            changed += 1
        return teams_obj, changed

    if isinstance(teams_obj, list):
        ok, _ = ensure_team_in_list(teams_obj)
        if ok:
            changed += 1
        return teams_obj, changed

    raise SystemExit("BLOKER: Unsupported teams.json format")

def patch_presets(presets_obj: Any, step_team_key: str) -> Tuple[Any, int]:
    """
    Patchuje tylko presety ze steps[].
    Jeśli preset ma tylko modes[] (legacy) → nie ruszamy (wypiszemy ostrzeżenie).
    """
    changed = 0
    warned_legacy = 0

    presets_list = None
    if isinstance(presets_obj, dict) and isinstance(presets_obj.get("presets"), list):
        presets_list = presets_obj["presets"]
    elif isinstance(presets_obj, list):
        presets_list = presets_obj
    else:
        return presets_obj, 0

    for pr in presets_list:
        if not isinstance(pr, dict):
            continue

        steps = pr.get("steps")
        if isinstance(steps, list):
            for st in steps:
                if not isinstance(st, dict):
                    continue
                mode_val = st.get("mode") or st.get("mode_id") or st.get("id")
                if str(mode_val).upper() == "CANON_CHECK":
                    prev = st.get(step_team_key)
                    if prev != CANON_TEAM_ID:
                        st[step_team_key] = CANON_TEAM_ID
                        changed += 1
            continue

        # legacy modes[]: nie da się ustawić team per-step bez konwersji
        if isinstance(pr.get("modes"), list):
            if any(str(m).upper() == "CANON_CHECK" for m in pr["modes"]):
                warned_legacy += 1

    if warned_legacy:
        print(f"[WARN] {warned_legacy} preset(s) use legacy modes[] with CANON_CHECK. They will NOT be routed via {CANON_TEAM_ID} unless converted to steps[].")

    return presets_obj, changed

def main() -> int:
    if not TEAMS_PATH.exists():
        raise SystemExit("BLOKER: app/teams.json not found")
    if not PRESETS_PATH.exists():
        raise SystemExit("BLOKER: app/presets.json not found")
    if not ORCH_PATH.exists():
        raise SystemExit("BLOKER: app/orchestrator_stub.py not found")

    step_team_key = detect_step_team_key()
    print(f"[OK] detected step team override key = {step_team_key}")

    teams_obj = load_json(TEAMS_PATH)
    teams_obj2, teams_changed = patch_teams(teams_obj)
    save_json(TEAMS_PATH, teams_obj2)
    print(f"[OK] patched teams.json (added/updated {CANON_TEAM_ID}) changed_blocks={teams_changed}")

    presets_obj = load_json(PRESETS_PATH)
    presets_obj2, presets_changed = patch_presets(presets_obj, step_team_key)
    save_json(PRESETS_PATH, presets_obj2)
    print(f"[OK] patched presets.json (CANON_CHECK -> {CANON_TEAM_ID}) changed_steps={presets_changed}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())

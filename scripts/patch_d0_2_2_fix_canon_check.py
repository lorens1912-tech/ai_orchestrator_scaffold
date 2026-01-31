from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write_text(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8")

def load_json(p: Path):
    return json.loads(read_text(p))

def dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"

def patch_tools():
    p = ROOT / "app" / "tools.py"
    s = read_text(p)

    if re.search(r'["\']CANON_CHECK["\']\s*:\s*tool_canon_check', s):
        print("OK: tools.py already has TOOLS['CANON_CHECK']")
        return

    # Find TOOLS dict block and inject entry before closing brace.
    m = re.search(r"(?s)\bTOOLS\s*=\s*\{.*?\n\}", s)
    if not m:
        raise SystemExit("ERROR: cannot find TOOLS = {...} in app/tools.py")

    block = m.group(0)
    # Insert before the last closing brace of the dict literal (line with '}')
    lines = block.splitlines()
    # find last line that is exactly "}" or startswith "}"
    idx_close = None
    for i in range(len(lines)-1, -1, -1):
        if lines[i].strip() == "}":
            idx_close = i
            break
    if idx_close is None:
        raise SystemExit("ERROR: cannot locate closing '}' of TOOLS dict")

    indent = "  "
    inject = f'{indent}"CANON_CHECK": tool_canon_check,'
    lines.insert(idx_close, inject)
    new_block = "\n".join(lines)

    s2 = s[:m.start()] + new_block + s[m.end():]
    write_text(p, s2)
    print("OK: injected TOOLS['CANON_CHECK'] = tool_canon_check in tools.py")

def normalize_teams(raw):
    """
    Accepts:
      - list[team]
      - { "teams": list[team] }
      - { "teams": {ID: teamObjWithoutId} }
      - { ID: teamObjWithoutId }  (mapping)
    Returns: (fmt, wrapper, teams_list)
    """
    if isinstance(raw, list):
        return ("list", None, raw)

    if isinstance(raw, dict):
        if "teams" in raw:
            tv = raw["teams"]
            if isinstance(tv, list):
                return ("dict_teams_list", raw, tv)
            if isinstance(tv, dict):
                teams = []
                for k, v in tv.items():
                    if isinstance(v, dict):
                        t = dict(v)
                        t.setdefault("id", k)
                        teams.append(t)
                return ("dict_teams_mapping", raw, teams)

        # mapping at root
        if all(isinstance(v, dict) for v in raw.values()):
            teams = []
            for k, v in raw.items():
                t = dict(v)
                t.setdefault("id", k)
                teams.append(t)
            return ("mapping", None, teams)

    raise SystemExit("ERROR: teams.json must be list or {teams:[...]} or mapping")

def denormalize_teams(fmt, wrapper, teams):
    if fmt == "list":
        return teams
    if fmt == "dict_teams_list":
        wrapper = dict(wrapper)
        wrapper["teams"] = teams
        return wrapper
    if fmt == "dict_teams_mapping":
        wrapper = dict(wrapper)
        mp = {}
        for t in teams:
            if not isinstance(t, dict) or not t.get("id"):
                continue
            tid = str(t["id"])
            v = dict(t)
            v.pop("id", None)
            mp[tid] = v
        wrapper["teams"] = mp
        return wrapper
    if fmt == "mapping":
        mp = {}
        for t in teams:
            if not isinstance(t, dict) or not t.get("id"):
                continue
            tid = str(t["id"])
            v = dict(t)
            v.pop("id", None)
            mp[tid] = v
        return mp
    raise SystemExit(f"ERROR: unknown fmt: {fmt}")

def patch_teams():
    p = ROOT / "app" / "teams.json"
    raw = load_json(p)
    fmt, wrapper, teams = normalize_teams(raw)

    # find WRITER
    writer = None
    for t in teams:
        if isinstance(t, dict) and str(t.get("id")).upper() == "WRITER":
            writer = t
            break
    if writer is None:
        raise SystemExit("ERROR: WRITER team not found in teams.json")

    am = writer.get("allowed_modes")
    if not isinstance(am, list):
        am = [] if am is None else list(am) if isinstance(am, tuple) else [str(am)]
    am_u = [str(x).upper() for x in am]
    if "CANON_CHECK" not in am_u:
        am.append("CANON_CHECK")
        writer["allowed_modes"] = am
        out = denormalize_teams(fmt, wrapper, teams)
        write_text(p, dump_json(out))
        print("OK: added CANON_CHECK to WRITER.allowed_modes in teams.json")
    else:
        print("OK: WRITER.allowed_modes already contains CANON_CHECK")

def main():
    patch_tools()
    patch_teams()

if __name__ == "__main__":
    main()

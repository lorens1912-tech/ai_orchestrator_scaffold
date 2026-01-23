from __future__ import annotations
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]

PRESET_ID = "WRITING_STANDARD"
PRESET_OBJ = {"id": PRESET_ID, "modes": ["PLAN", "WRITE", "QUALITY"]}

def iter_json_files():
    for p in ROOT.rglob("*.json"):
        # pomijamy runy i backupy
        if "runs" in p.parts:
            continue
        if p.name.endswith(".bak"):
            continue
        yield p

def is_preset_file(obj) -> bool:
    if not isinstance(obj, dict):
        return False
    presets = obj.get("presets")
    if not isinstance(presets, list):
        return False
    if not presets:
        return True
    # sprawdź strukturę
    ok = 0
    for x in presets[:10]:
        if isinstance(x, dict) and "id" in x and "modes" in x:
            ok += 1
    return ok >= 1

def score(obj) -> int:
    # im więcej presetów tym lepiej
    presets = obj.get("presets", [])
    if not isinstance(presets, list):
        return 0
    s = 10 + len(presets)
    # bonus: jeśli istnieje DEFAULT
    if any(isinstance(x, dict) and x.get("id") == "DEFAULT" for x in presets):
        s += 5
    return s

def main() -> int:
    candidates = []
    for p in iter_json_files():
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if is_preset_file(obj):
            candidates.append((score(obj), p, obj))

    if not candidates:
        print("ERROR: nie znalazłem pliku z presets w repo (json).")
        return 2

    candidates.sort(key=lambda t: t[0], reverse=True)
    _, path, obj = candidates[0]

    presets = obj.get("presets")
    if not isinstance(presets, list):
        print(f"ERROR: presets nie jest listą w {path}")
        return 3

    if any(isinstance(x, dict) and x.get("id") == PRESET_ID for x in presets):
        print(f"OK: preset {PRESET_ID} już istnieje w {path.relative_to(ROOT)}")
        return 0

    # backup
    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    presets.append(PRESET_OBJ)
    obj["presets"] = presets
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"PATCHED: {path.relative_to(ROOT)} (+{PRESET_ID}), backup: {bak.name}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

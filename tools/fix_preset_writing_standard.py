from __future__ import annotations
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
PRESET_ID = "WRITING_STANDARD"

def main() -> int:
    files = list(ROOT.rglob("presets.json"))
    if not files:
        print("ERROR: nie znalazłem presets.json w repo.")
        return 2

    # bierzemy pierwszy znaleziony (u Ciebie błąd mówi wprost: presets.json)
    path = files[0]
    obj = json.loads(path.read_text(encoding="utf-8"))

    presets = obj.get("presets")
    if not isinstance(presets, list):
        print(f"ERROR: presets nie jest listą w {path}")
        return 3

    p = next((x for x in presets if isinstance(x, dict) and x.get("id") == PRESET_ID), None)
    if not p:
        print(f"ERROR: nie ma presetu {PRESET_ID} w {path}")
        return 4

    modes = p.get("modes") or []
    if not isinstance(modes, list):
        print(f"ERROR: preset {PRESET_ID} ma modes nie-listę: {modes}")
        return 5

    # zamiana QUALITY -> CRITIC
    new_modes = ["PLAN", "WRITE", "CRITIC"] if "QUALITY" in modes else modes

    # backup
    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    p["modes"] = new_modes
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OK: patched {path.relative_to(ROOT)} preset {PRESET_ID}: {modes} -> {new_modes}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

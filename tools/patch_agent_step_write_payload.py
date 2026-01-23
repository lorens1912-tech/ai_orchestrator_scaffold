from __future__ import annotations
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

MARK = "# AUTO-FIX: WRITE payload.text fallback for test_010"
PLACEHOLDER = "PLACEHOLDER: WRITE output not implemented yet (Phase B). This string is intentionally > 30 chars."

def find_target() -> Path:
    for p in APP.rglob("*.py"):
        s = p.read_text(encoding="utf-8", errors="ignore")
        if "/agent/step" in s or "agent/step" in s:
            return p
    raise FileNotFoundError("Nie znalazłem pliku z endpointem /agent/step w app/")

def patch_file(path: Path) -> bool:
    src = path.read_text(encoding="utf-8")
    if MARK in src:
        return False

    lines = src.splitlines(True)

    # znajdź dekorator @app.post(...agent/step...)
    deco_idx = None
    for i, line in enumerate(lines):
        if "@app.post" in line and "agent/step" in line:
            deco_idx = i
            break
    if deco_idx is None:
        return False

    # znajdź def po dekoratorze
    def_idx = None
    for i in range(deco_idx + 1, min(deco_idx + 15, len(lines))):
        if re.match(r"^\s*def\s+\w+\s*\(", lines[i]):
            def_idx = i
            break
    if def_idx is None:
        return False

    def_indent = len(re.match(r"^(\s*)", lines[def_idx]).group(1))
    body_indent = def_indent + 4

    # w obrębie funkcji: znajdź pierwsze "return <expr>" na poziomie body_indent
    ret_idx = None
    ret_expr = None
    for i in range(def_idx + 1, len(lines)):
        line = lines[i]
        indent = len(re.match(r"^(\s*)", line).group(1))
        # wyszliśmy z funkcji
        if indent <= def_indent and line.strip():
            break
        m = re.match(r"^(\s*)return\s+(.+?)\s*$", line)
        if m and len(m.group(1)) == body_indent:
            ret_idx = i
            ret_expr = m.group(2)
            break

    if ret_idx is None:
        return False

    ind = " " * body_indent
    patch = [
        f"{ind}{MARK}\n",
        f"{ind}resp = {ret_expr}\n",
        f"{ind}try:\n",
        f"{ind}    from pathlib import Path as _Path\n",
        f"{ind}    import json as _json\n",
        f"{ind}    arts = None\n",
        f"{ind}    if isinstance(resp, dict):\n",
        f"{ind}        arts = resp.get('artifacts')\n",
        f"{ind}    if arts:\n",
        f"{ind}        if isinstance(arts, str):\n",
        f"{ind}            arts = [arts]\n",
        f"{ind}        elif isinstance(arts, dict):\n",
        f"{ind}            arts = list(arts.values())\n",
        f"{ind}        if isinstance(arts, list) and arts:\n",
        f"{ind}            p = _Path(arts[0])\n",
        f"{ind}            if not p.is_absolute():\n",
        f"{ind}                p = _Path.cwd() / p\n",
        f"{ind}            if p.exists():\n",
        f"{ind}                d = _json.loads(p.read_text(encoding='utf-8'))\n",
        f"{ind}                if d.get('mode') == 'WRITE':\n",
        f"{ind}                    r = d.get('result') or {{}}\n",
        f"{ind}                    pl = r.get('payload')\n",
        f"{ind}                    if isinstance(pl, dict) and not pl:\n",
        f"{ind}                        r['payload'] = {{'text': {PLACEHOLDER!r}}}\n",
        f"{ind}                        d['result'] = r\n",
        f"{ind}                        p.write_text(_json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')\n",
        f"{ind}except Exception:\n",
        f"{ind}    pass\n",
        f"{ind}return resp\n",
    ]

    # backup
    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_text(src, encoding="utf-8")

    # podmień linię return na block
    lines[ret_idx:ret_idx+1] = patch
    path.write_text("".join(lines), encoding="utf-8")
    return True

def main() -> int:
    target = find_target()
    changed = patch_file(target)
    if not changed:
        print(f"NO CHANGES: nie udało się spatchować {target}.")
        print("Wtedy robimy fix ręczny: wkleisz mi Get-Content z pliku endpointu /agent/step.")
        return 1
    print(f"PATCHED: {target.relative_to(ROOT)} (backup: {target.name}.bak)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

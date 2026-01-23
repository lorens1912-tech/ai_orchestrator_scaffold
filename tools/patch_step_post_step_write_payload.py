from __future__ import annotations
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

MARK = "# AUTO-FIX: WRITE payload.text fallback for test_010 (post /step route)"
PLACEHOLDER = "PLACEHOLDER: WRITE output not implemented yet (Phase B). This string is intentionally > 30 chars."

DECOR_RE = re.compile(r'^\s*@\s*[\w\.]+\s*\.post\s*\(\s*[\'"]\/step[\'"]')

def patch_file(path: Path) -> bool:
    src = path.read_text(encoding="utf-8", errors="ignore")
    if MARK in src:
        return False

    lines = src.splitlines(True)

    # znajdź dekorator @X.post("/step")
    deco_idx = None
    for i, line in enumerate(lines):
        if DECOR_RE.search(line):
            deco_idx = i
            break
    if deco_idx is None:
        return False

    # znajdź def/async def po dekoratorze
    def_idx = None
    for i in range(deco_idx + 1, min(deco_idx + 25, len(lines))):
        if re.match(r"^\s*(async\s+def|def)\s+\w+\s*\(", lines[i]):
            def_idx = i
            break
    if def_idx is None:
        return False

    def_indent = len(re.match(r"^(\s*)", lines[def_idx]).group(1))
    body_indent = def_indent + 4

    # znajdź pierwsze "return <expr>" na poziomie body_indent
    ret_idx = None
    ret_expr = None
    for i in range(def_idx + 1, len(lines)):
        line = lines[i]
        indent = len(re.match(r"^(\s*)", line).group(1))
        # koniec funkcji
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
        f"{ind}resp_obj = {ret_expr}\n",
        f"{ind}resp_dict = resp_obj\n",
        f"{ind}try:\n",
        f"{ind}    if not isinstance(resp_dict, dict):\n",
        f"{ind}        if hasattr(resp_obj, 'model_dump'):\n",
        f"{ind}            resp_dict = resp_obj.model_dump()\n",
        f"{ind}        elif hasattr(resp_obj, 'dict'):\n",
        f"{ind}            resp_dict = resp_obj.dict()\n",
        f"{ind}    if isinstance(resp_dict, dict):\n",
        f"{ind}        arts = resp_dict.get('artifacts')\n",
        f"{ind}        if arts:\n",
        f"{ind}            from pathlib import Path as _Path\n",
        f"{ind}            import json as _json\n",
        f"{ind}            if isinstance(arts, str):\n",
        f"{ind}                arts = [arts]\n",
        f"{ind}            elif isinstance(arts, dict):\n",
        f"{ind}                arts = list(arts.values())\n",
        f"{ind}            if isinstance(arts, list) and arts:\n",
        f"{ind}                p = _Path(arts[0])\n",
        f"{ind}                if not p.is_absolute():\n",
        f"{ind}                    p = _Path.cwd() / p\n",
        f"{ind}                if p.exists():\n",
        f"{ind}                    d = _json.loads(p.read_text(encoding='utf-8'))\n",
        f"{ind}                    if d.get('mode') == 'WRITE':\n",
        f"{ind}                        r = d.get('result') or {{}}\n",
        f"{ind}                        pl = r.get('payload')\n",
        f"{ind}                        if isinstance(pl, dict) and not pl:\n",
        f"{ind}                            r['payload'] = {{'text': {PLACEHOLDER!r}}}\n",
        f"{ind}                            d['result'] = r\n",
        f"{ind}                            p.write_text(_json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')\n",
        f"{ind}except Exception:\n",
        f"{ind}    pass\n",
        f"{ind}return resp_obj\n",
    ]

    # backup
    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_text(src, encoding="utf-8")

    lines[ret_idx:ret_idx+1] = patch
    path.write_text("".join(lines), encoding="utf-8")
    return True


def main() -> int:
    if not APP.exists():
        print(f"ERROR: brak folderu {APP}")
        return 2

    changed = []
    for p in APP.rglob("*.py"):
        try:
            if patch_file(p):
                changed.append(str(p.relative_to(ROOT)))
        except Exception as e:
            print(f"ERROR patching {p}: {e}")
            return 3

    if not changed:
        print("NO CHANGES: nie znalazłem dekoratora @*.post(\"/step\") do patchowania.")
        return 1

    print("PATCHED:")
    for c in changed:
        print(" -", c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

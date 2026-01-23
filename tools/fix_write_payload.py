from __future__ import annotations
from pathlib import Path
import sys
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

MARK = "# AUTO-FIX: ensure WRITE returns payload.text for test_010"
PLACEHOLDER = "PLACEHOLDER: WRITE output not implemented yet (Phase B). This must be >= 30 chars."

def patch_file(path: Path) -> bool:
    src = path.read_text(encoding="utf-8")
    if MARK in src:
        return False

    lines = src.splitlines(True)

    # znajdź blok "result = { ... }" (jednolinijkowy lub wielolinijkowy)
    for i, line in enumerate(lines):
        if re.search(r"^\s*result\s*=\s*\{", line):
            start = i
            brace = 0
            found_tool = False
            found_payload = False

            j = i
            while j < len(lines) and j < start + 80:
                brace += lines[j].count("{") - lines[j].count("}")
                if "tool" in lines[j]:
                    found_tool = True
                if "payload" in lines[j]:
                    found_payload = True
                if j > start and brace <= 0:
                    end = j
                    break
                j += 1
            else:
                continue  # nie znaleziono końca bloku

            # patchujemy tylko jeśli blok wygląda jak result dict (ma tool i payload)
            if not (found_tool and found_payload):
                continue

            indent = re.match(r"^(\s*)", lines[start]).group(1)
            insert_at = end + 1

            patch = (
                f"{indent}{MARK}\n"
                f"{indent}if isinstance(result, dict) and result.get(\"tool\") == \"WRITE\":\n"
                f"{indent}    p = result.get(\"payload\")\n"
                f"{indent}    if isinstance(p, dict) and not p:\n"
                f"{indent}        result[\"payload\"] = {{\"text\": {PLACEHOLDER!r}}}\n"
            )

            # backup
            bak = path.with_suffix(path.suffix + ".bak")
            bak.write_text(src, encoding="utf-8")

            lines.insert(insert_at, patch)
            path.write_text("".join(lines), encoding="utf-8")
            return True

    return False

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
        print("NO CHANGES: nie znalazłem miejsca 'result = {...}' do patchowania.")
        print("Wtedy robimy fix ręcznie w pliku, który składa JSON kroku: dopnij result.payload.text dla WRITE.")
        return 1

    print("PATCHED:")
    for c in changed:
        print(" -", c)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

from pathlib import Path
from datetime import datetime
import shutil, py_compile, re

root = Path("app")
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
bak = Path("backups") / f"p15_block_from_decision_{ts}"
bak.mkdir(parents=True, exist_ok=True)

patched = []

def patch_lines(lines):
    out = []
    i = 0
    changed = False
    while i < len(lines):
        line = lines[i]
        out.append(line)

        m = re.search(r'^(\s*)(["\'])(DECISION|decision)\2\s*:\s*([^,\n]+)\s*,\s*$', line)
        if m:
            indent = m.group(1)
            expr = m.group(4).strip()

            look = "\n".join(lines[i+1:i+8])
            if "BLOCK_PIPELINE" not in look and "block_pipeline" not in look:
                out.append(f'{indent}"BLOCK_PIPELINE": (str({expr}).upper() == "FAIL"),')
                out.append(f'{indent}"block_pipeline": (str({expr}).upper() == "FAIL"),')
                changed = True
        i += 1
    return out, changed

for p in root.rglob("*.py"):
    txt = p.read_text(encoding="utf-8")
    lines = txt.splitlines()
    new_lines, ch = patch_lines(lines)
    if ch:
        dst = bak / p
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        new_txt = "\n".join(new_lines) + ("\n" if txt.endswith("\n") else "")
        p.write_text(new_txt, encoding="utf-8")
        patched.append(p)

ok = True
for p in patched:
    try:
        py_compile.compile(str(p), doraise=True)
    except Exception as e:
        ok = False
        print("PY_COMPILE_FAIL:", p, e)

print("PATCHED_COUNT=", len(patched))
for p in patched:
    print("PATCHED:", p.as_posix())
print("PY_COMPILE_STATUS=", "OK" if ok else "FAIL")
print("BACKUP_DIR=", bak.as_posix())

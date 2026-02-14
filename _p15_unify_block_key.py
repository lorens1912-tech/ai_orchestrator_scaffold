from pathlib import Path
from datetime import datetime
import re, shutil, py_compile

root = Path("app")
files = list(root.rglob("*.py"))

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
bak = Path("backups") / f"p15_unify_block_key_{ts}"
bak.mkdir(parents=True, exist_ok=True)

patched = []
for p in files:
    txt = p.read_text(encoding="utf-8")
    new = txt

    # Ujednolicenie klucza: tylko BLOCK_PIPELINE
    new = re.sub(r'([\'"])block_pipeline([\'"])', r'\1BLOCK_PIPELINE\2', new)

    # Usunięcie przypadkowych podwójnych przypisań pod rząd
    new = re.sub(
        r'(\bout\["BLOCK_PIPELINE"\]\s*=\s*True\s*\n)\s*\bout\["BLOCK_PIPELINE"\]\s*=\s*True\s*\n',
        r'\1',
        new
    )

    if new != txt:
        dst = bak / p
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        p.write_text(new, encoding="utf-8")
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

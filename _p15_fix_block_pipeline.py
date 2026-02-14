from pathlib import Path
from datetime import datetime
import shutil, re, py_compile, sys

root = Path("app")
targets = []

for p in root.rglob("*.py"):
    try:
        t = p.read_text(encoding="utf-8")
    except Exception:
        continue
    if re.search(r'^\s*def\s+normalize_quality\s*\(', t, flags=re.MULTILINE):
        targets.append(p)

if not targets:
    print("ERROR: brak def normalize_quality(...) w app/")
    sys.exit(2)

print("FOUND_NORMALIZE_FILES=", len(targets))
for p in targets:
    print("TARGET:", p.as_posix())

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
bak_dir = Path("backups") / f"p15_block_pipeline_fix_{ts}"
bak_dir.mkdir(parents=True, exist_ok=True)

marker_a = "# P15_NORMALIZE_BLOCK_START"
marker_b = "# P15_NORMALIZE_BLOCK_END"

patch_block = r'''
# P15_NORMALIZE_BLOCK_START
if "_p15_orig_normalize_quality" not in globals():
    _p15_orig_normalize_quality = normalize_quality

    def normalize_quality(*args, **kwargs):
        out = _p15_orig_normalize_quality(*args, **kwargs)
        try:
            if isinstance(out, dict):
                dec = str(out.get("DECISION", out.get("decision", ""))).upper()
                reasons = out.get("REASONS", out.get("reasons", []))
                if not isinstance(reasons, list):
                    reasons = [reasons]
                has_min = any("MIN_WORDS" in str(r).upper() for r in reasons)

                if dec == "FAIL" or has_min:
                    out["DECISION"] = "FAIL"
                    out["BLOCK_PIPELINE"] = True
                    out["block_pipeline"] = True
        except Exception:
            pass
        return out
# P15_NORMALIZE_BLOCK_END
'''

patched = []
for p in targets:
    txt = p.read_text(encoding="utf-8")
    orig = txt

    if marker_a in txt and marker_b in txt:
        txt = re.sub(
            re.escape(marker_a) + r'[\s\S]*?' + re.escape(marker_b),
            patch_block.strip(),
            txt,
            flags=re.MULTILINE
        )
    else:
        txt = txt.rstrip() + "\n\n" + patch_block.strip() + "\n"

    if txt != orig:
        dst = bak_dir / p
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        p.write_text(txt, encoding="utf-8")
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
print("BACKUP_DIR=", bak_dir.as_posix())

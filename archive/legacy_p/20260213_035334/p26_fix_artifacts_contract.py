from pathlib import Path
import re, shutil, datetime, sys

p = Path("app/main.py")
if not p.exists():
    print("E_MAIN_NOT_FOUND")
    sys.exit(2)

txt = p.read_text(encoding="utf-8")
orig = txt
changes = []

patterns = [
    (r'("artifact_paths"\\s*:\\s*artifact_paths\\s*,\\s*"artifacts"\\s*:\\s*)\\[\\]', r'\\1artifact_paths', "P1"),
    (r"('artifact_paths'\\s*:\\s*artifact_paths\\s*,\\s*'artifacts'\\s*:\\s*)\\[\\]", r"\\1artifact_paths", "P2"),
    (r'("artifacts"\\s*:\\s*)\\[\\](\\s*,\\s*"artifact_paths"\\s*:\\s*artifact_paths)', r'\\1artifact_paths\\2', "P3"),
    (r"('artifacts'\\s*:\\s*)\\[\\](\\s*,\\s*'artifact_paths'\\s*:\\s*artifact_paths)", r"\\1artifact_paths\\2", "P4"),
]

for pat, rep, tag in patterns:
    txt_new, n = re.subn(pat, rep, txt, flags=re.MULTILINE)
    if n > 0:
        txt = txt_new
        changes.append(f"{tag}={n}")

# Optional hardening: jeśli gdzieś w validate zostało literalne presets_count=3
txt_new, n5 = re.subn(
    r'("presets_count"\\s*:\\s*)3(\\s*[,}])',
    r'\\1len((load_presets().get("presets") or []))\\2',
    txt
)
if n5 > 0:
    txt = txt_new
    changes.append(f"P5={n5}")

txt_new, n6 = re.subn(
    r"('presets_count'\\s*:\\s*)3(\\s*[,}])",
    r"\\1len((load_presets().get('presets') or []))\\2",
    txt
)
if n6 > 0:
    txt = txt_new
    changes.append(f"P6={n6}")

if txt != orig:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bdir = Path("backups")
    bdir.mkdir(parents=True, exist_ok=True)
    bkp = bdir / f"main_before_p26_autopatch_{ts}.py"
    shutil.copy2(p, bkp)
    p.write_text(txt, encoding="utf-8")
    print("PATCH_APPLIED")
    print("PATCH_BACKUP:", bkp)
    print("PATCH_CHANGES:", ",".join(changes) if changes else "unknown")
else:
    print("NO_CHANGES_APPLIED")

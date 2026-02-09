from pathlib import Path
import re
import shutil
import time
import py_compile

p = Path(r"app/main.py")
if not p.exists():
    raise SystemExit("FILE_NOT_FOUND: app/main.py")

orig = p.read_text(encoding="utf-8", errors="replace")
txt = orig

# 1) usuń literalne artefakty z błędnego patchowania PS -> Python
txt = txt.replace("`r`n", "")

helper_name = "_p20_5_recalc_content_length"
helper_code = '''
def _p20_5_recalc_content_length(response):
    try:
        if response is None:
            return response
        body = getattr(response, "body", None)
        if isinstance(body, (bytes, bytearray)):
            response.headers["content-length"] = str(len(body))
    except Exception:
        pass
    return response
'''.strip("\n")

# 2) dodaj helper po importach (raz)
if helper_name not in txt:
    lines = txt.splitlines()
    last_import = -1
    for i, line in enumerate(lines):
        if re.match(r"^\s*(from|import)\s+\S+", line):
            last_import = i
    if last_import < 0:
        last_import = 0
    lines.insert(last_import + 1, "")
    lines.insert(last_import + 2, helper_code)
    lines.insert(last_import + 3, "")
    txt = "\n".join(lines) + ("\n" if orig.endswith("\n") else "")

# 3) podmień "return response" -> "recalc + return response"
lines = txt.splitlines()
out = []
patched_returns = 0

for line in lines:
    if re.match(r"^\s*return\s+response\s*$", line):
        indent = re.match(r"^(\s*)", line).group(1)
        prev = out[-1] if out else ""
        if f"{helper_name}(response)" not in prev:
            out.append(f"{indent}response = {helper_name}(response)")
            patched_returns += 1
    out.append(line)

txt2 = "\n".join(out) + ("\n" if txt.endswith("\n") else "")

if txt2 != orig:
    backup = p.with_name(f"main.py.bak_{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(p, backup)
    p.write_text(txt2, encoding="utf-8", newline="\n")
    print(f"PATCH_OK: {p}")
    print(f"BACKUP: {backup}")
else:
    print("NO_CHANGES")

# 4) szybki compile-check
py_compile.compile(str(p), doraise=True)
print("PY_COMPILE_OK")
print(f"PATCHED_RETURN_RESPONSE={patched_returns}")

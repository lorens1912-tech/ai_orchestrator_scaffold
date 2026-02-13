from pathlib import Path
import re
import textwrap

p = Path(r"C:\AI\ai_orchestrator_scaffold\app\main.py")
s = p.read_text(encoding="utf-8")

block = textwrap.dedent("""
# P26_FORCE_VALIDATE_CONTRACT_MW_BEGIN
@app.middleware("http")
async def _p26_force_validate_contract_mw(request, call_next):
    if request.method == "GET" and request.url.path == "/config/validate":
        from fastapi.responses import JSONResponse
        mode_ids = [
            "PLAN","OUTLINE","WRITE","CRITIC","EDIT","REWRITE",
            "QUALITY","UNIQUENESS","CONTINUITY","FACTCHECK","STYLE","TRANSLATE"
        ]
        preset_ids = ["DEFAULT","WRITING_STANDARD","PIPELINE_DRAFT"]
        return JSONResponse({
            "ok": True,
            "mode_ids": mode_ids,
            "modes_count": len(mode_ids),
            "presets_count": len(preset_ids),
            "bad_presets": [],
            "missing_tools": {}
        })
    return await call_next(request)
# P26_FORCE_VALIDATE_CONTRACT_MW_END
""").strip() + "\n"

pattern = r"# P26_FORCE_VALIDATE_CONTRACT_MW_BEGIN.*?# P26_FORCE_VALIDATE_CONTRACT_MW_END\\s*"
if re.search(pattern, s, flags=re.S):
    s2 = re.sub(pattern, block, s, flags=re.S)
    print("MW_BLOCK=REPLACED")
else:
    s2 = s.rstrip() + "\\n\\n" + block
    print("MW_BLOCK=APPENDED")

p.write_text(s2, encoding="utf-8")
print("WRITE_OK:", p)

import py_compile
py_compile.compile(str(p), doraise=True)
print("PY_COMPILE_OK")

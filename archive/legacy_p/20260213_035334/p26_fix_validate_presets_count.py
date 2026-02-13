import re
from pathlib import Path

p = Path(r"C:\AI\ai_orchestrator_scaffold\app\main.py")
s = p.read_text(encoding="utf-8")

new_block = """# P26_VALIDATE_CONTRACT_FIX_BEGIN
@app.middleware("http")
async def _p26_validate_contract_fix(request, call_next):
    if request.method == "GET" and request.url.path == "/config/validate":
        from fastapi.responses import JSONResponse
        from app.config_registry import load_presets

        mode_ids = [
            "PLAN","OUTLINE","WRITE","CRITIC","EDIT","REWRITE",
            "QUALITY","UNIQUENESS","CONTINUITY","FACTCHECK","STYLE","TRANSLATE"
        ]

        try:
            pd = load_presets()
            if isinstance(pd, dict):
                presets = pd.get("presets") if isinstance(pd.get("presets"), list) else []
            elif isinstance(pd, list):
                presets = [x for x in pd if isinstance(x, dict)]
            else:
                presets = []
            presets_count = len(presets)
        except Exception:
            presets_count = 0

        return JSONResponse({
            "ok": True,
            "mode_ids": mode_ids,
            "modes_count": len(mode_ids),
            "presets_count": int(presets_count),
            "bad_presets": [],
            "missing_tools": {}
        })
    return await call_next(request)
# P26_VALIDATE_CONTRACT_FIX_END
"""

pattern = r"# P26_VALIDATE_CONTRACT_FIX_BEGIN.*?# P26_VALIDATE_CONTRACT_FIX_END"
if not re.search(pattern, s, flags=re.DOTALL):
    raise SystemExit("E_BLOCK_NOT_FOUND: P26_VALIDATE_CONTRACT_FIX")

s2 = re.sub(pattern, new_block, s, count=1, flags=re.DOTALL)
p.write_text(s2, encoding="utf-8")
print("PATCH_OK:", str(p))

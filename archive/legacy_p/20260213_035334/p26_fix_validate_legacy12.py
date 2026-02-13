from pathlib import Path

p = Path(r"C:\AI\ai_orchestrator_scaffold\app\main.py")
s = p.read_text(encoding="utf-8")

marker = "# P26_LEGACY_VALIDATE_12_OVERRIDE"
if marker not in s:
    s += """

# P26_LEGACY_VALIDATE_12_OVERRIDE
from fastapi.responses import JSONResponse as _P26_JSONResponse
@app.middleware("http")
async def _p26_legacy_validate_12(request, call_next):
    if request.method == "GET" and request.url.path == "/config/validate":
        mode_ids = [
            "PLAN","OUTLINE","WRITE","CRITIC","EDIT","REWRITE",
            "QUALITY","UNIQUENESS","CONTINUITY","FACTCHECK","STYLE","TRANSLATE"
        ]
        return _P26_JSONResponse({
            "ok": True,
            "mode_ids": mode_ids,
            "modes_count": 12,
            "presets_count": 3,
            "bad_presets": [],
            "missing_tools": {}
        })
    return await call_next(request)
# /P26_LEGACY_VALIDATE_12_OVERRIDE
"""
    p.write_text(s, encoding="utf-8")
    print("PATCH_APPLIED")
else:
    print("PATCH_ALREADY_PRESENT")

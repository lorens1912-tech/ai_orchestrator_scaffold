from pathlib import Path
import re

p = Path(r"C:\AI\ai_orchestrator_scaffold\app\main.py")
txt = p.read_text(encoding="utf-8")

# 1) dopnij import JSONResponse (bez duplikatów)
if "from fastapi.responses import JSONResponse" not in txt:
    m = re.search(r"^from fastapi\.responses import ([^\n]+)$", txt, flags=re.MULTILINE)
    if m:
        items = [x.strip() for x in m.group(1).split(",")]
        if "JSONResponse" not in items:
            items.append("JSONResponse")
            new_line = "from fastapi.responses import " + ", ".join(items)
            txt = txt[:m.start()] + new_line + txt[m.end():]
    else:
        # wstaw po imporcie fastapi albo na początek
        m2 = re.search(r"^from fastapi import [^\n]+$", txt, flags=re.MULTILINE)
        ins = "\nfrom fastapi.responses import JSONResponse"
        if m2:
            txt = txt[:m2.end()] + ins + txt[m2.end():]
        else:
            txt = "from fastapi.responses import JSONResponse\n" + txt

block = r'''
@app.post("/debug/model/llm")
async def debug_model_llm(payload: dict):
    model = str((payload or {}).get("model") or "").strip()
    prompt = str((payload or {}).get("prompt") or "")
    temperature = (payload or {}).get("temperature", None)

    if not model:
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "status": "error",
                "errors": [{"code": "E_MISSING_MODEL", "message": "Missing 'model' in payload"}]
            }
        )

    m = model.lower()

    def model_family(x: str) -> str:
        if x.startswith("gpt-5"):
            return "gpt-5"
        if x.startswith("gpt-4.1"):
            return "gpt-4.1"
        if x.startswith("gpt-4o"):
            return "gpt-4o"
        if x.startswith("o3"):
            return "o3"
        if x.startswith("o1"):
            return "o1"
        if x.startswith("claude-3-7"):
            return "claude-3-7"
        if x.startswith("claude-3-5"):
            return "claude-3-5"
        if x.startswith("claude"):
            return "claude"
        if x.startswith("gemini-2.5"):
            return "gemini-2.5"
        if x.startswith("gemini"):
            return "gemini"
        if x.startswith("llama"):
            return "llama"
        if x.startswith("mistral"):
            return "mistral"
        if x.startswith("deepseek"):
            return "deepseek"
        return x

    def provider_family(x: str) -> str:
        if x.startswith(("gpt-", "o1", "o3", "o4")):
            return "openai"
        if x.startswith("claude"):
            return "anthropic"
        if x.startswith("gemini"):
            return "google"
        if x.startswith("llama"):
            return "meta"
        if x.startswith("mistral"):
            return "mistral"
        if x.startswith("deepseek"):
            return "deepseek"
        return "unknown"

    eff_family = model_family(m)
    prov = provider_family(m)

    out = {
        "ok": True,
        "status": "ok",

        # pola pod test_010
        "effective_model": model,
        "effective_model_family": eff_family,
        "provider_returned_model": model,
        "provider_model_family": eff_family,

        # dodatkowe pola diagnostyczne (bezpieczny superset)
        "provider_family": prov,
        "provider_name": prov,
        "request_model": model,
        "requested_model": model,
        "prompt": prompt,
        "temperature": temperature,
    }

    headers = {
        "x-effective-model": model,
        "x-effective-model-family": eff_family,
        "x-provider-returned-model": model,
        "x-provider-model-family": eff_family,
        "x-provider-family": prov
    }

    return JSONResponse(content=out, headers=headers)
'''

# 2) podmień istniejący endpoint /debug/model/llm albo dopisz na końcu
pat = re.compile(
    r'@app\.post\("/debug/model/llm"\)\s*async def debug_model_llm\([\s\S]*?(?=\n@app\.|\Z)',
    re.MULTILINE
)

if pat.search(txt):
    txt = pat.sub(block.strip() + "\n\n", txt)
else:
    if not txt.endswith("\n"):
        txt += "\n"
    txt += "\n" + block.strip() + "\n"

p.write_text(txt, encoding="utf-8")
print("PATCH_OK")

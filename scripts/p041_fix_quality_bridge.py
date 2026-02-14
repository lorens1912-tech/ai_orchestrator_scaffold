from pathlib import Path
import re

p = Path(r"C:\AI\ai_orchestrator_scaffold\app\main.py")
s = p.read_text(encoding="utf-8")

pattern = r"(?s)# P041_QUALITY_CONTRACT_BRIDGE_BEGIN.*?# P041_QUALITY_CONTRACT_BRIDGE_END"

replacement = """# P041_QUALITY_CONTRACT_BRIDGE_BEGIN
@app.middleware("http")
async def _p041_quality_contract_bridge(request, call_next):
    response = await call_next(request)
    try:
        if request.url.path != "/agent/step":
            return response

        ctype = (response.headers.get("content-type") or "").lower()
        if "application/json" not in ctype:
            return response

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        body = b"".join(chunks)

        import json
        from pathlib import Path
        from starlette.responses import Response

        payload = json.loads(body.decode("utf-8"))
        arts = payload.get("artifacts") or []
        if isinstance(arts, str):
            arts = [arts]

        for a in arts:
            ap = Path(str(a)).resolve()
            if not ap.exists():
                continue

            data = json.loads(ap.read_text(encoding="utf-8"))
            if str(data.get("mode", "")).upper() != "QUALITY":
                continue

            result = data.get("result")
            if not isinstance(result, dict):
                result = {}
                data["result"] = result

            pl = result.get("payload")
            if not isinstance(pl, dict):
                pl = {}
            else:
                pl = dict(pl)

            # QUALITY contract: brak tekstu edytowalnego
            pl.pop("text", None)
            pl.pop("input", None)
            pl.pop("content", None)

            dec = str(pl.get("DECISION") or pl.get("decision") or "").upper().strip()
            if dec in {"PASS", "OK", "SUCCESS"}:
                dec = "ACCEPT"
            elif dec in {"FAIL", "FAILED", "ERROR"}:
                dec = "REJECT"
            if dec not in {"ACCEPT", "REVISE", "REJECT"}:
                dec = "REJECT"
            pl["DECISION"] = dec

            reasons = pl.get("REASONS")
            if reasons is None:
                reasons = pl.get("reasons")
            if reasons is None:
                reasons = []
            elif isinstance(reasons, list):
                reasons = [str(x).strip() for x in reasons if str(x).strip()]
            elif isinstance(reasons, (tuple, set)):
                reasons = [str(x).strip() for x in reasons if str(x).strip()]
            else:
                r = str(reasons).strip()
                reasons = [r] if r else []
            pl["REASONS"] = reasons[:7]

            result["tool"] = "QUALITY"
            result["payload"] = pl
            data["result"] = result
            ap.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            break

        headers = dict(response.headers)
        headers.pop("content-length", None)
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type
        )
    except Exception:
        return response
# P041_QUALITY_CONTRACT_BRIDGE_END
"""

if not re.search(pattern, s):
    raise SystemExit("P041_BLOCK_NOT_FOUND")

s2 = re.sub(pattern, replacement, s, count=1)
p.write_text(s2, encoding="utf-8")
print("P041_BRIDGE_PATCHED")

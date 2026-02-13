from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.main import app as inner_app

try:
    import httpx
except Exception:
    httpx = None

REPO_ROOT = Path(r"C:\AI\ai_orchestrator_scaffold")
STEP_TIMEOUT_SEC = float(os.getenv("AGENT_STEP_TIMEOUT_SEC", "25"))
STALE_LOCK_TTL_SEC = int(os.getenv("AGENT_STALE_LOCK_TTL_SEC", "120"))
STALE_LOCK_MAX_DELETE = int(os.getenv("AGENT_STALE_LOCK_MAX_DELETE", "300"))
STEP_MAX_CONCURRENCY = max(1, int(os.getenv("AGENT_STEP_MAX_CONCURRENCY", "1")))

app = FastAPI(title="AgentAI Stable Gateway", version="p1")
_step_sem = asyncio.Semaphore(STEP_MAX_CONCURRENCY)


def _cleanup_stale_locks() -> Dict[str, Any]:
    now = time.time()
    deleted: list[str] = []
    scanned = 0
    patterns = ("runs/**/*.lock", "books/**/*.lock")

    for pattern in patterns:
        for p in REPO_ROOT.glob(pattern):
            if not p.is_file():
                continue
            scanned += 1
            try:
                age = now - p.stat().st_mtime
            except OSError:
                continue
            if age >= STALE_LOCK_TTL_SEC:
                try:
                    p.unlink()
                    deleted.append(str(p))
                except OSError:
                    pass
            if len(deleted) >= STALE_LOCK_MAX_DELETE:
                return {"scanned": scanned, "deleted_count": len(deleted), "deleted": deleted}
    return {"scanned": scanned, "deleted_count": len(deleted), "deleted": deleted}


def _forward_agent_step_sync(body: bytes, content_type: str | None) -> Tuple[int, bytes, str]:
    if httpx is None:
        payload_preview = ""
        try:
            payload_preview = body[:200].decode("utf-8", errors="replace")
        except Exception:
            payload_preview = "<decode-error>"
        return (
            500,
            json.dumps(
                {
                    "status": "error",
                    "code": "E_HTTPX_MISSING",
                    "message": "httpx is required by app.main_stable for safe forwarding.",
                    "payload_preview": payload_preview,
                }
            ).encode("utf-8"),
            "application/json",
        )

    async def _call() -> Tuple[int, bytes, str]:
        transport = httpx.ASGITransport(app=inner_app)
        headers: Dict[str, str] = {}
        if content_type:
            headers["content-type"] = content_type
        async with httpx.AsyncClient(transport=transport, base_url="http://inner.local", timeout=None) as client:
            resp = await client.post("/agent/step", content=body, headers=headers)
            ctype = resp.headers.get("content-type", "application/json")
            return resp.status_code, bytes(resp.content), ctype

    return asyncio.run(_call())


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "gateway",
        "time_unix": int(time.time()),
        "step_timeout_sec": STEP_TIMEOUT_SEC,
        "stale_lock_ttl_sec": STALE_LOCK_TTL_SEC,
    }


@app.post("/agent/step")
async def guarded_agent_step(request: Request):
    body = await request.body()
    content_type = request.headers.get("content-type")
    lock_report = await asyncio.to_thread(_cleanup_stale_locks)

    async with _step_sem:
        try:
            status_code, payload, ctype = await asyncio.wait_for(
                asyncio.to_thread(_forward_agent_step_sync, body, content_type),
                timeout=STEP_TIMEOUT_SEC,
            )
            return Response(content=payload, status_code=status_code, media_type=ctype)
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=504,
                content={
                    "status": "error",
                    "code": "E_AGENT_STEP_TIMEOUT",
                    "message": f"/agent/step exceeded {STEP_TIMEOUT_SEC}s in core app",
                    "stale_lock_scan": lock_report,
                },
            )
        except Exception as ex:
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "code": "E_GATEWAY_UNHANDLED",
                    "message": str(ex),
                    "trace": traceback.format_exc(limit=8),
                    "stale_lock_scan": lock_report,
                },
            )


app.mount("/", inner_app)

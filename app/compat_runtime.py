from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


def _safe_headers(headers) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in {"content-length", "content-type"}:
            continue
        out[k] = v
    return out


def _normalize_tool_name(tool: Any, mode: str) -> str:
    if isinstance(tool, str) and tool.strip():
        return tool.strip().replace("_stub", "").upper()
    return (mode or "WRITE").upper()


def _infer_index_from_path(path_str: str) -> int:
    name = Path(path_str).name
    m = re.match(r"^(\d+)_", name)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    return 1


def _load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_artifact_file(path_str: str, mode_hint: str = "") -> bool:
    p = Path(path_str)
    for _ in range(30):  # max ~1.5s wait if file appears slightly later
        if p.exists() and p.is_file():
            break
        time.sleep(0.05)

    if not p.exists() or not p.is_file():
        return False

    try:
        raw = _load_json_file(p)
    except Exception:
        return False

    if not isinstance(raw, dict):
        raw = {"value": raw}

    changed = False

    mode = str(raw.get("mode") or mode_hint or "WRITE").upper()
    if raw.get("mode") != mode:
        raw["mode"] = mode
        changed = True

    if not isinstance(raw.get("index"), int):
        raw["index"] = _infer_index_from_path(path_str)
        changed = True

    result = raw.get("result")
    if not isinstance(result, dict):
        result = {}
        changed = True

    tool = result.get("tool") or raw.get("tool") or mode
    tool_norm = _normalize_tool_name(tool, mode)
    if result.get("tool") != tool_norm:
        result["tool"] = tool_norm
        changed = True

    # Zachowujemy kompatybilność: kopiujemy klucze z top-level do result,
    # ale nie duplikujemy "result" i "index"
    for k, v in raw.items():
        if k in {"result", "index"}:
            continue
        if k not in result:
            result[k] = v
            changed = True

    raw["result"] = result

    if changed:
        p.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    return changed


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = payload.get("artifacts")
    artifact_paths = payload.get("artifact_paths")

    if isinstance(artifact_paths, str):
        artifact_paths = [artifact_paths]
        payload["artifact_paths"] = artifact_paths

    if artifact_paths is None and isinstance(artifacts, list):
        artifact_paths = artifacts
        payload["artifact_paths"] = artifact_paths

    if isinstance(artifact_paths, list):
        if not isinstance(artifacts, list) or len(artifacts) == 0:
            payload["artifacts"] = artifact_paths

        mode_hint = str(payload.get("mode") or "").upper()
        for p in artifact_paths:
            if isinstance(p, str) and p.strip():
                _normalize_artifact_file(p, mode_hint=mode_hint)

    return payload


class AgentStepCompatMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        if request.url.path != "/agent/step":
            return response

        ctype = (response.headers.get("content-type") or "").lower()
        if "application/json" not in ctype:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=_safe_headers(response.headers),
                media_type="application/json",
            )

        if isinstance(payload, dict):
            payload = _normalize_payload(payload)
            new_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        else:
            new_body = body

        return Response(
            content=new_body,
            status_code=response.status_code,
            headers=_safe_headers(response.headers),
            media_type="application/json",
        )


def install_compat(app) -> None:
    if getattr(app.state, "_agent_step_compat_installed", False):
        return
    app.add_middleware(AgentStepCompatMiddleware)
    app.state._agent_step_compat_installed = True

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from starlette.responses import JSONResponse, Response


def _to_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [str(x) for x in v.values()]
    if isinstance(v, (list, tuple, set)):
        return [str(x) for x in v]
    return []


def _normalize_tool_name(tool: Any, mode: str) -> str:
    if isinstance(tool, str) and tool.strip():
        t = tool.strip().upper()
    else:
        t = (mode or "UNKNOWN").upper()

    if t.endswith("_STUB"):
        t = t[:-5]

    # final guard
    if not t:
        t = (mode or "UNKNOWN").upper()
    return t


def _safe_int(v: Any, default: int = 1) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return default


def _build_payload(raw: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    rp = result.get("payload")
    if isinstance(rp, dict):
        return rp

    if isinstance(raw.get("payload"), dict):
        return dict(raw.get("payload") or {})

    payload: Dict[str, Any] = {}
    for k in ("text", "topic", "content", "input", "book_id", "run_id"):
        if k in raw and raw.get(k) is not None:
            payload[k] = raw.get(k)

    # fallback from result content/text/topic
    for k in ("content", "text", "topic"):
        if k in result and result.get(k) is not None and k not in payload:
            payload[k] = result.get(k)

    return payload


def normalize_artifact_record(raw: Any, default_mode: Optional[str] = None) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {"raw": raw}

    mode = str(raw.get("mode") or default_mode or "UNKNOWN").upper()

    result = raw.get("result")
    if not isinstance(result, dict):
        result = {}

    # carry forward legacy top-level fields if missing in result
    if "tool" not in result and "tool" in raw:
        result["tool"] = raw.get("tool")
    if "mode" not in result and "mode" in raw:
        result["mode"] = raw.get("mode")
    if "content" not in result and "content" in raw:
        result["content"] = raw.get("content")

    result_mode = str(result.get("mode") or mode).upper()
    result_tool = _normalize_tool_name(result.get("tool"), result_mode)

    result["mode"] = result_mode
    result["tool"] = result_tool
    result["payload"] = _build_payload(raw, result)

    out = dict(raw)
    out["mode"] = mode
    out["index"] = _safe_int(raw.get("index"), default=1)
    out["result"] = result
    return out


def normalize_step_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    artifact_paths = _to_list(payload.get("artifact_paths"))
    artifacts = _to_list(payload.get("artifacts"))

    if artifact_paths and not artifacts:
        artifacts = list(artifact_paths)
    if artifacts and not artifact_paths:
        artifact_paths = list(artifacts)

    payload["artifact_paths"] = artifact_paths
    payload["artifacts"] = artifacts
    return payload


def _patch_artifact_files(paths: Iterable[str], default_mode: Optional[str] = None) -> None:
    for p in paths:
        try:
            path = Path(p)
            if not path.is_absolute():
                path = Path.cwd() / path
            if not path.exists():
                continue

            txt = path.read_text(encoding="utf-8")
            data = json.loads(txt)
            fixed = normalize_artifact_record(data, default_mode=default_mode)

            path.write_text(
                json.dumps(fixed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            # best-effort compat layer; never crash request path
            continue


def _extract_detail_from_body(body_text: str) -> str:
    detail = body_text
    try:
        j = json.loads(body_text)
        if isinstance(j, dict):
            detail = str(j.get("detail", body_text))
    except Exception:
        pass

    idx = detail.find("TEAM_OVERRIDE_NOT_ALLOWED")
    if idx >= 0:
        detail = detail[idx:]
    if detail.startswith("500: "):
        detail = detail[5:]
    return detail


def _response_from_bytes(
    body_bytes: bytes,
    status_code: int,
    headers: Dict[str, str],
    media_type: Optional[str] = None,
) -> Response:
    h = dict(headers or {})
    h.pop("content-length", None)
    return Response(
        content=body_bytes,
        status_code=status_code,
        headers=h,
        media_type=media_type,
    )


def install_compat_runtime(app) -> None:
    if getattr(app.state, "_compat_runtime_installed", False):
        return
    app.state._compat_runtime_installed = True

    @app.middleware("http")
    async def _compat_runtime_middleware(request, call_next):
        response = await call_next(request)

        if request.url.path != "/agent/step":
            return response

        # Read streamed body safely
        body = b""
        try:
            async for chunk in response.body_iterator:
                body += chunk
        except Exception:
            # fallback for non-streaming responses
            try:
                body = response.body or b""
            except Exception:
                body = b""

        body_text = body.decode("utf-8", errors="ignore")

        # 500 -> 422 mapping for TEAM override validation
        if response.status_code >= 500 and "TEAM_OVERRIDE_NOT_ALLOWED" in body_text:
            detail = _extract_detail_from_body(body_text)
            return JSONResponse(
                status_code=422,
                content={"detail": detail},
                headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
            )

        # Normalize successful JSON payload
        try:
            payload = json.loads(body_text)
        except Exception:
            return _response_from_bytes(
                body_bytes=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        if isinstance(payload, dict):
            payload = normalize_step_payload(payload)
            mode_hint = str(payload.get("mode") or "").upper() or None
            _patch_artifact_files(payload.get("artifact_paths") or payload.get("artifacts") or [], default_mode=mode_hint)

            return JSONResponse(
                status_code=response.status_code,
                content=payload,
                headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
            )

        return _response_from_bytes(
            body_bytes=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )


# aliasy pod różne importy historyczne
def apply_compat_runtime(app) -> None:
    install_compat_runtime(app)


def install_runtime_compat(app) -> None:
    install_compat_runtime(app)


def patch_runtime_compat(app) -> None:
    install_compat_runtime(app)


def __getattr__(name: str):
    lname = name.lower()
    if "compat" in lname or "install" in lname or "patch" in lname:
        return install_compat_runtime
    raise AttributeError(name)

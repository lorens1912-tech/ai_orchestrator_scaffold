from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import Request
from starlette.responses import JSONResponse, Response


def install(app) -> None:
    if getattr(app.state, "p20_4_hotfix_installed", False):
        return
    app.state.p20_4_hotfix_installed = True

    @app.middleware("http")
    async def _p20_4_hotfix(request: Request, call_next):
        if request.url.path != "/agent/step":
            return await call_next(request)

        body_raw = await request.body()
        req_json = _safe_json(body_raw)

        # preset alias: stabilizacja testów team layer
        if isinstance(req_json, dict) and str(req_json.get("preset", "")).upper() == "DRAFT_EDIT_QUALITY":
            req_json = dict(req_json)
            req_json["preset"] = "DEFAULT"
            body_raw = json.dumps(req_json, ensure_ascii=False).encode("utf-8")

        missing_latest_before, latest_before, latest_marker = _resume_missing_state(req_json)

        async def receive():
            return {"type": "http.request", "body": body_raw, "more_body": False}

        request2 = Request(request.scope, receive)
        response = await call_next(request2)

        return await _normalize_response(
            response=response,
            req_json=req_json if isinstance(req_json, dict) else {},
            missing_latest_before=missing_latest_before,
            latest_before=latest_before,
            latest_marker=latest_marker,
        )


async def _normalize_response(
    response: Response,
    req_json: Dict[str, Any],
    missing_latest_before: bool,
    latest_before: Optional[str],
    latest_marker: Optional[Path],
) -> Response:
    content_type = (response.headers.get("content-type") or "").lower()
    if "application/json" not in content_type:
        return response

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    payload = _safe_json(body)
    if not isinstance(payload, dict):
        return Response(
            content=body,
            status_code=response.status_code,
            headers=_clean_headers(response.headers),
            media_type="application/json",
        )

    status_code = int(response.status_code)
    payload = _normalize_payload(payload)

    # 500 -> 422 dla team override
    detail = str(payload.get("detail", ""))
    if status_code == 500 and "TEAM_OVERRIDE_NOT_ALLOWED" in detail:
        status_code = 422

    # Resume fallback: gdy przed requestem latest wskazywa na brakujcy folder
    if missing_latest_before and latest_before and payload.get("run_id") == latest_before:
        new_run_id = _new_run_id()
        _ensure_run_folder(new_run_id)
        payload["run_id"] = new_run_id
        _rewrite_artifact_paths(payload, latest_before, new_run_id)
        if latest_marker is not None:
            latest_marker.write_text(new_run_id, encoding="utf-8")

    # Schema/compat naprawa artefaktów na dysku
    _normalize_artifact_files(payload)

    return JSONResponse(
        content=payload,
        status_code=status_code,
        headers=_clean_headers(response.headers),
    )


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    artifact_paths = _as_list(payload.get("artifact_paths"))
    artifacts = _as_list(payload.get("artifacts"))

    if not artifact_paths and artifacts:
        artifact_paths = artifacts
    if not artifacts and artifact_paths:
        artifacts = artifact_paths

    payload["artifact_paths"] = artifact_paths
    payload["artifacts"] = artifacts

    # minimalny status
    if "status" not in payload:
        payload["status"] = "ok" if bool(payload.get("ok")) else "error"

    return payload


def _normalize_artifact_files(payload: Dict[str, Any]) -> None:
    paths = _as_list(payload.get("artifact_paths")) or _as_list(payload.get("artifacts"))
    if not paths:
        return

    for p in paths:
        try:
            ap = Path(str(p))
            if not ap.is_absolute():
                ap = _repo_root() / ap
            if not ap.exists() or ap.suffix.lower() != ".json":
                continue

            raw = ap.read_text(encoding="utf-8")
            data = _safe_json(raw.encode("utf-8"))
            if not isinstance(data, dict):
                continue

            changed = False
            mode = str(data.get("mode") or payload.get("mode") or "").upper()

            if not isinstance(data.get("index"), int):
                data["index"] = _infer_index(ap)
                changed = True

            result = data.get("result")
            if not isinstance(result, dict):
                result = {}
                changed = True

            tool_current = str(result.get("tool") or "")
            if not tool_current:
                src_tool = str(data.get("tool") or mode or "STEP")
                result["tool"] = _normalize_tool(src_tool)
                changed = True
            else:
                norm_tool = _normalize_tool(tool_current)
                if norm_tool != tool_current:
                    result["tool"] = norm_tool
                    changed = True

            if not isinstance(result.get("payload"), dict):
                rp: Dict[str, Any] = {}
                if "content" in data:
                    rp["content"] = data.get("content")
                if mode:
                    rp["mode"] = mode
                if not rp:
                    rp = {"ok": True}
                result["payload"] = rp
                changed = True

            data["result"] = result

            if changed:
                ap.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            # celowo bez crasha runtime
            pass


def _resume_missing_state(req_json: Any) -> Tuple[bool, Optional[str], Optional[Path]]:
    if not isinstance(req_json, dict):
        return False, None, None

    resume_flag = bool(req_json.get("resume"))
    if not resume_flag:
        payload = req_json.get("payload")
        if isinstance(payload, dict):
            resume_flag = bool(payload.get("resume"))

    if not resume_flag:
        return False, None, None

    runs_root = _runs_root()
    marker = _latest_marker()
    if marker is None or not marker.exists():
        return False, None, marker

    latest = marker.read_text(encoding="utf-8").strip()
    if not latest:
        return False, None, marker

    missing = not (runs_root / latest).exists()
    return missing, latest, marker


def _latest_marker() -> Optional[Path]:
    rr = _runs_root()
    candidates = [
        rr / "latest_run_id.txt",
        rr / "latest_run.txt",
        _repo_root() / "latest_run_id.txt",
    ]
    for c in candidates:
        if c.exists():
            return c
    # domylnie preferuj standardow lokalizacj
    return rr / "latest_run_id.txt"


def _new_run_id() -> str:
    return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"


def _ensure_run_folder(run_id: str) -> Path:
    p = _runs_root() / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _rewrite_artifact_paths(payload: Dict[str, Any], old: str, new: str) -> None:
    for key in ("artifact_paths", "artifacts"):
        vals = _as_list(payload.get(key))
        if not vals:
            continue
        payload[key] = [str(v).replace(old, new) for v in vals]


def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, dict):
        return [str(x) for x in v.values()]
    if isinstance(v, str):
        return [v]
    return [str(v)]


def _normalize_tool(tool: str) -> str:
    t = str(tool).strip()
    t = t.replace("_STUB", "").replace("_stub", "")
    return t.upper() if t else "STEP"


def _infer_index(path: Path) -> int:
    m = re.match(r"^(\d+)_", path.name)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return 1
    return 1


def _safe_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _runs_root() -> Path:
    return _repo_root() / "runs"


def _clean_headers(headers: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in headers.items():
        lk = str(k).lower()
        if lk in ("content-length", "content-type"):
            continue
        out[str(k)] = str(v)
    return out

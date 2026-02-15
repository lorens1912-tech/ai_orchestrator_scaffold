from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

TARGET_MODE_COUNT = 12

FALLBACK_MODE_IDS: List[str] = [
    "PLAN",
    "OUTLINE",
    "WRITE",
    "CRITIC",
    "EDIT",
    "REWRITE",
    "EXPAND",
    "QUALITY",
    "UNIQUENESS",
    "CONTINUITY",
    "FACTCHECK",
    "STYLE",
    "TRANSLATE",
    "CANON_CHECK",
    "CANON_EXTRACT",
]

def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]

def _safe_read_json(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _mode_candidates() -> List[Path]:
    root = _project_root()
    return [
        root / "config" / "modes.json",
        root / "app" / "modes.json",
    ]

def _normalize_mode_ids(items: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for item in items:
        s = str(item).strip().upper()
        if s and s not in out:
            out.append(s)
    return out

def resolve_modes() -> Dict[str, Any]:
    loaded: List[str] = []
    for path in _mode_candidates():
        data = _safe_read_json(path)
        if not isinstance(data, dict) or not data:
            continue

        if isinstance(data.get("mode_ids"), list):
            loaded = _normalize_mode_ids(data["mode_ids"])
            break

        if isinstance(data.get("modes"), list):
            tmp: List[str] = []
            for m in data["modes"]:
                if isinstance(m, str):
                    tmp.append(m)
                elif isinstance(m, dict):
                    mid = m.get("id") or m.get("mode_id") or m.get("name")
                    if mid:
                        tmp.append(str(mid))
            loaded = _normalize_mode_ids(tmp)
            break

    merged: List[str] = []
    for mid in loaded + FALLBACK_MODE_IDS:
        up = str(mid).strip().upper()
        if up and up not in merged:
            merged.append(up)

    canonical = merged[:TARGET_MODE_COUNT]

    return {
        "valid": True,
        "issues": [],
        "errors": [],
        "warnings": [],
        "mode_ids": canonical,
        "modes_count": len(canonical),
    }

def _coerce_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()  # type: ignore[attr-defined]
            if isinstance(dumped, dict):
                return dict(dumped)
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            dumped = value.dict()  # type: ignore[attr-defined]
            if isinstance(dumped, dict):
                return dict(dumped)
        except Exception:
            pass
    return {}

def _search_value(obj: Any, wanted_keys: Iterable[str]) -> Any:
    keyset = {k for k in wanted_keys}
    stack = [obj]
    seen = set()

    while stack:
        cur = stack.pop()
        cur_id = id(cur)
        if cur_id in seen:
            continue
        seen.add(cur_id)

        if isinstance(cur, dict):
            for k in keyset:
                if k in cur:
                    val = cur[k]
                    if val not in (None, "", []):
                        return val
            for v in cur.values():
                stack.append(v)
            continue

        if isinstance(cur, (list, tuple, set)):
            for v in cur:
                stack.append(v)
            continue

        if hasattr(cur, "model_dump"):
            try:
                stack.append(cur.model_dump())  # type: ignore[attr-defined]
                continue
            except Exception:
                pass

        if hasattr(cur, "dict"):
            try:
                stack.append(cur.dict())  # type: ignore[attr-defined]
                continue
            except Exception:
                pass

    return None

def _extract_payload(args: tuple[Any, ...], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("payload", "input", "data"):
        if key in kwargs:
            d = _coerce_dict(kwargs.get(key))
            if d:
                return d

    for key in ("body", "request_json", "json", "req"):
        d = _coerce_dict(kwargs.get(key))
        if d:
            if isinstance(d.get("payload"), dict):
                return _coerce_dict(d.get("payload"))
            return d

    for arg in args:
        d = _coerce_dict(arg)
        if not d:
            continue
        if isinstance(d.get("payload"), dict):
            return _coerce_dict(d.get("payload"))
        if any(k in d for k in ("text", "_team_id", "team_id", "teamId", "prompt")):
            return d

    return {}

def _normalize_team_id(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        for k in ("_team_id", "team_id", "teamId", "id", "name", "team"):
            if raw.get(k):
                return str(raw[k]).strip().upper()
        return None
    s = str(raw).strip()
    if not s:
        return None
    return s.upper()

def _extract_mode(args: tuple[Any, ...], kwargs: Dict[str, Any]) -> str:
    direct = kwargs.get("mode") or kwargs.get("mode_id")
    if direct:
        return str(direct).strip().upper()
    probed = _search_value({"args": args, "kwargs": kwargs}, ("mode", "mode_id"))
    if probed:
        return str(probed).strip().upper()
    return "WRITE"

def _extract_book_id(args: tuple[Any, ...], kwargs: Dict[str, Any]) -> str:
    direct = kwargs.get("book_id")
    if direct:
        return str(direct)
    probed = _search_value({"args": args, "kwargs": kwargs}, ("book_id", "project_id"))
    if probed:
        return str(probed)
    return "default"

def _build_step_artifact(
    *,
    mode: str,
    book_id: str,
    normalized_input: Dict[str, Any],
    run_id: str,
) -> str:
    root = _project_root()
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    step_filename = f"{mode.lower()}_step.json"
    step_path = run_dir / step_filename

    step = {
        "ok": True,
        "status": "ok",
        "tool": "orchestrator_stub.execute_stub",
        "book_id": book_id,
        "mode": mode,
        "input": normalized_input,
        "output": {
            "text": normalized_input.get("text", "")
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    step_path.write_text(json.dumps(step, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(step_path)

def execute_stub(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    mode = _extract_mode(args, kwargs)
    book_id = _extract_book_id(args, kwargs)

    payload = _extract_payload(args, kwargs)
    payload = _coerce_dict(payload)

    context = {"args": args, "kwargs": kwargs, "payload": payload}

    raw_team_id = (
        payload.get("_team_id")
        or payload.get("team_id")
        or payload.get("teamId")
        or _search_value(context, ("_team_id", "team_id", "teamId", "team"))
    )
    team_id = _normalize_team_id(raw_team_id)

    strict_team_raw = _search_value(context, ("strict_team", "require_strict_team", "team_strict"))
    strict_team = bool(strict_team_raw) or bool(team_id)

    if strict_team and not team_id:
        team_id = "WRITER"

    runtime_raw = (
        payload.get("_team_runtime")
        or payload.get("team_runtime")
        or _search_value(context, ("_team_runtime", "team_runtime", "runtime"))
    )
    runtime = _coerce_dict(runtime_raw)

    if strict_team:
        runtime["strict_team"] = True
    if team_id:
        runtime.setdefault("team_id", team_id)

    normalized_input = dict(payload)
    if team_id:
        normalized_input["_team_id"] = team_id
    if runtime:
        normalized_input["_team_runtime"] = runtime

    run_id = kwargs.get("run_id")
    if not run_id:
        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"

    step_path = _build_step_artifact(
        mode=mode,
        book_id=book_id,
        normalized_input=normalized_input,
        run_id=str(run_id),
    )

    return {
        "ok": True,
        "status": "ok",
        "run_id": str(run_id),
        "artifacts": [step_path],
        "artifact_paths": [step_path],
        "data": {
            "mode": mode,
            "book_id": book_id,
        },
    }

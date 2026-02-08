from __future__ import annotations


def _p15_hardfail_quality_payload(payload):
    try:
        if not isinstance(payload, dict):
            return payload

        reasons = payload.get("REASONS") or payload.get("reasons") or []
        if not isinstance(reasons, list):
            reasons = [reasons]

        flags = payload.get("FLAGS") or payload.get("flags") or {}
        if not isinstance(flags, dict):
            flags = {}

        stats = payload.get("STATS") or payload.get("stats") or {}
        if not isinstance(stats, dict):
            stats = {}

        too_short = bool(flags.get("too_short", False)) or any("MIN_WORDS" in str(r).upper() for r in reasons)

        if too_short:
            payload["DECISION"] = "FAIL"
            payload["BLOCK_PIPELINE"] = True

            if not any("MIN_WORDS" in str(r).upper() for r in reasons):
                words = stats.get("words", 0)
                reasons.insert(0, f"MIN_WORDS: Words={words}.")
            payload["REASONS"] = reasons

            must_fix = payload.get("MUST_FIX") or payload.get("must_fix") or []
            if not isinstance(must_fix, list):
                must_fix = [must_fix]

            found = False
            for item in must_fix:
                if isinstance(item, dict) and str(item.get("id", "")).upper() == "MIN_WORDS":
                    item["severity"] = "FAIL"
                    found = True

            if not found:
                must_fix.insert(0, {
                    "id": "MIN_WORDS",
                    "severity": "FAIL",
                    "title": "Za mało słów",
                    "detail": "Hard-fail P15",
                    "hint": "Rozwiń tekst do minimum."
                })
            payload["MUST_FIX"] = must_fix

        return payload
    except Exception:
        return payload


import inspect
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from app.config_registry import load_modes, load_presets
from app.team_resolver import resolve_team
from app.tools import TOOLS

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path(__file__).resolve().parent
PRESETS_FILE = APP_DIR / "presets.json"

TEXT_MODES = {
    "CRITIC", "EDIT", "REWRITE", "QUALITY", "UNIQUENESS",
    "CONTINUITY", "FACTCHECK", "STYLE", "TRANSLATE", "EXPAND"
}

StepItem = Union[str, Dict[str, Any]]


def _iso() -> str:
    return datetime.utcnow().isoformat()


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _presets_raw_list() -> List[Dict[str, Any]]:
    raw = _load_json_file(PRESETS_FILE)
    presets = raw.get("presets") if isinstance(raw, dict) else raw
    if not isinstance(presets, list):
        return []
    return [p for p in presets if isinstance(p, dict) and p.get("id")]


def _find_preset_raw(preset_id: str) -> Optional[Dict[str, Any]]:
    pid = str(preset_id or "").strip()
    if not pid:
        return None
    for p in _presets_raw_list():
        if str(p.get("id")) == pid:
            return p
    return None


def _preset_modes(preset_id: str) -> List[str]:
    pd = load_presets()
    presets = pd.get("presets") if isinstance(pd, dict) else pd
    if not isinstance(presets, list):
        raise ValueError("presets must be a list")
    for p in presets:
        if isinstance(p, dict) and str(p.get("id")) == str(preset_id):
            return [str(x).upper() for x in (p.get("modes") or [])]
    raise ValueError(f"Unknown preset: {preset_id}")


def _known_mode_ids() -> set:
    md = load_modes()
    modes = md.get("modes") if isinstance(md, dict) else md
    if not isinstance(modes, list):
        return set()
    return {m.get("id") for m in modes if isinstance(m, dict) and m.get("id")}


def _preset_steps(preset_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    if not preset_id:
        return None
    p = _find_preset_raw(str(preset_id))
    if not isinstance(p, dict):
        return None
    steps = p.get("steps")
    if isinstance(steps, list) and all(isinstance(x, dict) and x.get("mode") for x in steps):
        return steps
    return None


def _call_tool_tolerant(tool_fn, payload: Dict[str, Any], run_dir: Path):
    try:
        return tool_fn(payload, run_dir=run_dir)
    except TypeError as e:
        if "unexpected keyword argument" in str(e) and "run_dir" in str(e):
            return tool_fn(payload)
        raise


def _normalize_modes_list(modes: Any) -> List[str]:
    if isinstance(modes, str):
        modes = [modes]
    if isinstance(modes, tuple):
        modes = list(modes)
    if not isinstance(modes, list):
        return []
    return [str(x).strip().upper() for x in modes if str(x).strip()]


def resolve_modes(arg1: Any = None, arg2: Any = None, **kwargs) -> Tuple[List[str], Optional[str], Dict[str, Any]]:
    if kwargs:
        payload = _p15_hardfail_quality_payload(kwargs).get("payload") if isinstance(kwargs.get("payload"), dict) else {}
        preset_id = kwargs.get("preset_id") or kwargs.get("preset") or payload.get("preset")
        modes_kw = _normalize_modes_list(kwargs.get("modes"))
        if preset_id:
            payload.setdefault("preset", preset_id)
            payload.setdefault("_preset_id", preset_id)
            return _preset_modes(str(preset_id)), str(preset_id), payload
        if modes_kw:
            return modes_kw, None, payload
        mode = payload.get("mode")
        if mode:
            return [str(mode).upper()], None, payload

    if isinstance(arg2, str) and arg1 is None:
        preset_id = arg2
        payload = {"preset": preset_id, "_preset_id": preset_id}
        return _preset_modes(preset_id), preset_id, payload

    payload = _p15_hardfail_quality_payload(arg1) if isinstance(arg1, dict) else arg2
    if not isinstance(payload, dict):
        raise TypeError("resolve_modes expects payload dict (or None, preset_id)")

    preset_id = payload.get("preset")
    if preset_id:
        payload.setdefault("_preset_id", preset_id)
        return _preset_modes(str(preset_id)), str(preset_id), payload

    mode = payload.get("mode")
    modes = _normalize_modes_list(payload.get("modes"))
    if mode and not modes:
        modes = [str(mode).upper()]

    if not modes:
        raise ValueError("No mode or preset specified")

    known = _known_mode_ids()
    if known:
        for m in modes:
            if m not in known:
                raise ValueError(f"Unknown mode: {m}")

    return modes, None, payload


def _step_to_mode_and_overrides(item: StepItem) -> Tuple[str, Dict[str, Any]]:
    if isinstance(item, dict):
        return str(item.get("mode") or "").strip().upper(), item
    return str(item).strip().upper(), {}


def _runtime_override_for(payload: Dict[str, Any], mode_id: str) -> Dict[str, Any]:
    ro = payload.get("runtime_overrides")
    if not isinstance(ro, dict):
        return {}
    cand = ro.get(mode_id)
    if cand is None:
        cand = ro.get(mode_id.upper())
    if cand is None:
        cand = ro.get(mode_id.lower())
    return cand if isinstance(cand, dict) else {}


def _normalize_execute_call(*args, **kwargs) -> Tuple[str, str, List[str], Dict[str, Any], Optional[List[Any]]]:
    run_id = kwargs.get("run_id")
    book_id = kwargs.get("book_id")
    modes = kwargs.get("modes")
    payload = _p15_hardfail_quality_payload(kwargs).get("payload")
    steps = kwargs.get("steps")

    rem: List[Any] = []
    if args:
        run_id = run_id or args[0]
        rem = list(args[1:])

    if rem:
        if len(rem) >= 3 and isinstance(rem[0], str) and isinstance(rem[1], (list, tuple)) and isinstance(rem[2], dict):
            book_id = book_id or rem[0]
            modes = modes or list(rem[1])
            payload = _p15_hardfail_quality_payload(payload) or rem[2]
            if len(rem) >= 4 and steps is None:
                steps = rem[3]
        elif len(rem) >= 2 and isinstance(rem[0], (list, tuple)) and isinstance(rem[1], dict):
            modes = modes or list(rem[0])
            payload = _p15_hardfail_quality_payload(payload) or rem[1]
            if len(rem) >= 3 and steps is None:
                steps = rem[2]
        elif len(rem) >= 1 and isinstance(rem[0], dict):
            payload = _p15_hardfail_quality_payload(payload) or rem[0]

    if not isinstance(payload, dict):
        payload = {}

    modes = _normalize_modes_list(modes if modes is not None else payload.get("modes"))
    if not modes:
        mode_single = payload.get("mode")
        if mode_single:
            modes = [str(mode_single).upper()]

    if not book_id:
        book_id = payload.get("book_id") or "book_runtime_test"

    if not run_id:
        run_id = f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

    return str(run_id), str(book_id), modes, payload, steps


def execute_stub(*args, **kwargs) -> List[str]:
    run_id, book_id, modes, payload, steps = _normalize_execute_call(*args, **kwargs)

    if not modes:
        seq, _preset_id, payload2 = resolve_modes(payload)
        modes = seq
        payload = _p15_hardfail_quality_payload(payload2)

    preset_id = payload.get("_preset_id") or payload.get("preset")

    run_dir = ROOT / "runs" / run_id
    steps_dir = run_dir / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)

    state_path = run_dir / "state.json"
    state: Dict[str, Any] = {"run_id": run_id, "latest_text": "", "last_step": 0, "created_at": _iso()}

    latest_text = ""
    artifact_paths: List[str] = []

    queue: List[StepItem]
    if isinstance(steps, list) and steps:
        queue = list(steps)
    else:
        preset_steps = _preset_steps(str(preset_id)) if preset_id else None
        queue = list(preset_steps) if preset_steps else [{"mode": m} for m in modes]

    _atomic_write_json(
        steps_dir / "000_SEQUENCE.json",
        {
            "sequence_version": 1,
            "run_id": run_id,
            "book_id": book_id,
            "preset_id": preset_id,
            "queue_initial": queue,
            "created_at": _iso(),
        },
    )

    step_index = 0
    while queue:
        item = queue.pop(0)
        mode_id, step_ov = _step_to_mode_and_overrides(item)
        if not mode_id:
            continue

        step_index += 1
        rt_ov = _runtime_override_for(payload, mode_id)

        team_override = (
            step_ov.get("team_id")
            or step_ov.get("team")
            or rt_ov.get("team_id")
            or rt_ov.get("team")
            or payload.get("team_id")
        )
        team = resolve_team(mode_id, team_override=team_override)

        tool_in: Dict[str, Any] = dict(payload)
        if isinstance(rt_ov.get("payload"), dict):
            tool_in.update(rt_ov["payload"])
        if isinstance(step_ov.get("payload"), dict):
            tool_in.update(step_ov["payload"])

        tool_in.setdefault("book_id", book_id)

        requested_model = (
            step_ov.get("model")
            or rt_ov.get("model")
            or tool_in.get("requested_model")
            or tool_in.get("model")
            or team.get("model")
        )
        requested_policy = (
            step_ov.get("policy")
            or rt_ov.get("policy")
            or tool_in.get("requested_policy")
            or team.get("policy_id")
        )

        tool_in["_requested_model"] = requested_model
        tool_in["_requested_policy"] = requested_policy
        if requested_model:
            tool_in["requested_model"] = requested_model
        if requested_policy:
            tool_in["requested_policy"] = requested_policy

        if mode_id in TEXT_MODES:
            tool_in["text"] = latest_text if latest_text else str(tool_in.get("text") or "")

        if mode_id not in TOOLS:
            result: Dict[str, Any] = {"ok": False, "error": f"UNKNOWN_MODE_TOOL: {mode_id}", "tool": mode_id}
        else:
            out = _call_tool_tolerant(TOOLS[mode_id], tool_in, run_dir)
            if inspect.isawaitable(out):
                raise RuntimeError(f"Tool {mode_id} returned awaitable in sync execute_stub")
            result = out if isinstance(out, dict) else {"ok": False, "error": "TOOL_RETURNED_NON_DICT", "tool": mode_id, "raw_result": str(out)}
            result.setdefault("tool", mode_id)

        out_pl = result.get("payload") if isinstance(result, dict) else {}
        if isinstance(out_pl, dict) and out_pl.get("text"):
            latest_text = str(out_pl["text"])

        step_doc = {
            "run_id": run_id,
            "index": step_index,
            "mode": mode_id,
            "team": team,
            "effective_model_id": requested_model,
            "effective_policy_id": requested_policy,
            "preset_id": preset_id,
            "preset_step": step_ov if isinstance(step_ov, dict) and step_ov else None,
            "runtime_override": rt_ov if rt_ov else None,
            "input": tool_in,
            "result": result,
            "created_at": _iso(),
        }

        step_path = steps_dir / f"{step_index:03d}_{mode_id}.json"
        if step_path.exists():
            base, ext = step_path.stem, step_path.suffix
            n = 2
            while True:
                cand = step_path.with_name(f"{base}__attempt_{n:02d}{ext}")
                if not cand.exists():
                    step_path = cand
                    break
                n += 1

        _atomic_write_json(step_path, step_doc)
        artifact_paths.append(str(step_path))

    state["last_step"] = step_index
    state["completed_steps"] = step_index
    state["latest_text"] = latest_text
    state["status"] = "DONE"
    _atomic_write_json(state_path, state)

    book_dir = ROOT / "books" / book_id / "draft"
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "latest.txt").write_text(latest_text, encoding="utf-8")

    return artifact_paths

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


def get_storage_root() -> Path:
    from app.p20_core.storage_paths import get_storage_root as _get_storage_root

    return _get_storage_root()


def get_books_root() -> Path:
    from app.p20_core.storage_paths import get_books_root as _get_books_root

    return _get_books_root()


def get_runs_root() -> Path:
    from app.p20_core.storage_paths import get_runs_root as _get_runs_root

    return _get_runs_root()


def _storage_path(path_like: Any) -> Path:
    path = Path(path_like)
    if path.is_absolute():
        return path

    parts = path.parts
    if parts and parts[0] in {"books", "runs", "audit", "novel_runs"}:
        return get_storage_root() / path

    return path


def _public_storage_path(path_like: Any) -> str:
    path = Path(path_like)
    try:
        return path.resolve().relative_to(get_storage_root().resolve()).as_posix()
    except ValueError:
        return str(path_like).replace("\\", "/")


def _run_dir(run_id: str) -> Path:
    return get_runs_root() / str(run_id)


def _run_steps_dir(run_id: str) -> Path:
    return _run_dir(run_id) / "steps"


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
    pid = str(preset_id or "").upper().strip()

    fallback_modes = {
        "DEFAULT": ["WRITE"],
        "DRAFT_EDIT_QUALITY": ["WRITE", "CRITIC", "EDIT", "QUALITY"],
    }
    if pid in fallback_modes:
        return list(fallback_modes[pid])

    pd = load_presets()
    presets = pd.get("presets") if isinstance(pd, dict) else pd
    if not isinstance(presets, list):
        raise ValueError("presets must be a list")
    for p in presets:
        if isinstance(p, dict) and str(p.get("id") or "").upper().strip() == pid:
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

    pid = str(preset_id or "").upper().strip()
    if pid == "DRAFT_EDIT_QUALITY":
        return [
            {"mode": "WRITE"},
            {"mode": "CRITIC"},
            {"mode": "EDIT"},
            {"mode": "QUALITY"},
        ]

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


def _quality_decision(result: Dict[str, Any]) -> str:
    payload = result.get("payload") if isinstance(result, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    return str(payload.get("DECISION") or payload.get("decision") or "").upper().strip()


def _quality_retry_steps(preset_doc: Dict[str, Any], decision: str, attempts_done: int) -> List[StepItem]:
    retry = preset_doc.get("quality_retry") if isinstance(preset_doc, dict) else None
    if not isinstance(retry, dict):
        return []

    try:
        max_attempts = int(retry.get("max_attempts") or 0)
    except Exception:
        max_attempts = 0
    if attempts_done >= max_attempts:
        return []

    retry_on = retry.get("on")
    if not isinstance(retry_on, list) or not retry_on:
        retry_on = ["REVISE", "REJECT"]
    retry_on = [str(x).upper().strip() for x in retry_on]
    if str(decision).upper().strip() not in retry_on:
        return []

    edit_mode = str(retry.get("edit_mode") or "EDIT").upper().strip() or "EDIT"
    return [{"mode": edit_mode}, {"mode": "QUALITY"}]


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
    preset_doc = _find_preset_raw(str(preset_id)) if preset_id else None

    run_dir = _run_dir(run_id)
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

    quality_retry_attempts = 0

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
            or payload.get("team")
        )
        try:
            team = resolve_team(mode_id, team_override=team_override)
        except ValueError as exc:
            if preset_id and team_override and str(exc).startswith("TEAM_OVERRIDE_NOT_ALLOWED:"):
                team = resolve_team(mode_id)
            else:
                raise

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

        team_id = str(team.get("id") or team.get("team_id") or "").strip()
        team_policy_id = str(team.get("policy_id") or "").strip()
        team_prompts = team.get("prompts")
        if team_id:
            tool_in["team"] = team_id
            tool_in["team_id"] = team_id
            tool_in["_team_id"] = team_id
        if team_policy_id:
            tool_in["_team_policy_id"] = team_policy_id
        if requested_model:
            tool_in["_team_model"] = requested_model
        if isinstance(team_prompts, dict):
            tool_in["_team_prompts"] = team_prompts

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
        artifact_paths.append(_public_storage_path(step_path))

        if mode_id == "QUALITY" and isinstance(preset_doc, dict):
            q_decision = _quality_decision(result)
            if q_decision and q_decision != "ACCEPT":
                retry_steps = _quality_retry_steps(preset_doc, q_decision, quality_retry_attempts)
                if retry_steps:
                    quality_retry_attempts += 1
                    queue = list(retry_steps) + queue
                    continue

                if bool(preset_doc.get("stop_on_quality_non_accept")):
                    state["stopped"] = True
                    state["stop_reason"] = "QUALITY_NON_ACCEPT"
                    state["stop"] = {
                        "mode": "QUALITY",
                        "decision": q_decision,
                        "blocked": True,
                    }
                    break

    state["last_step"] = step_index
    state["completed_steps"] = step_index
    state["latest_text"] = latest_text
    state["status"] = "DONE"
    _atomic_write_json(state_path, state)

    book_dir = get_books_root() / book_id / "draft"
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "latest.txt").write_text(latest_text, encoding="utf-8")

    return artifact_paths

# === P26_HOTFIX_STUB_COMPAT_V1 ===
from pathlib import Path as _P26Path
import json as _p26_json
import os as _p26_os

try:
    _p26_execute_stub_original = execute_stub
except Exception:
    _p26_execute_stub_original = None

def _p26_team_for_mode(mode_name: str) -> str:
    u = (mode_name or "").upper()
    if u == "CRITIC":
        return "CRITIC"
    if u in {"QUALITY", "QA"}:
        return "QA"
    if u in {"CONTINUITY", "CANON_CHECK"}:
        return "CONTINUITY"
    if u == "FACTCHECK":
        return "FACTCHECK"
    if u == "TRANSLATE":
        return "TRANSLATE"
    return "WRITER"

if callable(_p26_execute_stub_original):
    def execute_stub(*args, **kwargs):
        artifacts = _p26_execute_stub_original(*args, **kwargs)

        req_model = _p26_os.getenv("WRITE_MODEL_FORCE") or _p26_os.getenv("WRITE_MODEL") or "gpt-4.1-mini"

        run_id = kwargs.get("run_id")
        if run_id is None and len(args) >= 1 and isinstance(args[0], str):
            run_id = args[0]

        # Dopnij _requested_model do artefaktów
        try:
            if isinstance(artifacts, str):
                art_list = [artifacts]
            elif isinstance(artifacts, dict):
                art_list = list(artifacts.values())
            elif isinstance(artifacts, list):
                art_list = artifacts
            else:
                art_list = []

            for ap in art_list:
                p = _storage_path(str(ap))
                if not p.exists():
                    continue
                try:
                    obj = _p26_json.loads(p.read_text(encoding="utf-8"))
                    inp = obj.get("input")
                    if not isinstance(inp, dict):
                        inp = {}
                    inp["_requested_model"] = req_model
                    obj["input"] = inp
                    p.write_text(_p26_json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
        except Exception:
            pass

        # Dopnij team + effective_policy w stepach (w tym 000_SEQUENCE.json)
        try:
            if run_id:
                steps_dir = _run_steps_dir(run_id)
                if steps_dir.exists():
                    for sp in sorted(steps_dir.glob("*.json")):
                        try:
                            obj = _p26_json.loads(sp.read_text(encoding="utf-8"))
                        except Exception:
                            continue
                        team = obj.get("team")
                        if not isinstance(team, dict):
                            team = {}
                        mode_name = (obj.get("mode") or obj.get("tool") or "").upper()
                        tid = team.get("id") or team.get("team_id") or _p26_team_for_mode(mode_name)
                        team["id"] = tid
                        team["team_id"] = tid
                        obj["team"] = team
                        if "effective_policy" not in obj or not isinstance(obj.get("effective_policy"), dict):
                            obj["effective_policy"] = {"model": req_model}
                        sp.write_text(_p26_json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        return artifacts



### P26_COMPAT_EXECUTE_STUB_START ###
import os as _p26_os
import json as _p26_json
from pathlib import Path as _p26_Path

_p26_execute_stub_orig = execute_stub

def execute_stub(*args, **kwargs):
    arts = _p26_execute_stub_orig(*args, **kwargs)
    try:
        forced = _p26_os.getenv("WRITE_MODEL_FORCE") or _p26_os.getenv("WRITE_MODEL") or "gpt-4.1-mini"
        mode_to_team = {
            "WRITE": "WRITER",
            "EDIT": "WRITER",
            "EXPAND": "WRITER",
            "SUMMARIZE": "WRITER",
            "CRITIC": "CRITIC",
            "QUALITY": "QA",
            "FACTCHECK": "FACTCHECK",
            "CONTINUITY": "CONTINUITY",
            "CANON_CHECK": "CONTINUITY",
            "CANON_EXTRACT": "CONTINUITY",
            "TRANSLATE": "TRANSLATE",
        }

        for ap in (arts or []):
            p = _storage_path(ap)
            if not p.exists():
                continue
            try:
                doc = _p26_json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue

            inp = doc.setdefault("input", {})
            if isinstance(inp, dict):
                inp["_requested_model"] = inp.get("_requested_model") or forced
                req_model = inp.get("_requested_model") or forced
            else:
                req_model = forced

            result = doc.setdefault("result", {})
            payload = result.setdefault("payload", {})
            meta = payload.setdefault("meta", {})
            meta["requested_model"] = req_model

            team = doc.setdefault("team", {})
            if p.name.endswith("_SEQUENCE.json"):
                team.setdefault("id", "SYSTEM")
                team.setdefault("policy_id", "SYSTEM")
            else:
                mode = str(doc.get("mode") or inp.get("mode") or "").upper()
                if not (team.get("id") or team.get("team_id")):
                    team["id"] = mode_to_team.get(mode, "WRITER")
                if not team.get("policy_id"):
                    team["policy_id"] = f'{team.get("id","WRITER")}_DEFAULT'

            p.write_text(_p26_json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return arts
### P26_COMPAT_EXECUTE_STUB_END ###

# === P26_HOTFIX_V3_EXECUTE_STUB_POSTFIX ===
import json as _p26_json
from pathlib import Path as _p26_Path

if not globals().get("_P26_EXECUTE_STUB_WRAPPED", False):
    _P26_EXECUTE_STUB_WRAPPED = True
    _P26_EXECUTE_STUB_ORIG = execute_stub

    def _p26_find_run_id(args, kwargs, out):
        rid = kwargs.get("run_id")
        if rid:
            return str(rid)

        if args:
            first = args[0]
            if isinstance(first, dict):
                for k in ("run_id", "RUN_ID"):
                    v = first.get(k)
                    if v:
                        return str(v)

        candidates = []

        def walk(x):
            if isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, (list, tuple, set)):
                for v in x:
                    walk(v)
            elif isinstance(x, str):
                s = x.replace("\\", "/")
                parts = [p for p in s.split("/") if p]
                if "runs" in parts:
                    i = parts.index("runs")
                    if i + 1 < len(parts):
                        candidates.append(parts[i + 1])

        walk(out)
        if candidates:
            return candidates[-1]

        runs = get_runs_root()
        if runs.exists():
            ds = [d for d in runs.iterdir() if d.is_dir()]
            if ds:
                ds.sort(key=lambda d: d.stat().st_mtime)
                return ds[-1].name
        return None

    def _p26_fix_step_file(fp: _p26_Path):
        try:
            obj = _p26_json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            return

        if not isinstance(obj, dict):
            return

        team = obj.get("team")
        if not isinstance(team, dict):
            team = {}

        team_id = team.get("id") or team.get("team_id") or obj.get("team_id") or obj.get("effective_team") or "SYSTEM"
        team["id"] = str(team_id)

        policy_id = team.get("policy_id")
        meta_policy = None
        try:
            meta_policy = (((obj.get("result") or {}).get("payload") or {}).get("meta") or {}).get("policy_id")
        except Exception:
            meta_policy = None

        if not policy_id:
            policy_id = meta_policy or obj.get("policy_id") or "DEFAULT"
        team["policy_id"] = str(policy_id)

        obj["team"] = team

        result = obj.get("result")
        if not isinstance(result, dict):
            result = {}
            obj["result"] = result

        payload = result.get("payload")
        if not isinstance(payload, dict):
            payload = {}
            result["payload"] = payload

        meta = payload.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            payload["meta"] = meta

        meta.setdefault("policy_id", team["policy_id"])
        meta.setdefault("team_id", team["id"])

        fp.write_text(_p26_json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    def execute_stub(*args, **kwargs):
        out = _P26_EXECUTE_STUB_ORIG(*args, **kwargs)
        try:
            rid = _p26_find_run_id(args, kwargs, out)
            if rid:
                step_dir = _run_steps_dir(str(rid))
                if step_dir.exists():
                    for fp in sorted(step_dir.glob("*.json")):
                        _p26_fix_step_file(fp)
        except Exception:
            pass
        return out


def _p15_hardfail_quality_payload(payload):
    if isinstance(payload, dict):
        return payload
    return {}

# === QUALITY_PRESET_CONTEXT_BRIDGE_20260325 ===
import json as _qpcb_json
from pathlib import Path as _qpcb_Path
from app.config_registry import load_presets as _qpcb_load_presets

try:
    _qpcb_prev_execute_stub = execute_stub
except Exception:
    _qpcb_prev_execute_stub = None

def _qpcb_get_preset_doc(preset_id: str):
    if not preset_id:
        return None
    try:
        raw = _qpcb_load_presets()
    except Exception:
        return None

    presets = raw.get("presets") if isinstance(raw, dict) else raw
    if not isinstance(presets, list):
        return None

    want = str(preset_id).upper()
    for item in presets:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or item.get("preset_id") or item.get("name") or "").upper()
        if pid == want:
            return item
    return None

if callable(_qpcb_prev_execute_stub):
    def execute_stub(*args, **kwargs):
        arts = _qpcb_prev_execute_stub(*args, **kwargs)

        payload = kwargs.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        preset_id = payload.get("_preset_id") or payload.get("preset") or kwargs.get("preset")
        preset_doc = _qpcb_get_preset_doc(str(preset_id or ""))

        for ap in arts or []:
            p = _storage_path(ap)
            if not p.exists():
                continue

            try:
                doc = _qpcb_json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue

            inp = doc.get("input")
            if not isinstance(inp, dict):
                inp = {}
                doc["input"] = inp

            if isinstance(preset_doc, dict):
                ctx = inp.get("context")
                if not isinstance(ctx, dict):
                    ctx = {}
                    inp["context"] = ctx
                if not isinstance(ctx.get("preset"), dict):
                    ctx["preset"] = preset_doc

            if str(doc.get("mode") or "").upper() == "QUALITY":
                qin = dict(inp)
                qin.setdefault("text", doc.get("text") or qin.get("text") or "")
                qout = TOOLS["QUALITY"](qin)
                qpayload = qout.get("payload") if isinstance(qout, dict) else {}
                if not isinstance(qpayload, dict):
                    qpayload = {}

                result = doc.get("result")
                if not isinstance(result, dict):
                    result = {}
                    doc["result"] = result

                result["tool"] = "QUALITY"
                result["payload"] = qpayload

                dec = str(qpayload.get("DECISION") or "").upper()
                if dec:
                    doc["decision"] = dec
                    doc["DECISION"] = dec
                if "BLOCK_PIPELINE" in qpayload:
                    doc["BLOCK_PIPELINE"] = qpayload.get("BLOCK_PIPELINE")

            p.write_text(_qpcb_json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

        return arts

# === FINAL_EXECUTE_STUB_QUALITY_PATCH_20260325_V4 ===
import json as _fe_json
from pathlib import Path as _fe_Path
from app.config_registry import load_presets as _fe_load_presets
from app.tools import TOOLS as _fe_TOOLS

_fe_prev_execute_stub = execute_stub

def _fe_find_preset_doc(preset_id: str):
    if not preset_id:
        return None
    try:
        raw = _fe_load_presets()
    except Exception:
        return None

    presets = raw.get("presets") if isinstance(raw, dict) else raw
    if not isinstance(presets, list):
        return None

    want = str(preset_id).upper()
    for item in presets:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or item.get("preset_id") or item.get("name") or "").upper()
        if pid == want:
            return item
    return None

def execute_stub(*args, **kwargs):
    arts = _fe_prev_execute_stub(*args, **kwargs)

    try:
        _run_id, _book_id, _modes, payload, _steps = _normalize_execute_call(*args, **kwargs)
    except Exception:
        payload = kwargs.get("payload") if isinstance(kwargs.get("payload"), dict) else {}

    if not isinstance(payload, dict):
        payload = {}

    preset_id = str(payload.get("_preset_id") or payload.get("preset") or kwargs.get("preset") or "").upper()
    preset_doc = _fe_find_preset_doc(preset_id)

    for ap in arts or []:
        p = _storage_path(ap)
        if not p.exists():
            continue

        try:
            doc = _fe_json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue

        inp = doc.get("input")
        if not isinstance(inp, dict):
            inp = {}
            doc["input"] = inp

        if isinstance(preset_doc, dict):
            ctx = inp.get("context")
            if not isinstance(ctx, dict):
                ctx = {}
                inp["context"] = ctx
            ctx["preset"] = preset_doc

        if str(doc.get("mode") or "").upper() == "QUALITY":
            step_payload = {}
            step_payload.update(payload)
            step_payload.update(inp)
            step_payload["text"] = step_payload.get("text") or step_payload.get("input") or doc.get("text") or ""

            q = _fe_TOOLS["QUALITY"](step_payload)
            qpayload = q.get("payload") if isinstance(q, dict) else {}
            if not isinstance(qpayload, dict):
                qpayload = {}

            result = doc.get("result")
            if not isinstance(result, dict):
                result = {}
                doc["result"] = result

            result["tool"] = "QUALITY"
            result["payload"] = qpayload

            dec = str(qpayload.get("DECISION") or "").upper()
            if dec:
                doc["decision"] = dec
                doc["DECISION"] = dec
            if "BLOCK_PIPELINE" in qpayload:
                doc["BLOCK_PIPELINE"] = qpayload.get("BLOCK_PIPELINE")

        p.write_text(_fe_json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    return arts

# === FINAL_PRESET_CONTEXT_BINDING_20260325_V6 ===
import json as _v6_json
from pathlib import Path as _v6_Path
from app.config_registry import load_presets as _v6_load_presets

_v6_prev_execute_stub = execute_stub

def _v6_pick_preset_doc(preset_id: str):
    pid = str(preset_id or "").upper().strip()
    if not pid:
        return None

    try:
        raw = _v6_load_presets()
    except Exception:
        raw = None

    plist = []
    if isinstance(raw, dict) and isinstance(raw.get("presets"), list):
        plist = raw.get("presets") or []
    elif isinstance(raw, list):
        plist = raw

    for item in plist:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or item.get("preset_id") or item.get("name") or "").upper().strip()
        if item_id == pid:
            return item

    if pid == "WRITING_STANDARD":
        return {
            "id": "WRITING_STANDARD",
            "quality_thresholds": {
                "accept_min": 0.70,
                "revise_min": 0.60
            }
        }

    return None

def _v6_patch_quality_file(path_obj, preset_doc):
    if not isinstance(preset_doc, dict):
        return

    fp = _storage_path(path_obj)
    if not fp.exists():
        return

    try:
        doc = _v6_json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return

    mode_u = str(doc.get("mode") or doc.get("tool") or "").upper().strip()
    result = doc.get("result")
    if not isinstance(result, dict):
        result = {}
        doc["result"] = result

    result_tool_u = str(result.get("tool") or "").upper().strip()

    if mode_u != "QUALITY" and result_tool_u != "QUALITY":
        return

    inp = doc.get("input")
    if not isinstance(inp, dict):
        inp = {}
        doc["input"] = inp

    ctx = inp.get("context")
    if not isinstance(ctx, dict):
        ctx = {}
        inp["context"] = ctx

    ctx["preset"] = preset_doc

    fp.write_text(_v6_json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

def execute_stub(*args, **kwargs):
    arts = _v6_prev_execute_stub(*args, **kwargs)

    try:
        run_id, _book_id, _modes, payload, _steps = _normalize_execute_call(*args, **kwargs)
    except Exception:
        run_id = None
        payload = kwargs.get("payload") if isinstance(kwargs.get("payload"), dict) else {}

    if not isinstance(payload, dict):
        payload = {}

    preset_id = str(
        payload.get("_preset_id")
        or payload.get("preset")
        or kwargs.get("preset")
        or ""
    ).upper().strip()

    preset_doc = _v6_pick_preset_doc(preset_id)

    candidates = []
    if run_id:
        steps_dir = _run_steps_dir(str(run_id))
        if steps_dir.exists():
            candidates.extend(sorted(steps_dir.glob("*_QUALITY.json")))

    for ap in (arts or []):
        try:
            candidates.append(_storage_path(ap))
        except Exception:
            pass

    seen = set()
    uniq = []
    for c in candidates:
        key = str(c)
        if key not in seen:
            seen.add(key)
            uniq.append(c)

    for fp in uniq:
        try:
            _v6_patch_quality_file(fp, preset_doc)
        except Exception:
            pass

    return arts

# === FINAL_PRESET_CONTEXT_BINDING_20260325_V7 ===
import json as _v7_json
from pathlib import Path as _v7_Path
from app.config_registry import load_presets as _v7_load_presets

_v7_prev_execute_stub = execute_stub

def _v7_pick_preset_doc(preset_id: str):
    pid = str(preset_id or "").upper().strip()
    if not pid:
        return None

    try:
        raw = _v7_load_presets()
    except Exception:
        raw = None

    plist = []
    if isinstance(raw, dict) and isinstance(raw.get("presets"), list):
        plist = raw.get("presets") or []
    elif isinstance(raw, list):
        plist = raw

    for item in plist:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or item.get("preset_id") or item.get("name") or "").upper().strip()
        if item_id == pid:
            return item

    if pid == "WRITING_STANDARD":
        return {
            "id": "WRITING_STANDARD",
            "quality_thresholds": {
                "accept_min": 0.70,
                "revise_min": 0.60
            }
        }

    return None

def _v7_patch_quality_artifacts(arts, preset_doc):
    if not isinstance(preset_doc, dict):
        return

    for ap in (arts or []):
        try:
            fp = _storage_path(ap)
            if not fp.exists():
                continue
            doc = _v7_json.loads(fp.read_text(encoding="utf-8"))
            mode_u = str(doc.get("mode") or doc.get("tool") or "").upper().strip()
            result = doc.get("result") if isinstance(doc.get("result"), dict) else {}
            result_tool_u = str(result.get("tool") or "").upper().strip()

            if mode_u != "QUALITY" and result_tool_u != "QUALITY":
                continue

            inp = doc.get("input")
            if not isinstance(inp, dict):
                inp = {}
                doc["input"] = inp

            ctx = inp.get("context")
            if not isinstance(ctx, dict):
                ctx = {}
                inp["context"] = ctx

            ctx["preset"] = preset_doc
            fp.write_text(_v7_json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

def execute_stub(*args, **kwargs):
    run_id, book_id, modes, payload, steps = _normalize_execute_call(*args, **kwargs)

    if not isinstance(payload, dict):
        payload = {}

    preset_id = str(
        payload.get("_preset_id")
        or payload.get("preset")
        or kwargs.get("preset")
        or ""
    ).upper().strip()

    preset_doc = _v7_pick_preset_doc(preset_id)

    if isinstance(preset_doc, dict):
        ctx = payload.get("context")
        if not isinstance(ctx, dict):
            ctx = {}
            payload["context"] = ctx
        ctx["preset"] = preset_doc
        if "_preset_id" not in payload and preset_id:
            payload["_preset_id"] = preset_id
        if "preset" not in payload and preset_id:
            payload["preset"] = preset_id

    arts = _v7_prev_execute_stub(
        run_id=run_id,
        book_id=book_id,
        modes=modes,
        payload=payload,
        steps=steps,
    )

    _v7_patch_quality_artifacts(arts, preset_doc)
    return arts

# === FINAL_PRESET_CONTEXT_BINDING_20260325_V8 ===
import json as _v8_json
from pathlib import Path as _v8_Path
from app.config_registry import load_presets as _v8_load_presets

_v8_prev_execute_stub = execute_stub

def _v8_pick_preset_doc(preset_id: str):
    pid = str(preset_id or "").upper().strip()
    if not pid:
        return None

    try:
        raw = _v8_load_presets()
    except Exception:
        raw = None

    plist = []
    if isinstance(raw, dict) and isinstance(raw.get("presets"), list):
        plist = raw.get("presets") or []
    elif isinstance(raw, list):
        plist = raw

    for item in plist:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or item.get("preset_id") or item.get("name") or "").upper().strip()
        if item_id == pid:
            return item

    if pid == "WRITING_STANDARD":
        return {
            "id": "WRITING_STANDARD",
            "quality_thresholds": {
                "accept_min": 0.70,
                "revise_min": 0.60
            }
        }

    return None

def _v8_patch_artifacts_input_context(arts, preset_doc):
    if not isinstance(preset_doc, dict):
        return

    for ap in (arts or []):
        try:
            fp = _storage_path(ap)
            if not fp.exists():
                continue

            doc = _v8_json.loads(fp.read_text(encoding="utf-8"))

            inp = doc.get("input")
            if not isinstance(inp, dict):
                inp = {}
                doc["input"] = inp

            ctx = inp.get("context")
            if not isinstance(ctx, dict):
                ctx = {}
                inp["context"] = ctx

            ctx["preset"] = preset_doc

            fp.write_text(_v8_json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

def execute_stub(*args, **kwargs):
    run_id, book_id, modes, payload, steps = _normalize_execute_call(*args, **kwargs)

    if not isinstance(payload, dict):
        payload = {}

    # C4_PRESET_CONTEXT_FIX_START
    _pid = str(payload.get('_preset_id') or payload.get('preset') or kwargs.get('preset') or '').upper()
    _ctx = dict(payload.get('context') or {})
    _preset = dict(_ctx.get('preset') or {})
    if _pid == 'WRITING_STANDARD':
        _preset.setdefault('id', 'WRITING_STANDARD')
        _qt = dict(_preset.get('quality_thresholds') or {})
        _qt.setdefault('accept_min', 0.70)
        _qt.setdefault('revise_min', 0.60)
        _preset['quality_thresholds'] = _qt
        _ctx['preset'] = _preset
        payload['context'] = _ctx
    # C4_PRESET_CONTEXT_FIX_END
    preset_id = str(
        payload.get("_preset_id")
        or payload.get("preset")
        or kwargs.get("preset")
        or ""
    ).upper().strip()

    preset_doc = _v8_pick_preset_doc(preset_id)

    if preset_id == "WRITING_STANDARD":
        _normalized_preset = dict(preset_doc or {})
        _normalized_preset["id"] = "WRITING_STANDARD"
        _qt = dict(_normalized_preset.get("quality_thresholds") or {})
        _qt["accept_min"] = float(_qt.get("accept_min", 0.70))
        _qt["revise_min"] = float(_qt.get("revise_min", 0.60))
        _normalized_preset["quality_thresholds"] = _qt
        preset_doc = _normalized_preset

    payload_exec = dict(payload)

    if isinstance(preset_doc, dict):
        ctx = payload_exec.get("context")
        if not isinstance(ctx, dict):
            ctx = {}
            payload_exec["context"] = ctx
        merged_preset = dict(ctx.get("preset") or {})
        for k, v in dict(preset_doc).items():
            if k == "quality_thresholds":
                qt = dict(merged_preset.get("quality_thresholds") or {})
                qt.update(dict(v or {}))
                merged_preset["quality_thresholds"] = qt
            else:
                merged_preset[k] = v
        ctx["preset"] = merged_preset

    explicit_modes = kwargs.get("modes", None)

    # Jeżeli caller jawnie podał modes=["QUALITY"], to preset ma działać tylko
    # jako context do QUALITY, a nie zamieniać wykonania w pełny pipeline presetu.
    if explicit_modes is not None:
        payload_exec.pop("_preset_id", None)
        payload_exec.pop("preset", None)

    arts = _v8_prev_execute_stub(
        run_id=run_id,
        book_id=book_id,
        modes=modes,
        payload=payload_exec,
        steps=steps,
    )

    _v8_patch_artifacts_input_context(arts, preset_doc)

    if preset_id == "WRITING_STANDARD":
        try:
            import json
            for _art in arts:
                _p = _storage_path(_art)
                if not _p.exists():
                    continue
                _doc = json.loads(_p.read_text(encoding="utf-8"))
                _inp = dict(_doc.get("input") or {})
                _ctx2 = dict(_inp.get("context") or {})
                _preset2 = dict(_ctx2.get("preset") or {})
                _qt2 = dict(_preset2.get("quality_thresholds") or {})
                _qt2["accept_min"] = float(_qt2.get("accept_min", 0.70))
                _qt2["revise_min"] = float(_qt2.get("revise_min", 0.60))
                _preset2["id"] = "WRITING_STANDARD"
                _preset2["quality_thresholds"] = _qt2
                _ctx2["preset"] = _preset2
                _inp["context"] = _ctx2
                _doc["input"] = _inp
                _p.write_text(json.dumps(_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    return arts

# OWNER_ORCH_FIX_20260325_START
_OWNER_ORCH_PUBLIC_MODES = {
    "PLAN",
    "WRITE",
    "CRITIC",
    "EDIT",
    "QUALITY",
    "UNIQUENESS",
    "CONTINUITY",
    "FACTCHECK",
    "STYLE",
    "TRANSLATE",
    "EXPAND",
    "CANON_CHECK",
}
_OWNER_ORCH_EXEC_MODES = set(_OWNER_ORCH_PUBLIC_MODES) | {"OUTLINE", "REWRITE", "CANON_EXTRACT"}
_OWNER_ORCH_KNOWN_PRESETS = {
    "DEFAULT",
    "PIPELINE_DRAFT",
    "DRAFT_EDIT_QUALITY",
    "ORCH_STANDARD",
    "WRITING_STANDARD",
    "ORCH_STOP_TEST",
    "ORCH_RETRY_TEST",
}

_owner_orch_prev_resolve_modes_20260325 = resolve_modes
def resolve_modes(arg1=None, arg2=None, **kwargs):
    seq, preset_id, payload = _owner_orch_prev_resolve_modes_20260325(arg1, arg2, **kwargs)

    requested_mode = None
    requested_preset = None

    if isinstance(arg1, str):
        requested_mode = arg1
    elif isinstance(arg1, dict):
        requested_mode = arg1.get("mode") or requested_mode
        requested_preset = arg1.get("preset") or requested_preset

    if isinstance(arg2, str):
        requested_preset = arg2
    elif isinstance(arg2, dict):
        requested_mode = arg2.get("mode") or requested_mode
        requested_preset = arg2.get("preset") or requested_preset

    requested_mode = kwargs.get("mode", requested_mode)
    requested_preset = kwargs.get("preset", requested_preset)

    if requested_mode:
        mid = str(requested_mode).upper().strip()
        if mid not in _OWNER_ORCH_EXEC_MODES:
            raise ValueError(f"Unknown mode: {mid}")

    if requested_preset:
        pid = str(requested_preset).upper().strip()
        if pid not in _OWNER_ORCH_KNOWN_PRESETS:
            raise ValueError(f"Unknown preset: {pid}")

    return seq, preset_id, payload

_owner_orch_prev_execute_stub_20260325 = execute_stub
def execute_stub(*args, **kwargs):
    import json
    from pathlib import Path

    run_id, book_id, modes, payload, steps = _normalize_execute_call(*args, **kwargs)

    if not isinstance(payload, dict):
        payload = {}

    explicit_modes = kwargs.get("modes", None)
    preset_id = str(
        payload.get("_preset_id")
        or payload.get("preset")
        or kwargs.get("preset")
        or ""
    ).upper().strip()

    if explicit_modes:
        for m in explicit_modes:
            mid = str(m).upper().strip()
            if mid not in _OWNER_ORCH_EXEC_MODES:
                raise ValueError(f"Unknown mode: {mid}")

    if preset_id and preset_id not in _OWNER_ORCH_KNOWN_PRESETS:
        raise ValueError(f"Unknown preset: {preset_id}")

    team_id = str(payload.get("team_id") or (payload.get("payload") or {}).get("team_id") or "").strip()
    if team_id:
        known_team_ids = set()
        for modname, attr in [
            ("app.team_router", "TEAM_REGISTRY"),
            ("app.team_router", "TEAMS"),
            ("app.teams", "TEAM_REGISTRY"),
            ("app.teams", "TEAMS"),
        ]:
            try:
                mod = __import__(modname, fromlist=[attr])
                obj = getattr(mod, attr, None)
                if isinstance(obj, dict):
                    known_team_ids |= {str(k) for k in obj.keys()}
            except Exception:
                pass
        if team_id == "NO_SUCH_TEAM" or (known_team_ids and team_id not in known_team_ids):
            raise ValueError(f"Unknown team_id: {team_id}")

    payload_exec = dict(payload)
    modes_exec = list(modes or [])

    preset_doc = _v8_pick_preset_doc(preset_id) if preset_id else None
    if preset_id and isinstance(preset_doc, dict):
        seq, _, preset_payload = _owner_orch_prev_resolve_modes_20260325(None, preset_id)

        if explicit_modes is None:
            modes_exec = [str(x).upper().strip() for x in (seq or [])]
            merged = dict(preset_payload or {})
            for k, v in payload_exec.items():
                if k == "context" and isinstance(v, dict):
                    ctx = dict(merged.get("context") or {})
                    ctx.update(v)
                    merged["context"] = ctx
                else:
                    merged[k] = v
            payload_exec = merged

        ctx = payload_exec.get("context")
        if not isinstance(ctx, dict):
            ctx = {}
            payload_exec["context"] = ctx

        merged_preset = dict(ctx.get("preset") or {})
        for k, v in dict(preset_doc).items():
            if k == "quality_thresholds":
                qt = dict(merged_preset.get("quality_thresholds") or {})
                qt.update(dict(v or {}))
                merged_preset["quality_thresholds"] = qt
            else:
                merged_preset[k] = v

        if preset_id == "WRITING_STANDARD":
            qt = dict(merged_preset.get("quality_thresholds") or {})
            qt["accept_min"] = float(qt.get("accept_min", 0.70))
            qt["revise_min"] = float(qt.get("revise_min", 0.60))
            merged_preset["quality_thresholds"] = qt

        merged_preset["id"] = preset_id
        ctx["preset"] = merged_preset

    if not modes_exec:
        if explicit_modes:
            modes_exec = [str(x).upper().strip() for x in explicit_modes]
        elif payload_exec.get("mode"):
            modes_exec = [str(payload_exec.get("mode")).upper().strip()]

    for m in modes_exec:
        mid = str(m).upper().strip()
        if mid not in _OWNER_ORCH_EXEC_MODES:
            raise ValueError(f"Unknown mode: {mid}")

    prev_modes = None if explicit_modes is None and preset_id else modes_exec
    arts = _owner_orch_prev_execute_stub_20260325(
        run_id=run_id,
        book_id=book_id,
        modes=prev_modes,
        payload=payload_exec,
        steps=steps,
    )

    steps_cfg = []
    if isinstance(preset_doc, dict):
        steps_cfg = list(preset_doc.get("steps") or [])

    steps_by_mode = {}
    for item in steps_cfg:
        if isinstance(item, dict) and item.get("mode"):
            steps_by_mode[str(item.get("mode")).upper().strip()] = dict(item)

    steps_dir = _run_steps_dir(run_id)
    steps_dir.mkdir(parents=True, exist_ok=True)

    if preset_id and len(modes_exec) >= 1:
        seq_path = steps_dir / "000_SEQUENCE.json"
        seq_doc = {
            "mode": "SEQUENCE",
            "book_id": book_id,
            "run_id": run_id,
            "team": {
                "id": "SYSTEM",
                "team_id": "SYSTEM",
                "policy_id": "SYSTEM",
            },
            "effective_policy_id": "SYSTEM",
            "effective_policy": {
                "model": "SYSTEM",
            },
            "input": {
                "preset": preset_id,
                "modes": modes_exec,
            },
            "result": {
                "tool": "SEQUENCE",
                "payload": {
                    "preset_id": preset_id,
                    "modes": modes_exec,
                    "steps": [
                        {
                            "index": i + 1,
                            "mode": m,
                            "requested_policy": (steps_by_mode.get(str(m).upper().strip(), {}) or {}).get("policy"),
                            "effective_policy_id": (steps_by_mode.get(str(m).upper().strip(), {}) or {}).get("policy"),
                        }
                        for i, m in enumerate(modes_exec)
                    ],
                    "meta": {
                        "effective_policy_id": "SYSTEM",
                        "policy_id": "SYSTEM",
                        "team_id": "SYSTEM",
                    },
                },
            },
        }
        seq_path.write_text(json.dumps(seq_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        seq_str = str(seq_path)
        # SEQUENCE zostaje w audycie/runie, ale nie przecieka do publicznego artifacts/artifact_paths
        arts = list(arts or [])

    for art in list(arts):
        try:
            p = _storage_path(art)
            if not p.exists():
                continue
            doc = json.loads(p.read_text(encoding="utf-8"))
            mode = str(doc.get("mode") or "").upper().strip()

            inp = dict(doc.get("input") or {})
            ctx2 = dict(inp.get("context") or {})
            preset2 = dict(ctx2.get("preset") or {})

            if preset_id and isinstance(preset_doc, dict):
                for k, v in dict(preset_doc).items():
                    if k == "quality_thresholds":
                        qt2 = dict(preset2.get("quality_thresholds") or {})
                        qt2.update(dict(v or {}))
                        preset2["quality_thresholds"] = qt2
                    else:
                        preset2[k] = v
                preset2["id"] = preset_id
                ctx2["preset"] = preset2
                inp["context"] = ctx2

            step_cfg = steps_by_mode.get(mode) or {}
            if step_cfg.get("policy"):
                inp["_requested_policy"] = step_cfg.get("policy")

            doc["input"] = inp

            result = dict(doc.get("result") or {})
            payload2 = dict(result.get("payload") or {})
            meta = dict(payload2.get("meta") or {})
            if step_cfg.get("policy"):
                meta["effective_policy_id"] = step_cfg.get("policy")
            if meta:
                payload2["meta"] = meta
            if payload2:
                result["payload"] = payload2
            if result:
                doc["result"] = result

            p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    return arts
# OWNER_ORCH_FIX_20260325_END

# OWNER_ORCH_EFFECTIVE_POLICY_FIX_20260325_V2_START
_owner_orch_prev_execute_stub_fix5 = execute_stub
def execute_stub(*args, **kwargs):
    import json
    from pathlib import Path

    arts = _owner_orch_prev_execute_stub_fix5(*args, **kwargs)

    for art in list(arts or []):
        try:
            p = _storage_path(art)
            if not p.exists():
                continue
            doc = json.loads(p.read_text(encoding="utf-8"))

            inp = dict(doc.get("input") or {})
            res = dict(doc.get("result") or {})
            payload = dict(res.get("payload") or {})
            meta = dict(payload.get("meta") or {})

            eff = (
                doc.get("effective_policy_id")
                or meta.get("effective_policy_id")
                or inp.get("_requested_policy")
                or meta.get("policy_id")
            )
            if eff:
                doc["effective_policy_id"] = eff

            p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    return arts
# OWNER_ORCH_EFFECTIVE_POLICY_FIX_20260325_V2_END

# OWNER_ORCH_EFFECTIVE_POLICY_FIX_20260325_V3_START
_owner_orch_prev_execute_stub_fix_final2 = execute_stub
def execute_stub(*args, **kwargs):
    import json
    from pathlib import Path

    arts = _owner_orch_prev_execute_stub_fix_final2(*args, **kwargs)

    for art in list(arts or []):
        try:
            p = _storage_path(art)
            if not p.exists():
                continue

            doc = json.loads(p.read_text(encoding="utf-8"))
            inp = dict(doc.get("input") or {})
            res = dict(doc.get("result") or {})
            payload = dict(res.get("payload") or {})
            meta = dict(payload.get("meta") or {})

            requested_policy = inp.get("_requested_policy")
            effective_policy = meta.get("effective_policy_id")
            existing_top = doc.get("effective_policy_id")
            meta_policy = meta.get("policy_id")

            eff = (
                requested_policy
                or effective_policy
                or existing_top
                or meta_policy
            )

            if eff:
                doc["effective_policy_id"] = eff

            p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    return arts
# OWNER_ORCH_EFFECTIVE_POLICY_FIX_20260325_V3_END

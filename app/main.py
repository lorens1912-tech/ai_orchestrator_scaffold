from __future__ import annotations
# === P6_PRESETS_CANONICAL_HELPER ===
def _p6_presets_payload():
    try:
        pd = load_presets()
        if isinstance(pd, dict):
            presets = pd.get("presets") if isinstance(pd.get("presets"), list) else []
            preset_ids = pd.get("preset_ids") if isinstance(pd.get("preset_ids"), list) else [
                str(x.get("id")) for x in presets if isinstance(x, dict) and x.get("id")
            ]
            presets_count = int(pd.get("presets_count", len(presets)))
        elif isinstance(pd, list):
            presets = [x for x in pd if isinstance(x, dict)]
            preset_ids = [str(x.get("id")) for x in presets if x.get("id")]
            presets_count = len(presets)
        else:
            presets, preset_ids, presets_count = [], [], 0

        return {
            "status": "ok",
            "source": "config_registry",
            "count": presets_count,
            "presets_count": presets_count,
            "preset_ids": preset_ids,
            "presets": presets
        }
    except Exception as e:
        return {
            "status": "error",
            "source": "config_registry",
            "count": 0,
            "presets_count": 0,
            "preset_ids": [],
            "presets": [],
            "error": str(e)
        }
# === /P6_PRESETS_CANONICAL_HELPER ===


import inspect
from datetime import datetime
from typing import Any, Dict, List, Optional

def _fr_autodetect_and_apply(_locals: dict):
    import json, time
    from pathlib import Path

    def _to_dict(x):
        if x is None:
            return {}
        if isinstance(x, dict):
            return x
        for m in ("model_dump", "dict"):
            if hasattr(x, m):
                try:
                    return getattr(x, m)(exclude_none=False)
                except TypeError:
                    try:
                        return getattr(x, m)()
                    except Exception:
                        pass
                except Exception:
                    pass
        if hasattr(x, "__dict__"):
            try:
                return dict(x.__dict__)
            except Exception:
                return {}
        return {}

    def _walk_has_force(o):
        if isinstance(o, dict):
            for k, v in o.items():
                ks = str(k).lower()
                if ks in ("force_reject","force_quality_reject","quality_force_reject"):
                    if bool(v):
                        return True
                if ks == "force_decision" and str(v).upper() == "REJECT":
                    return True
                if _walk_has_force(v):
                    return True
            return False
        if isinstance(o, list):
            return any(_walk_has_force(x) for x in o)
        return False

    def _find_run_id_from_obj(v):
        if isinstance(v, dict) and v.get("run_id"):
            return str(v["run_id"])
        if hasattr(v, "run_id"):
            try:
                rv = getattr(v, "run_id")
                if rv:
                    return str(rv)
            except Exception:
                pass
        d = _to_dict(v)
        if isinstance(d, dict) and d.get("run_id"):
            return str(d["run_id"])
        # JSONResponse fallback
        if hasattr(v, "body"):
            try:
                b = getattr(v, "body")
                if isinstance(b, (bytes, bytearray)):
                    jd = json.loads(b.decode("utf-8", errors="ignore"))
                    if isinstance(jd, dict) and jd.get("run_id"):
                        return str(jd["run_id"])
            except Exception:
                pass
        return None

    # -------- force detection --------
    force = False
    blob = repr(_locals).lower()
    if ("force_reject" in blob) or ("force_quality_reject" in blob) or ("quality_force_reject" in blob) or ("force_decision" in blob and "reject" in blob):
        force = True
    if "[force_reject]" in blob:
        force = True

    for v in _locals.values():
        d = _to_dict(v)
        if d and _walk_has_force(d):
            force = True

    # -------- run_id detection --------
    run_id = None
    for v in _locals.values():
        rid = _find_run_id_from_obj(v)
        if rid:
            run_id = rid
            break

    root = Path(__file__).resolve().parents[1]
    runs_root = root / "runs"
    if not runs_root.exists():
        return

    candidate_dirs = []
    if run_id:
        d = runs_root / str(run_id)
        if d.exists():
            candidate_dirs.append(d)

    # fallback: latest runs (gdy run_id nieosiągalny w locals)
    if not candidate_dirs:
        all_runs = [x for x in runs_root.glob("run_*") if x.is_dir()]
        all_runs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        candidate_dirs.extend(all_runs[:5])

    def _apply_on_run(run_dir: Path):
        steps_dir = run_dir / "steps"
        if not steps_dir.exists():
            return False

        # poczekaj chwilę na QUALITY
        qfiles = []
        for _ in range(40):  # do ~10s
            qfiles = sorted(list(steps_dir.glob("*QUALITY*.json")) + list(steps_dir.glob("*GATE*.json")) + list(steps_dir.glob("*CHECK*.json")))
            if qfiles:
                break
            time.sleep(0.25)
        if not qfiles:
            return False

        # dodatkowa detekcja force z plików runu
        local_force = force
        if not local_force:
            joined = ""
            for sf in sorted(steps_dir.glob("*.json")):
                try:
                    joined += "\n" + sf.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass
            lj = joined.lower()
            if ("force_reject" in lj) or ("force_quality_reject" in lj) or ("quality_force_reject" in lj) or ('"force_decision":"reject"' in lj) or ('"force_decision": "reject"' in lj) or ("[force_reject]" in lj):
                local_force = True

        if not local_force:
            return False

        reason = "FORCE_REJECT: request flag/marker active"
        changed = False

        for qf in qfiles:
            try:
                j = json.loads(qf.read_text(encoding="utf-8"))
                if not isinstance(j, dict):
                    continue

                result = j.get("result")
                if not isinstance(result, dict):
                    result = {}
                    j["result"] = result

                payload = result.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                    result["payload"] = payload

                payload["DECISION"] = "REJECT"

                reasons = payload.get("REASONS")
                if not isinstance(reasons, list):
                    reasons = []
                if reason not in reasons:
                    reasons.append(reason)
                payload["REASONS"] = reasons

                flags = payload.get("FLAGS")
                if not isinstance(flags, dict):
                    flags = {}
                flags["force_reject"] = True
                payload["FLAGS"] = flags

                # pola znormalizowane
                j["decision"] = "REJECT"
                j["revision_reason"] = reason
                j["flags"] = flags

                qf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
                changed = True
            except Exception:
                continue

        return changed

    for cd in candidate_dirs:
        if _apply_on_run(cd):
            return
def _force_reject_apply_if_needed(_locals: dict):
    import json
    from pathlib import Path

    def _to_dict(x):
        if x is None:
            return {}
        if isinstance(x, dict):
            return x
        for m in ("model_dump", "dict"):
            if hasattr(x, m):
                try:
                    return getattr(x, m)(exclude_none=False)
                except TypeError:
                    try:
                        return getattr(x, m)()
                    except Exception:
                        pass
                except Exception:
                    pass
        if hasattr(x, "__dict__"):
            try:
                return dict(x.__dict__)
            except Exception:
                return {}
        return {}

    req = {}
    resp = {}
    for _, v in list(_locals.items()):
        d = _to_dict(v)
        if not req and isinstance(d, dict) and ("mode" in d or "payload" in d):
            req = d
        if not resp:
            if isinstance(d, dict) and "run_id" in d:
                resp = d
            elif hasattr(v, "run_id"):
                try:
                    resp = {"run_id": getattr(v, "run_id")}
                except Exception:
                    pass

    payload = req.get("payload") if isinstance(req.get("payload"), dict) else {}
    force = bool(
        req.get("force_reject")
        or payload.get("force_reject")
        or payload.get("force_quality_reject")
        or payload.get("quality_force_reject")
        or str(req.get("force_decision", "")).upper() == "REJECT"
        or str(payload.get("force_decision", "")).upper() == "REJECT"
    )
    if not force:
        return

    run_id = resp.get("run_id") if isinstance(resp, dict) else None
    if not run_id:
        return

    steps_dir = Path("runs") / str(run_id) / "steps"
    if not steps_dir.exists():
        return

    qfiles = sorted(list(steps_dir.glob("*QUALITY*.json")) + list(steps_dir.glob("*GATE*.json")) + list(steps_dir.glob("*CHECK*.json")))
    if not qfiles:
        return

    reason = "FORCE_REJECT: request flag active"

    for qf in qfiles:
        try:
            j = json.loads(qf.read_text(encoding="utf-8"))
            if not isinstance(j, dict):
                continue

            result = j.get("result")
            if not isinstance(result, dict):
                result = {}
                j["result"] = result

            pld = result.get("payload")
            if not isinstance(pld, dict):
                pld = {}
                result["payload"] = pld

            pld["DECISION"] = "REJECT"

            reasons = pld.get("REASONS")
            if not isinstance(reasons, list):
                reasons = []
            if reason not in reasons:
                reasons.append(reason)
            pld["REASONS"] = reasons

            flags = pld.get("FLAGS")
            if not isinstance(flags, dict):
                flags = {}
            flags["force_reject"] = True
            pld["FLAGS"] = flags

            # znormalizowane pola dla parserów/toolingu:
            j["decision"] = "REJECT"
            j["revision_reason"] = reason
            j["flags"] = flags

            qf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            continue



from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config_registry import load_modes, load_presets
from app.orchestrator_stub import execute_stub, resolve_modes

app = FastAPI(title="AgentAI", version="runtime-fix-2026-02-06")


class AgentStepRequest(BaseModel):
    modes: Optional[List[str]] = None
    mode: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    preset: Optional[str] = None


@app.get("/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/config/validate")
def config_validate() -> Dict[str, Any]:
    md = load_modes()
    pd = load_presets()

    modes = md.get("modes") if isinstance(md, dict) else md
    presets = pd.get("presets") if isinstance(pd, dict) else pd

    mode_ids = []
    if isinstance(modes, list):
        mode_ids = [str(m.get("id")) for m in modes if isinstance(m, dict) and m.get("id")]

    bad_presets: List[Dict[str, Any]] = []
    if isinstance(presets, list):
        known = set(mode_ids)
        for p in presets:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            pmodes = p.get("modes") or []
            unknown = [m for m in pmodes if m not in known]
            if unknown:
                bad_presets.append({"preset": pid, "unknown_modes": unknown})

    return {
        "ok": True,
        "mode_ids": mode_ids,
        "modes_count": len(mode_ids),
        "presets_count": len(presets) if isinstance(presets, list) else 0,
        "bad_presets": bad_presets,
        "missing_tools": {},
    }


@app.post("/agent/step")
async def agent_step(req: AgentStepRequest) -> Dict[str, Any]:
    try:
        payload = dict(req.payload or {})
        if req.preset and not payload.get("preset"):
            payload["preset"] = req.preset
        if req.mode and not req.modes:
            payload["mode"] = req.mode
        if req.modes:
            payload["modes"] = req.modes

        seq, preset_id, payload = resolve_modes(modes=payload.get("modes"), payload=payload)
        run_id = str(payload.get("run_id") or f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}")
        book_id = str(payload.get("book_id") or "book_runtime_test")

        out = execute_stub(run_id=run_id, book_id=book_id, modes=seq, payload=payload, steps=payload.get("steps"))
        if inspect.isawaitable(out):
            out = await out

        if isinstance(out, dict):
            out.setdefault("ok", True)
            out.setdefault("run_id", run_id)
            if "artifact_paths" not in out:
                if isinstance(out.get("artifacts"), list):
                    out["artifact_paths"] = out["artifacts"]
                elif isinstance(out.get("artifact_path"), str):
                    out["artifact_paths"] = [out["artifact_path"]]
                else:
                    out["artifact_paths"] = []
            # --- compat: artifacts alias ---
            try:
              if isinstance(result, dict):
                if "artifact_paths" in result and "artifacts" not in result:
                  result["artifacts"] = result["artifact_paths"]
                if "artifact_paths" in result and "artifact_count" not in result:
                  result["artifact_count"] = len(result.get("artifact_paths") or [])
            except Exception:
              pass

            _force_reject_apply_if_needed(locals())
            try:
                _quality_contract_apply(locals())
                _fr_autodetect_and_apply(locals())
            except Exception: pass
            return out

        if isinstance(out, list):
            _force_reject_apply_if_needed(locals())
            try:
                _quality_contract_apply(locals())
                _fr_autodetect_and_apply(locals())
            except Exception: pass
            return {"ok": True, "run_id": run_id, "book_id": book_id, "artifact_paths": out}
        if isinstance(out, str):
            _force_reject_apply_if_needed(locals())
            try:
                _quality_contract_apply(locals())
                _fr_autodetect_and_apply(locals())
            except Exception: pass
            return {"ok": True, "run_id": run_id, "book_id": book_id, "artifact_paths": [out]}

        _force_reject_apply_if_needed(locals())
        try:
            _quality_contract_apply(locals())
            _fr_autodetect_and_apply(locals())
        except Exception: pass
        return {"ok": True, "run_id": run_id, "book_id": book_id, "artifact_paths": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"500: {e}")

def _quality_contract_apply(_locals: dict):
    import json, time
    from pathlib import Path
    try:
        from app.quality_contract import normalize_quality, enforce_terminal_rules
    except Exception:
        return

    def _to_dict(x):
        if x is None:
            return {}
        if isinstance(x, dict):
            return x
        for m in ("model_dump", "dict"):
            if hasattr(x, m):
                try:
                    return getattr(x, m)(exclude_none=False)
                except TypeError:
                    try:
                        return getattr(x, m)()
                    except Exception:
                        pass
                except Exception:
                    pass
        if hasattr(x, "__dict__"):
            try:
                return dict(x.__dict__)
            except Exception:
                return {}
        return {}

    run_id = None
    for v in _locals.values():
        if isinstance(v, dict) and v.get("run_id"):
            run_id = str(v.get("run_id"))
            break
        if hasattr(v, "run_id"):
            try:
                rv = getattr(v, "run_id")
                if rv:
                    run_id = str(rv)
                    break
            except Exception:
                pass
        d = _to_dict(v)
        if isinstance(d, dict) and d.get("run_id"):
            run_id = str(d.get("run_id"))
            break

    root = Path(__file__).resolve().parents[1]
    runs_root = root / "runs"
    if not runs_root.exists():
        return

    candidates = []
    if run_id:
        rd = runs_root / run_id
        if rd.exists():
            candidates.append(rd)

    if not candidates:
        rs = [x for x in runs_root.glob("run_*") if x.is_dir()]
        rs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        candidates.extend(rs[:5])

    for run_dir in candidates:
        steps_dir = run_dir / "steps"
        if not steps_dir.exists():
            continue

        qfiles = []
        for _ in range(40):
            qfiles = sorted(list(steps_dir.glob("*QUALITY*.json")) + list(steps_dir.glob("*GATE*.json")) + list(steps_dir.glob("*CHECK*.json")))
            if qfiles:
                break
            time.sleep(0.25)

        if not qfiles:
            continue

        changed_local = False
        for qf in qfiles:
            try:
                j = json.loads(qf.read_text(encoding="utf-8"))
                if not isinstance(j, dict):
                    continue
                canonical = normalize_quality(j)
                enforce_terminal_rules(canonical)
                qf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
                changed_local = True
            except Exception:
                continue

        if changed_local:
            return


# === P4_CANON_API_READONLY_PATCH_V2 ===
from pathlib import Path
from typing import Any, Dict, List
import json
from fastapi import HTTPException

def _p4_route_exists(_path: str, _method: str = "GET") -> bool:
    _app = globals().get("app")
    if _app is None:
        return False
    m = _method.upper()
    for r in getattr(_app, "routes", []):
        if getattr(r, "path", None) == _path and m in (getattr(r, "methods", set()) or set()):
            return True
    return False

def _p4_root() -> Path:
    return Path(__file__).resolve().parents[1]

def _p4_candidates() -> List[Path]:
    root = _p4_root()
    out: List[Path] = []
    patterns = [
        "canon/*.json",
        "books/*/bible/book_bible.json",
        "books/*/bible.json",
        "books/*/memory/book_bible.json",
        "books/*/book_bible.json",
    ]
    for pat in patterns:
        for p in root.glob(pat):
            if p.is_file():
                out.append(p)
    return out

def _p4_book_id_from_path(p: Path) -> str:
    parts = [x.lower() for x in p.parts]
    if "books" in parts:
        i = parts.index("books")
        if i + 1 < len(p.parts):
            return str(p.parts[i + 1])
    if p.parent.name.lower() == "canon":
        return str(p.stem)
    return str(p.stem)

def _p4_list_books(limit: int = 500) -> List[Dict[str, Any]]:
    root = _p4_root()
    seen: Dict[str, Dict[str, Any]] = {}
    for p in _p4_candidates():
        bid = _p4_book_id_from_path(p)
        if bid in seen:
            continue
        st = p.stat()
        rel = str(p.relative_to(root)).replace("\\", "/")
        seen[bid] = {
            "book_id": bid,
            "path": rel,
            "bytes": int(st.st_size),
            "modified": int(st.st_mtime),
        }
    items = list(seen.values())
    items.sort(key=lambda x: x["book_id"])
    return items[: int(limit)]

def _p4_read_json(p: Path) -> Dict[str, Any]:
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data
    return {"_type": type(data).__name__, "data": data}

def _p4_load_book(book_id: str) -> Dict[str, Any]:
    bid = str(book_id)
    items = _p4_list_books(limit=5000)
    match = next((x for x in items if str(x.get("book_id")) == bid), None)
    if not match:
        raise FileNotFoundError(f"CANON_NOT_FOUND:{bid}")
    src = _p4_root() / Path(match["path"])
    return {"book_id": bid, "source": str(src), "canon": _p4_read_json(src)}

def _p4_extract_characters(canon: Dict[str, Any]) -> List[Any]:
    if not isinstance(canon, dict):
        return []
    keys = ["characters", "cast", "persons", "people", "postacie", "bohaterowie"]
    for k in keys:
        v = canon.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            arr = []
            for kk, vv in v.items():
                if isinstance(vv, dict):
                    row = {"name": kk}
                    row.update(vv)
                    arr.append(row)
                else:
                    arr.append({"name": kk, "value": vv})
            return arr
    return []

if not _p4_route_exists("/canon", "GET"):
    @app.get("/canon")
    def p4_canon_list(limit: int = 200):
        items = _p4_list_books(limit=limit)
        return {"count": len(items), "items": items}

if not _p4_route_exists("/canon/get", "GET"):
    @app.get("/canon/get")
    def p4_canon_get(book_id: str):
        try:
            return _p4_load_book(book_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"CANON_READ_ERROR:{e}")

if not _p4_route_exists("/canon/{book_id}", "GET"):
    @app.get("/canon/{book_id}")
    def p4_canon_get_path(book_id: str):
        try:
            return _p4_load_book(book_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"CANON_READ_ERROR:{e}")

if not _p4_route_exists("/books/{book_id}/bible", "GET"):
    @app.get("/books/{book_id}/bible")
    def p4_book_bible(book_id: str):
        try:
            x = _p4_load_book(book_id)
            return {"book_id": x["book_id"], "source": x["source"], "bible": x["canon"]}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"BIBLE_READ_ERROR:{e}")

if not _p4_route_exists("/books/{book_id}/bible/characters", "GET"):
    @app.get("/books/{book_id}/bible/characters")
    def p4_book_bible_characters(book_id: str):
        try:
            x = _p4_load_book(book_id)
            chars = _p4_extract_characters(x["canon"])
            return {"book_id": x["book_id"], "source": x["source"], "count": len(chars), "characters": chars}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"CHAR_READ_ERROR:{e}")
# === /P4_CANON_API_READONLY_PATCH_V2 ===


# === P6_PRESETS_API_RESTORE_PATCH ===
# === P6_PRESETS_CANONICAL_HELPER ===
def _p6_presets_payload():
    try:
        pd = load_presets()
        if isinstance(pd, dict):
            presets = pd.get("presets") if isinstance(pd.get("presets"), list) else []
            preset_ids = pd.get("preset_ids") if isinstance(pd.get("preset_ids"), list) else [
                str(x.get("id")) for x in presets if isinstance(x, dict) and x.get("id")
            ]
            presets_count = int(pd.get("presets_count", len(presets)))
        elif isinstance(pd, list):
            presets = [x for x in pd if isinstance(x, dict)]
            preset_ids = [str(x.get("id")) for x in presets if x.get("id")]
            presets_count = len(presets)
        else:
            presets, preset_ids, presets_count = [], [], 0

        return {
            "status": "ok",
            "source": "config_registry",
            "count": presets_count,
            "presets_count": presets_count,
            "preset_ids": preset_ids,
            "presets": presets
        }
    except Exception as e:
        return {
            "status": "error",
            "source": "config_registry",
            "count": 0,
            "presets_count": 0,
            "preset_ids": [],
            "presets": [],
            "error": str(e)
        }
# === /P6_PRESETS_CANONICAL_HELPER ===

# === P6_CONFIG_PRESETS_ENDPOINT_FIX ===
def _config_presets_legacy_payload():
    return _p6_presets_payload()
# === /P6_CONFIG_PRESETS_ENDPOINT_FIX ===


# === P6_FINAL_CONFIG_PRESETS_ROUTE ===
@app.get("/config/presets")
def config_presets():
    return _p6_presets_payload()
# === /P6_FINAL_CONFIG_PRESETS_ROUTE ===

# === P6_FINAL_CONFIG_PRESETS_ROUTE ===

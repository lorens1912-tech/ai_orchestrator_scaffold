from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.canon_check import canon_check

APP_VERSION = "P20.0-novel-core"
REPO_ROOT = Path(__file__).resolve().parents[2]
BOOKS_ROOT = REPO_ROOT / "books"
RUNS_ROOT = REPO_ROOT / "runs"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def sha256_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_load(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def json_write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def ensure_book_dirs(book_id: str) -> Path:
    book_dir = BOOKS_ROOT / str(book_id)
    (book_dir / "memory").mkdir(parents=True, exist_ok=True)
    (book_dir / "artifacts" / "canon").mkdir(parents=True, exist_ok=True)
    (book_dir / "chapters").mkdir(parents=True, exist_ok=True)
    (book_dir / "audit").mkdir(parents=True, exist_ok=True)

    book_bible = book_dir / "book_bible.json"
    if not book_bible.exists():
        json_write(book_bible, {})

    canon_json = book_dir / "memory" / "canon.json"
    if not canon_json.exists():
        json_write(canon_json, {"timeline": [], "decisions": {}, "facts": {}, "approved_chapters": []})

    return book_dir


def ensure_run_dirs(run_id: str) -> Path:
    run_dir = RUNS_ROOT / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def load_run_state(run_id: str) -> Dict[str, Any]:
    return json_load(ensure_run_dirs(run_id) / "run_state.json", {})


def save_run_state(
    run_id: str,
    *,
    book_id: str,
    status: str,
    last_modes: List[str],
    last_artifact_paths: List[str],
    chapter_path: Optional[str],
) -> Dict[str, Any]:
    run_dir = ensure_run_dirs(run_id)
    state = {
        "run_id": run_id,
        "book_id": book_id,
        "status": status,
        "last_modes": list(last_modes or []),
        "last_artifact_paths": list(last_artifact_paths or []),
        "chapter_path": chapter_path,
        "updated_at": utc_now_iso(),
        "engine": APP_VERSION,
    }
    json_write(run_dir / "run_state.json", state)
    return state


def resolve_resume_run_id(book_id: str, payload_run_id: Optional[str], resume: bool) -> str:
    explicit = str(payload_run_id or "").strip()
    if explicit:
        return explicit

    book_dir = ensure_book_dirs(book_id)
    runs_dir = REPO_ROOT / "runs"

    def _new_run_id_local() -> str:
        from datetime import datetime, timezone
        from uuid import uuid4
        return f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"

    def _state_for(run_dir: Path) -> Dict[str, Any]:
        state = json_load(run_dir / "run_state.json", {})
        if isinstance(state, dict) and state:
            return state
        state = json_load(run_dir / "state.json", {})
        return state if isinstance(state, dict) else {}

    def _same_book_existing(candidate: str) -> Optional[str]:
        rid = str(candidate or "").strip()
        if not rid:
            return None
        run_dir = runs_dir / rid
        if not run_dir.exists():
            return None
        state = _state_for(run_dir)
        if str(state.get("book_id") or "") != str(book_id):
            return None
        return rid

    if not resume:
        return _new_run_id_local()

    marker = book_dir / "audit" / "latest_run_id.txt"
    if marker.exists():
        marker_rid = str(marker.read_text(encoding="utf-8") or "").strip()
        resolved = _same_book_existing(marker_rid)
        if resolved:
            return resolved

    candidates: list[tuple[str, str]] = []
    if runs_dir.exists():
        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            state = _state_for(run_dir)
            if str(state.get("book_id") or "") != str(book_id):
                continue
            rid = str(state.get("run_id") or run_dir.name)
            updated = str(state.get("updated_at") or state.get("created_at") or "")
            candidates.append((updated, rid))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    return _new_run_id_local()

def update_latest_run_marker(book_id: str, run_id: str) -> None:
    book_dir = ensure_book_dirs(book_id)
    marker = book_dir / "audit" / "latest_run_id.txt"
    marker.write_text(str(run_id), encoding="utf-8")


def merge_dicts(a: Any, b: Any) -> Any:
    if not isinstance(a, dict):
        a = {}
    if not isinstance(b, dict):
        b = {}
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_dicts(out[k], v)
        else:
            out[k] = v
    return out


def _chapter_sort_key(path: Path) -> Tuple[int, str]:
    stem = path.stem
    try:
        idx = int(stem.split("_")[-1])
    except Exception:
        idx = 0
    return idx, path.name


def rebuild_canon_from_chapters(book_id: str) -> Dict[str, Any]:
    book_dir = ensure_book_dirs(book_id)
    canon_path = book_dir / "memory" / "canon.json"
    canon = json_load(canon_path, {"timeline": [], "decisions": {}, "facts": {}, "approved_chapters": []})
    if not isinstance(canon, dict):
        canon = {"timeline": [], "decisions": {}, "facts": {}, "approved_chapters": []}

    chapters_dir = book_dir / "chapters"
    chapter_files = sorted(chapters_dir.glob("chapter_*.json"), key=_chapter_sort_key)

    approved: List[Dict[str, Any]] = []
    last_accepted: Optional[Dict[str, Any]] = None

    for path in chapter_files:
        data = json_load(path, {})
        if not isinstance(data, dict):
            continue

        chapter_id = str(data.get("chapter_id") or path.stem)
        chapter_rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        run_id = str(data.get("run_id") or "")
        sha256 = str(data.get("sha256") or "")
        accepted_at = str(data.get("created_at") or utc_now_iso())

        item = {
            "chapter_id": chapter_id,
            "chapter_path": chapter_rel,
            "run_id": run_id,
            "sha256": sha256,
            "accepted_at": accepted_at,
        }
        approved.append(item)
        last_accepted = {
            "chapter_id": chapter_id,
            "chapter_path": chapter_rel,
            "run_id": run_id,
            "sha256": sha256,
        }

    canon["approved_chapters"] = approved

    if last_accepted is not None:
        canon["last_accepted_chapter"] = last_accepted
        canon["chapter_count"] = len(approved)
    else:
        canon.pop("last_accepted_chapter", None)
        canon.pop("chapter_count", None)

    canon["updated_at"] = utc_now_iso()
    canon["engine"] = APP_VERSION

    json_write(canon_path, canon)
    return canon


def load_canon_snapshot(book_id: str) -> Tuple[Dict[str, Any], Path]:
    book_dir = ensure_book_dirs(book_id)
    book_bible = json_load(book_dir / "book_bible.json", {})
    canon_memory = rebuild_canon_from_chapters(book_id)

    snapshot = merge_dicts(book_bible, canon_memory)
    snapshot["_meta"] = {
        "book_id": book_id,
        "generated_at": utc_now_iso(),
        "engine": APP_VERSION,
    }

    snapshot_path = book_dir / "artifacts" / "canon" / f"canon_snapshot_{utc_stamp()}.json"
    json_write(snapshot_path, snapshot)
    return snapshot, snapshot_path


def commit_chapter_to_canon(
    *,
    book_id: str,
    run_id: str,
    chapter_path: str,
    chapter_id: str,
    chapter_sha256: str,
) -> Dict[str, Any]:
    book_dir = ensure_book_dirs(book_id)
    canon_path = book_dir / "memory" / "canon.json"
    canon = json_load(canon_path, {"timeline": [], "decisions": {}, "facts": {}, "approved_chapters": []})
    if not isinstance(canon, dict):
        canon = {"timeline": [], "decisions": {}, "facts": {}, "approved_chapters": []}

    approved = canon.get("approved_chapters")
    if not isinstance(approved, list):
        approved = []

    already = False
    for item in approved:
        if isinstance(item, dict) and item.get("chapter_id") == chapter_id:
            already = True
            break

    if not already:
        approved.append(
            {
                "chapter_id": chapter_id,
                "chapter_path": chapter_path,
                "run_id": run_id,
                "sha256": chapter_sha256,
                "accepted_at": utc_now_iso(),
            }
        )

    canon["approved_chapters"] = approved
    canon["last_accepted_chapter"] = {
        "chapter_id": chapter_id,
        "chapter_path": chapter_path,
        "run_id": run_id,
        "sha256": chapter_sha256,
    }
    canon["chapter_count"] = len(approved)
    canon["updated_at"] = utc_now_iso()
    canon["engine"] = APP_VERSION

    json_write(canon_path, canon)
    return rebuild_canon_from_chapters(book_id)


def coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "body", "input", "topic"):
            if key in value and isinstance(value[key], str):
                return value[key]
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    if isinstance(value, list):
        return "\n".join(coerce_text(x) for x in value)
    return str(value)


def read_artifact_text(path_value: str) -> str:
    p = Path(path_value)
    if not p.is_absolute():
        p = REPO_ROOT / p
    if not p.exists():
        return ""

    data = json_load(p, {})
    if not isinstance(data, dict):
        return ""

    candidates = [
        data.get("content"),
        data.get("text"),
        ((data.get("result") or {}).get("content") if isinstance(data.get("result"), dict) else None),
        (((data.get("result") or {}).get("payload") or {}).get("content") if isinstance((data.get("result") or {}).get("payload"), dict) else None),
        (((data.get("result") or {}).get("payload") or {}).get("text") if isinstance((data.get("result") or {}).get("payload"), dict) else None),
        ((data.get("payload") or {}).get("content") if isinstance(data.get("payload"), dict) else None),
        ((data.get("payload") or {}).get("text") if isinstance(data.get("payload"), dict) else None),
    ]

    for candidate in candidates:
        txt = coerce_text(candidate).strip()
        if txt:
            return txt

    return ""


def run_canon_check(text: str, canon: Dict[str, Any], scene_ref: Optional[str]) -> Dict[str, Any]:
    if not str(text or "").strip():
        return {"ok": True, "status": "skipped", "reason": "empty_text"}

    try:
        result = canon_check(text, canon, scene_ref)
    except TypeError:
        try:
            result = canon_check(text=text, canon=canon, scene_ref=scene_ref)
        except Exception as e:
            return {"ok": False, "status": "error", "detail": f"canon_check_failed: {e}"}
    except Exception as e:
        return {"ok": False, "status": "error", "detail": f"canon_check_failed: {e}"}

    if isinstance(result, dict):
        return result

    return {"ok": True, "status": "unknown_format", "raw": result}


def canon_blocks(report: Dict[str, Any]) -> bool:
    if not isinstance(report, dict):
        return True

    if report.get("status") == "skipped":
        return False

    for key in ("ok", "passed", "valid"):
        if key in report and report.get(key) is False:
            return True

    for key in ("errors", "violations", "issues", "must_fix", "blocking_issues", "mismatches"):
        value = report.get(key)
        if isinstance(value, list) and len(value) > 0:
            return True
        if isinstance(value, dict) and len(value) > 0:
            return True
        if isinstance(value, int) and value > 0:
            return True
        if isinstance(value, str) and value.strip():
            return True

    return False


def next_chapter_path(book_dir: Path) -> Path:
    chapters_dir = book_dir / "chapters"
    existing = sorted(chapters_dir.glob("chapter_*.json"), key=_chapter_sort_key)
    max_idx = 0
    for p in existing:
        try:
            max_idx = max(max_idx, int(p.stem.split("_")[-1]))
        except Exception:
            pass
    return chapters_dir / f"chapter_{max_idx + 1:03d}.json"


def save_chapter(
    book_id: str,
    run_id: str,
    text: str,
    source_artifact: Optional[str],
    canon_snapshot_path: Path,
    pre_report: Dict[str, Any],
    post_report: Dict[str, Any],
) -> Optional[str]:
    if not str(text or "").strip():
        return None

    book_dir = ensure_book_dirs(book_id)
    chapter_path = next_chapter_path(book_dir)
    chapter_id = chapter_path.stem

    doc = {
        "chapter_id": chapter_id,
        "book_id": book_id,
        "run_id": run_id,
        "created_at": utc_now_iso(),
        "engine": APP_VERSION,
        "source_artifact": source_artifact,
        "canon_snapshot_path": str(canon_snapshot_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "sha256": sha256_text(text),
        "content": text,
        "text": text,
        "pre_canon_check": pre_report,
        "post_canon_check": post_report,
    }
    json_write(chapter_path, doc)
    return str(chapter_path.relative_to(REPO_ROOT)).replace("\\", "/")


def write_audit(
    book_id: str,
    run_id: str,
    modes: List[str],
    artifact_paths: List[str],
    decision: str,
    chapter_path: Optional[str],
    canon_snapshot_path: Path,
    master_canon: Optional[Dict[str, Any]] = None,
    project_truth: Optional[Dict[str, Any]] = None,
) -> None:
    book_dir = ensure_book_dirs(book_id)
    audit_doc = {
        "ts": utc_now_iso(),
        "book_id": book_id,
        "run_id": run_id,
        "modes": modes,
        "artifact_paths": artifact_paths,
        "decision": decision,
        "chapter_path": chapter_path,
        "canon_snapshot_path": str(canon_snapshot_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "master_canon": master_canon,
        "project_truth": project_truth,
        "engine": APP_VERSION,
    }

    append_jsonl(
        book_dir / "audit" / "audit_log.jsonl",
        audit_doc,
    )

    run_dir = REPO_ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    json_write(run_dir / "audit.json", audit_doc)

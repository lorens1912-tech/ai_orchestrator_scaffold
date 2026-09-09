from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.canon_service import json_write
from app.p20_core.project_truth import build_project_truth_binding


REPO_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def _book_dir(book_id: str) -> Path:
    return REPO_ROOT / "books" / book_id


def _run_dir(run_id: str) -> Path:
    return REPO_ROOT / "runs" / run_id


def _clean_book(book_id: str) -> None:
    p = _book_dir(book_id)
    if p.exists():
        shutil.rmtree(p)


def _clean_run(run_id: str) -> None:
    p = _run_dir(run_id)
    if p.exists():
        shutil.rmtree(p)


def _make_book(book_id: str) -> None:
    book_dir = _book_dir(book_id)
    (book_dir / "chapters").mkdir(parents=True, exist_ok=True)
    (book_dir / "memory").mkdir(parents=True, exist_ok=True)

    json_write(book_dir / "memory" / "canon.json", {
        "timeline": [],
        "decisions": {},
        "facts": {},
        "approved_chapters": [],
    })

    json_write(book_dir / "chapters" / "chapter_001.json", {
        "chapter_id": "chapter_001",
        "book_id": book_id,
        "run_id": "run_prev_1",
        "created_at": "2026-03-24T10:00:00+00:00",
        "sha256": "sha001",
        "content": "A",
        "text": "A",
    })


def test_canon_rebuild_resume_reuses_latest_run_id_and_persists_runtime_style_state():
    book_id = f"p20_rebuild_resume_{uuid4().hex[:8]}"
    run_id = f"run_rebuild_resume_{uuid4().hex[:8]}"
    try:
        _make_book(book_id)

        r1 = client.post("/canon/rebuild", json={
            "book_id": book_id,
            "run_id": run_id,
        })
        r2 = client.post("/canon/rebuild", json={
            "book_id": book_id,
            "resume": True,
        })

        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text

        d1 = r1.json()
        d2 = r2.json()

        assert d1["run_id"] == run_id, d1
        assert d2["run_id"] == run_id, d2

        marker = _book_dir(book_id) / "audit" / "latest_run_id.txt"
        assert marker.exists(), marker
        assert marker.read_text(encoding="utf-8").strip() == run_id

        run_state_path = _run_dir(run_id) / "run_state.json"
        assert run_state_path.exists(), run_state_path

        state = json.loads(run_state_path.read_text(encoding="utf-8"))
        assert state["run_id"] == run_id, state
        assert state["book_id"] == book_id, state
        assert state["status"] == "ACCEPT", state
        assert state["decision"] == "ACCEPT", state
        assert state["last_modes"] == ["CANON_REBUILD"], state
        assert state["project_truth"] == build_project_truth_binding(), state
        assert state["audit_path"] == d2["audit_path"], state
        assert state["rebuild_artifact_path"] == d2["rebuild_artifact_path"], state
        assert state["canon_snapshot_path"] == d2["canon_snapshot_path"], state
    finally:
        _clean_book(book_id)
        _clean_run(run_id)


def test_canon_rebuild_source_contains_resume_lock_and_audit_parity_hooks():
    source = Path("app/p20_core/canon_rebuild.py").read_text(encoding="utf-8")

    assert "resolve_resume_run_id" in source
    assert "load_run_state" in source
    assert "assert_resume_project_truth_consistency" in source
    assert "acquire_book_lock" in source
    assert "acquire_run_lock" in source
    assert "save_run_state(" in source
    assert "update_latest_run_marker(" in source
    assert "write_audit(" in source
    assert "run_state.json" in source

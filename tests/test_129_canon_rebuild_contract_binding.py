from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.canon_service import json_write
from app.p20_core.master_canon import resolve_master_canon
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

    json_write(book_dir / "chapters" / "chapter_002.json", {
        "chapter_id": "chapter_002",
        "book_id": book_id,
        "run_id": "run_prev_2",
        "created_at": "2026-03-24T11:00:00+00:00",
        "sha256": "sha002",
        "content": "B",
        "text": "B",
    })


def test_canon_rebuild_returns_full_contract_and_bindings():
    book_id = f"p20_rebuild_contract_{uuid4().hex[:8]}"
    try:
        _make_book(book_id)

        resp = client.post("/canon/rebuild", json={"book_id": book_id})
        assert resp.status_code == 200, resp.text
        data = resp.json()

        required = {
            "ok",
            "status",
            "run_id",
            "book_id",
            "decision",
            "chapter_count",
            "master_canon",
            "project_truth",
            "rebuild_artifact_path",
            "canon_snapshot_path",
            "audit_path",
            "rebuild_summary",
            "violations",
            "run_state",
            "canon_memory",
        }
        assert required.issubset(data.keys()), data
        assert data["ok"] is True, data
        assert data["status"] == "ok", data
        assert data["decision"] == "ACCEPT", data
        assert data["chapter_count"] == 2, data
        assert data["master_canon"] == resolve_master_canon(), data
        assert data["project_truth"] == build_project_truth_binding(), data

        audit_path = REPO_ROOT / data["audit_path"]
        snapshot_path = REPO_ROOT / data["canon_snapshot_path"]
        rebuild_path = REPO_ROOT / data["rebuild_artifact_path"]

        assert audit_path.exists(), audit_path
        assert snapshot_path.exists(), snapshot_path
        assert rebuild_path.exists(), rebuild_path

        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

        assert audit["master_canon"] == data["master_canon"], audit
        assert audit["project_truth"] == data["project_truth"], audit
        assert audit["decision"] == "ACCEPT", audit

        assert snapshot["master_canon"] == data["master_canon"], snapshot
        assert snapshot["project_truth"] == data["project_truth"], snapshot
        assert snapshot["decision"] == "ACCEPT", snapshot
    finally:
        _clean_book(book_id)


def test_canon_rebuild_rejects_project_truth_mismatch_without_promoting_live_canon():
    book_id = f"p20_rebuild_reject_{uuid4().hex[:8]}"
    try:
        _make_book(book_id)
        canon_path = _book_dir(book_id) / "memory" / "canon.json"
        before = canon_path.read_text(encoding="utf-8")

        resp = client.post("/canon/rebuild", json={
            "book_id": book_id,
            "project_truth": "MISMATCH_SHA256",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert data["ok"] is False, data
        assert data["status"] == "reject", data
        assert data["decision"] == "REJECT", data
        assert "project_truth_sha256_mismatch" in data["violations"], data

        after = canon_path.read_text(encoding="utf-8")
        assert after == before
    finally:
        _clean_book(book_id)


def test_canon_rebuild_is_deterministic_for_same_input():
    book_id = f"p20_rebuild_determinism_{uuid4().hex[:8]}"
    run_ids: list[str] = []
    try:
        _make_book(book_id)

        r1 = client.post("/canon/rebuild", json={"book_id": book_id})
        r2 = client.post("/canon/rebuild", json={"book_id": book_id})

        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text

        d1 = r1.json()
        d2 = r2.json()

        run_ids.append(d1["run_id"])
        run_ids.append(d2["run_id"])

        assert d1["decision"] == d2["decision"], (d1, d2)
        assert d1["master_canon"] == d2["master_canon"], (d1, d2)
        assert d1["project_truth"] == d2["project_truth"], (d1, d2)
        assert d1["chapter_count"] == d2["chapter_count"], (d1, d2)
        assert d1["rebuild_summary"] == d2["rebuild_summary"], (d1, d2)
        assert d1["canon_memory"]["approved_chapters"] == d2["canon_memory"]["approved_chapters"], (d1, d2)
    finally:
        _clean_book(book_id)
        for run_id in run_ids:
            _clean_run(run_id)

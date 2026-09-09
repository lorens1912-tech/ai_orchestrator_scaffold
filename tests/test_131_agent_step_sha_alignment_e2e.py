from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.canon_service import json_write
from app.p20_core.book_bible_test_helper import ensure_test_book_bible


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


def test_agent_step_master_canon_sha_matches_project_truth_everywhere():
    book_id = f"p20_agent_sha_align_{uuid4().hex[:8]}"
    ensure_test_book_bible(book_id)
    run_id = f"run_agent_sha_align_{uuid4().hex[:8]}"

    try:
        book_dir = _book_dir(book_id)
        (book_dir / "chapters").mkdir(parents=True, exist_ok=True)
        (book_dir / "memory").mkdir(parents=True, exist_ok=True)

        json_write(book_dir / "memory" / "canon.json", {
            "timeline": [],
            "decisions": {},
            "facts": {},
            "approved_chapters": [],
        })

        with patch(
            "app.p20_core.runtime.execute_stub",
            return_value={"artifact_paths": ["runs/fake_sha_align/001_WRITE.json"]},
        ), patch(
            "app.p20_core.runtime.read_artifact_text",
            return_value="Scena testowa dla wyrównania SHA.",
        ), patch(
            "app.p20_core.runtime.run_canon_check",
            side_effect=[
                {"ok": True, "issues": [], "scene_ref": ""},
                {"ok": True, "issues": [], "scene_ref": ""},
            ],
        ):
            resp = client.post("/agent/step", json={
                "mode": "WRITE",
                "payload": {
                    "book_id": book_id,
                    "run_id": run_id,
                    "text": "Scena wejściowa.",
                },
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert "master_canon" in data, data
        assert "project_truth" in data, data

        response_master_sha = data["master_canon"]["sha256"]
        response_truth_sha = data["project_truth"]["sha256"]
        assert response_master_sha == response_truth_sha

        chapter_path = REPO_ROOT / data["chapter_path"]
        audit_path = REPO_ROOT / "runs" / run_id / "audit.json"
        snapshot_path = REPO_ROOT / data["canon_snapshot_path"]

        chapter = json.loads(chapter_path.read_text(encoding="utf-8"))
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

        assert "master_canon" in chapter, chapter
        assert "project_truth" in chapter, chapter
        assert "master_canon" in audit, audit
        assert "project_truth" in audit, audit
        assert "project_truth" in snapshot, snapshot

        if "master_canon" in snapshot:
            snapshot_master_sha = snapshot["master_canon"]["sha256"]
        else:
            snapshot_master_sha = response_master_sha

        chapter_master_sha = chapter["master_canon"]["sha256"]
        chapter_truth_sha = chapter["project_truth"]["sha256"]
        audit_master_sha = audit["master_canon"]["sha256"]
        audit_truth_sha = audit["project_truth"]["sha256"]
        snapshot_truth_sha = snapshot["project_truth"]["sha256"]

        assert chapter_master_sha == chapter_truth_sha
        assert audit_master_sha == audit_truth_sha
        assert snapshot_master_sha == snapshot_truth_sha

        assert chapter_master_sha == response_master_sha
        assert audit_master_sha == response_master_sha
        assert snapshot_master_sha == response_master_sha
    finally:
        _clean_book(book_id)
        _clean_run(run_id)

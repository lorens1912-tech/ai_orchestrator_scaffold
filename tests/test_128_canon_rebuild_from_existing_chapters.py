from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.p20_core.canon_service import rebuild_canon_from_chapters, json_write
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


def test_rebuild_canon_from_existing_chapters():
    book_id = f"p20_rebuild_{uuid4().hex[:8]}"
    ensure_test_book_bible(book_id)
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

        json_write(book_dir / "chapters" / "chapter_001.json", {
            "chapter_id": "chapter_001",
            "book_id": book_id,
            "run_id": "run_a",
            "created_at": "2026-03-24T10:00:00+00:00",
            "sha256": "sha001",
            "content": "A",
            "text": "A",
        })

        json_write(book_dir / "chapters" / "chapter_002.json", {
            "chapter_id": "chapter_002",
            "book_id": book_id,
            "run_id": "run_b",
            "created_at": "2026-03-24T11:00:00+00:00",
            "sha256": "sha002",
            "content": "B",
            "text": "B",
        })

        canon = rebuild_canon_from_chapters(book_id)

        assert canon["chapter_count"] == 2
        assert len(canon["approved_chapters"]) == 2
        assert canon["last_accepted_chapter"]["chapter_id"] == "chapter_002"
    finally:
        _clean_book(book_id)


def test_accept_after_existing_chapters_returns_full_canon_memory():
    book_id = f"p20_rebuild_accept_{uuid4().hex[:8]}"
    ensure_test_book_bible(book_id)
    run_id = f"run_rebuild_accept_{uuid4().hex[:8]}"
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

        with patch(
            "app.p20_core.runtime.execute_stub",
            return_value={"artifact_paths": ["runs/fake_rebuild/001_WRITE.json"]},
        ), patch(
            "app.p20_core.runtime.read_artifact_text",
            return_value="Scena dla testu backfill + ACCEPT.",
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

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["chapter_path"].endswith("chapter_003.json")
        assert data["canon_memory"]["chapter_count"] == 3
        assert len(data["canon_memory"]["approved_chapters"]) == 3
        assert data["canon_memory"]["last_accepted_chapter"]["chapter_id"] == "chapter_003"
    finally:
        _clean_book(book_id)
        _clean_run(run_id)

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.p20_core.book_bible_contract import load_book_bible_or_raise
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.canon_service import (
    ensure_book_dirs,
    ensure_run_dirs,
    load_canon_snapshot,
    rebuild_canon_from_chapters,
    save_chapter,
    write_audit,
)
from app.p20_core.lock_service import (
    acquire_book_lock,
    acquire_run_lock,
    book_lock_path,
    release_book_lock,
    release_run_lock,
    run_lock_path,
)
from app.p20_core.storage_paths import get_books_root, get_runs_root


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_core_storage_consumers_use_configured_storage_root(tmp_path, monkeypatch) -> None:
    storage_root = (tmp_path / "agentpro").resolve()
    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(storage_root))

    book_id = "storage_test"
    lock_book_id = "storage_test_lock_book"
    bible_book_id = "storage_test_bible"
    run_id = "storage_test_run"

    real_repo_paths = [
        REPO_ROOT / "books" / book_id,
        REPO_ROOT / "books" / lock_book_id,
        REPO_ROOT / "books" / bible_book_id,
        REPO_ROOT / "runs" / run_id,
    ]
    assert all(not path.exists() for path in real_repo_paths)

    book_dir = ensure_book_dirs(book_id)

    assert get_books_root() == storage_root / "books"
    assert book_dir == storage_root / "books" / book_id
    assert (book_dir / "memory" / "canon.json").exists()
    assert (book_dir / "artifacts" / "canon").is_dir()
    assert (book_dir / "chapters").is_dir()
    assert (book_dir / "audit").is_dir()

    run_dir = ensure_run_dirs(run_id)

    assert get_runs_root() == storage_root / "runs"
    assert run_dir == storage_root / "runs" / run_id
    assert run_dir.is_dir()

    try:
        acquire_run_lock(run_id, lock_book_id)
        acquire_book_lock(lock_book_id, run_id)

        expected_run_lock = storage_root / "runs" / run_id / "run.lock.json"
        expected_book_lock = storage_root / "books" / lock_book_id / "audit" / "book.lock.json"
        assert run_lock_path(run_id) == expected_run_lock
        assert book_lock_path(lock_book_id) == expected_book_lock
        assert expected_run_lock.exists()
        assert expected_book_lock.exists()
    finally:
        release_run_lock(run_id)
        release_book_lock(lock_book_id)

    helper_path = ensure_test_book_bible(bible_book_id)

    assert helper_path == storage_root / "books" / bible_book_id / "book_bible.json"
    assert helper_path.exists()

    loaded = load_book_bible_or_raise(bible_book_id)
    raw = helper_path.read_text(encoding="utf-8")

    assert loaded["book_id"] == bible_book_id
    assert loaded["_contract"]["path"] == f"books/{bible_book_id}/book_bible.json"
    assert loaded["_contract"]["sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()

    _, canon_snapshot_path = load_canon_snapshot(book_id)
    chapter_public_path = save_chapter(
        book_id=book_id,
        run_id=run_id,
        text="A storage isolated chapter body.",
        source_artifact=None,
        canon_snapshot_path=canon_snapshot_path,
        pre_report={"ok": True},
        post_report={"ok": True},
    )
    write_audit(
        book_id=book_id,
        run_id=run_id,
        modes=["WRITE"],
        artifact_paths=[chapter_public_path],
        decision="ACCEPT",
        chapter_path=chapter_public_path,
        canon_snapshot_path=canon_snapshot_path,
    )
    rebuilt_canon = rebuild_canon_from_chapters(book_id)

    chapter_doc_path = storage_root / chapter_public_path
    chapter_doc = json.loads(chapter_doc_path.read_text(encoding="utf-8"))
    run_audit_doc = json.loads((storage_root / "runs" / run_id / "audit.json").read_text(encoding="utf-8"))
    public_paths = [
        chapter_public_path,
        chapter_doc["canon_snapshot_path"],
        run_audit_doc["chapter_path"],
        run_audit_doc["canon_snapshot_path"],
        rebuilt_canon["approved_chapters"][0]["chapter_path"],
    ]

    for public_path in public_paths:
        assert public_path.startswith(("books/", "runs/"))
        assert not Path(public_path).is_absolute()
        assert str(tmp_path).replace("\\", "/") not in public_path
        assert str(storage_root).replace("\\", "/") not in public_path

    assert all(not path.exists() for path in real_repo_paths)

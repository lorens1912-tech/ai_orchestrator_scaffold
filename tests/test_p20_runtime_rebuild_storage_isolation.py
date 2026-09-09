from __future__ import annotations

import json
import threading
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

import app.orchestrator_stub as orchestrator_stub
from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core import runtime


REPO_ROOT = Path(__file__).resolve().parents[1]


def _assert_public_storage_path(path_value: str, *, storage_root: Path, tmp_path: Path) -> None:
    assert path_value.startswith(("books/", "runs/"))
    assert not Path(path_value).is_absolute()
    assert str(tmp_path).replace("\\", "/") not in path_value
    assert str(storage_root).replace("\\", "/") not in path_value


def test_agent_step_write_and_canon_rebuild_use_configured_storage_root(tmp_path, monkeypatch) -> None:
    storage_root = (tmp_path / "agentpro").resolve()
    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(storage_root))

    book_id = f"storage_runtime_{uuid4().hex}"
    run_id = f"storage_runtime_run_{uuid4().hex}"
    rebuild_run_id = f"storage_rebuild_run_{uuid4().hex}"

    real_repo_paths = [
        REPO_ROOT / "books" / book_id,
        REPO_ROOT / "runs" / run_id,
        REPO_ROOT / "runs" / rebuild_run_id,
    ]
    assert all(not path.exists() for path in real_repo_paths)

    book_bible_path = ensure_test_book_bible(book_id)
    assert book_bible_path == storage_root / "books" / book_id / "book_bible.json"

    client = TestClient(app)
    write_response = client.post(
        "/agent/step",
        json={
            "mode": "WRITE",
            "payload": {
                "book_id": book_id,
                "run_id": run_id,
                "text": (
                    "Storage isolation scene. The protagonist follows a coded signal through "
                    "the archive, confirms the witness account, and records the discovery for "
                    "the next chapter without touching the production repository storage."
                ),
            },
        },
    )

    assert write_response.status_code == 200, write_response.text
    write_data = write_response.json()
    assert write_data["ok"] is True, write_data

    write_run_root = storage_root / "runs" / run_id
    write_book_root = storage_root / "books" / book_id
    assert (write_run_root / "run_state.json").exists()
    assert (write_run_root / "audit.json").exists()
    assert (write_book_root / "audit" / "audit_log.jsonl").exists()
    assert (write_book_root / "book_bible.json").exists()

    write_public_paths = [
        write_data["chapter_path"],
        write_data["canon_snapshot_path"],
        *write_data["artifact_paths"],
    ]
    for public_path in write_public_paths:
        _assert_public_storage_path(public_path, storage_root=storage_root, tmp_path=tmp_path)
        assert (storage_root / public_path).exists()

    write_run_state = json.loads((write_run_root / "run_state.json").read_text(encoding="utf-8"))
    assert write_run_state["book_id"] == book_id
    assert write_run_state["chapter_path"] == write_data["chapter_path"]

    assert all(not path.exists() for path in real_repo_paths)

    rebuild_response = client.post(
        "/canon/rebuild",
        json={
            "book_id": book_id,
            "run_id": rebuild_run_id,
        },
    )

    assert rebuild_response.status_code == 200, rebuild_response.text
    rebuild_data = rebuild_response.json()
    assert rebuild_data["ok"] is True, rebuild_data
    assert rebuild_data["chapter_count"] >= 1, rebuild_data

    rebuild_public_paths = [
        rebuild_data["rebuild_artifact_path"],
        rebuild_data["canon_snapshot_path"],
        rebuild_data["audit_path"],
    ]
    for public_path in rebuild_public_paths:
        _assert_public_storage_path(public_path, storage_root=storage_root, tmp_path=tmp_path)
        assert (storage_root / public_path).exists()

    rebuild_artifact = json.loads(
        (storage_root / rebuild_data["rebuild_artifact_path"]).read_text(encoding="utf-8")
    )
    assert rebuild_artifact["input_sources"]["book_memory_canon"].startswith("books/")
    assert rebuild_artifact["input_sources"]["chapters_dir"].startswith("books/")

    rebuild_run_root = storage_root / "runs" / rebuild_run_id
    assert (rebuild_run_root / "run_state.json").exists()
    assert (rebuild_run_root / "audit.json").exists()
    assert all(not path.exists() for path in real_repo_paths)


def test_execute_stub_storage_scope_serializes_process_global_state(tmp_path, monkeypatch) -> None:
    storage_root = (tmp_path / "agentpro_scope").resolve()
    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(storage_root))

    original_cwd = Path.cwd()
    original_stub_root = orchestrator_stub.ROOT
    first_entered = threading.Event()
    release_first = threading.Event()
    second_attempting = threading.Event()
    second_entered = threading.Event()
    errors: list[BaseException] = []

    def worker_first() -> None:
        try:
            with runtime._execute_stub_storage_scope():
                first_entered.set()
                assert Path.cwd() == storage_root
                assert orchestrator_stub.ROOT == storage_root
                assert release_first.wait(timeout=5)
        except BaseException as exc:
            errors.append(exc)

    def worker_second() -> None:
        try:
            assert first_entered.wait(timeout=5)
            second_attempting.set()
            with runtime._execute_stub_storage_scope():
                second_entered.set()
                assert Path.cwd() == storage_root
                assert orchestrator_stub.ROOT == storage_root
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=worker_first)
    second = threading.Thread(target=worker_second)
    first.start()
    assert first_entered.wait(timeout=5)

    second.start()
    assert second_attempting.wait(timeout=5)
    assert not second_entered.wait(timeout=0.2)

    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert Path.cwd() == original_cwd
    assert orchestrator_stub.ROOT == original_stub_root
    assert not (REPO_ROOT / "books" / "agentpro_scope").exists()
    assert not (REPO_ROOT / "runs" / "agentpro_scope").exists()
    assert storage_root.exists()

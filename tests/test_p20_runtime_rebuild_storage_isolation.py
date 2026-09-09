from __future__ import annotations

import json
import threading
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

import app.orchestrator_stub as orchestrator_stub
from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible


REPO_ROOT = Path(__file__).resolve().parents[1]


def _assert_public_storage_path(path_value: str, *, storage_root: Path, tmp_path: Path) -> None:
    assert path_value.startswith(("books/", "runs/"))
    assert not Path(path_value).is_absolute()
    assert str(tmp_path).replace("\\", "/") not in path_value
    assert str(storage_root).replace("\\", "/") not in path_value


def test_agent_step_write_and_canon_rebuild_use_configured_storage_root(tmp_path, monkeypatch) -> None:
    storage_root = (tmp_path / "agentpro").resolve()
    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(storage_root))
    original_cwd = Path.cwd()
    original_stub_root = orchestrator_stub.ROOT

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
    assert Path.cwd() == original_cwd
    assert orchestrator_stub.ROOT == original_stub_root

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
    assert Path.cwd() == original_cwd
    assert orchestrator_stub.ROOT == original_stub_root

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


def test_parallel_agent_step_calls_use_configured_storage_without_global_scope(tmp_path, monkeypatch) -> None:
    storage_root = (tmp_path / "agentpro_parallel").resolve()
    monkeypatch.setenv("AGENTPRO_STORAGE_ROOT", str(storage_root))

    original_cwd = Path.cwd()
    original_stub_root = orchestrator_stub.ROOT
    cases = [
        (
            f"storage_parallel_book_a_{uuid4().hex}",
            f"storage_parallel_run_a_{uuid4().hex}",
        ),
        (
            f"storage_parallel_book_b_{uuid4().hex}",
            f"storage_parallel_run_b_{uuid4().hex}",
        ),
    ]
    for book_id, run_id in cases:
        assert not (REPO_ROOT / "books" / book_id).exists()
        assert not (REPO_ROOT / "runs" / run_id).exists()
        ensure_test_book_bible(book_id)

    start = threading.Barrier(len(cases))
    results: list[dict[str, object]] = []
    errors: list[BaseException] = []

    def worker(book_id: str, run_id: str) -> None:
        try:
            start.wait(timeout=5)
            client = TestClient(app)
            response = client.post(
                "/agent/step",
                json={
                    "mode": "WRITE",
                    "payload": {
                        "book_id": book_id,
                        "run_id": run_id,
                        "text": (
                            "Parallel storage isolation scene. The team writes a clean chapter "
                            "while preserving the configured storage root."
                        ),
                    },
                },
            )
            results.append({"book_id": book_id, "run_id": run_id, "response": response})
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=case) for case in cases]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == len(cases)
    assert Path.cwd() == original_cwd
    assert orchestrator_stub.ROOT == original_stub_root

    by_run_id = {str(result["run_id"]): result for result in results}
    for book_id, run_id in cases:
        response = by_run_id[run_id]["response"]
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is True, data

        public_paths = [
            data["chapter_path"],
            data["canon_snapshot_path"],
            *data["artifact_paths"],
        ]
        for public_path in public_paths:
            _assert_public_storage_path(public_path, storage_root=storage_root, tmp_path=tmp_path)
            assert (storage_root / public_path).exists()

        assert (storage_root / "books" / book_id / "book_bible.json").exists()
        assert (storage_root / "runs" / run_id / "run_state.json").exists()
        assert (storage_root / "runs" / run_id / "audit.json").exists()
        assert not (REPO_ROOT / "books" / book_id).exists()
        assert not (REPO_ROOT / "runs" / run_id).exists()

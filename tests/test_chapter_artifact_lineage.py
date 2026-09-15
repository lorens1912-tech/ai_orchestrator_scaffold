from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.p20_core import executor
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.cross_store_recovery import (
    CrossStoreRecoveryError,
    CrossStoreRecoveryService,
    RecoveryStatus,
)
from app.p20_core.project_repository import ProjectRepository, StorageResolver
from app.p20_core.storage_paths import get_storage_root


def _read_public_json(path_value: str) -> dict:
    path = get_storage_root() / path_value
    return json.loads(path.read_text(encoding="utf-8"))


def _long_neutral_scene() -> str:
    paragraph = (
        "A neutral test character entered the quiet archive, checked the numbered "
        "shelves, and compared the sealed inventory with the lamp-lit register. "
        "Rain tapped against the high windows while the clock marked each careful "
        "observation. The character chose a precise route, recorded one discrepancy, "
        "and left a clear note for the next synthetic operation."
    )
    return "\n\n".join(paragraph for _ in range(6))


def test_api_p20_persists_complete_chapter_lineage_retry_and_isolation(
    isolated_agentpro_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del isolated_agentpro_storage
    storage_book_id = "TEST_BOOK_F005_LINEAGE"
    requested_project_id = "PROJ-f005-lineage"
    run_id = "run-f005-lineage"
    root_step_id = "step-f005-lineage"
    ensure_test_book_bible(storage_book_id)

    def controlled_writer_boundary(payload: dict) -> dict:
        return {
            "tool": "WRITE",
            "payload": {
                "text": payload["input"],
                "meta": {"provider_family": "controlled-test-boundary"},
            },
        }

    monkeypatch.setitem(executor.TOOLS, "WRITE", controlled_writer_boundary)
    request = {
        "book_id": storage_book_id,
        "project_id": requested_project_id,
        "run_id": run_id,
        "step_id": root_step_id,
        "modes": ["WRITE", "CRITIC", "REWRITE", "EDIT", "QUALITY"],
        "payload": {
            "input": _long_neutral_scene(),
            "style_version": "STYLE-f005-v1",
        },
    }

    response = TestClient(app).post("/agent/step", json=request)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True, data
    assert data["quality_gate"]["decision"] == "ACCEPT", data
    lineage_summary = data["chapter_lineage"]
    assert lineage_summary["recovery_status"] == RecoveryStatus.COMMITTED.value
    chapter_path = data["chapter_path"]
    chapter = _read_public_json(chapter_path)
    assert chapter["schema_version"] == 2
    assert chapter["lineage_version"] == 2
    assert chapter["project_id"] == data["project_id"]
    assert chapter["book_id"] == storage_book_id
    assert chapter["domain_book_id"] == data["domain_book_id"]
    assert chapter["status"] == "ACCEPTED"
    assert chapter["quality_decision"] == "ACCEPT"
    assert [version["version"] for version in chapter["versions"]] == [1, 2, 3]
    assert [version["parent_version"] for version in chapter["versions"]] == [
        None,
        1,
        2,
    ]
    assert [version["reason"] for version in chapter["versions"]] == [
        "WRITE",
        "REWRITE",
        "EDIT",
    ]
    assert chapter["versions"][0]["source_evaluation"] is None
    assert chapter["versions"][1]["source_evaluation"]["mode"] == "CRITIC"
    assert chapter["versions"][2]["source_evaluation"]["mode"] == "CRITIC"
    assert chapter["quality_evaluation"]["mode"] == "QUALITY"
    for version in chapter["versions"]:
        assert version["context_package_id"]
        assert version["context_hash"]
        assert version["created_by_role"]
        assert version["requested_model"]
        assert version["effective_model"]
        assert version["canon_version"]
        assert version["book_bible_version"]
        assert version["style_version"] == "STYLE-f005-v1"
        assert version["run_id"] == run_id
        assert version["step_id"]
        assert version["artifact_hash"] == hashlib.sha256(
            version["text"].encode("utf-8")
        ).hexdigest()
        assert version["source_operation"]["artifact_hash"]
        assert version["source_operation"]["input_ref"].endswith("#/input")
        assert version["source_operation"]["input_hash"]
    assert chapter["version"] == 3
    assert chapter["parent_version"] == 2
    assert chapter["artifact_hash"] == chapter["versions"][-1]["artifact_hash"]
    assert chapter["text"] == chapter["versions"][-1]["text"]

    run_state = _read_public_json(f"runs/{run_id}/run_state.json")
    audit = _read_public_json(f"runs/{run_id}/audit.json")
    assert run_state["chapter_lineage"] == lineage_summary
    assert audit["chapter_lineage"] == lineage_summary
    assert audit["chapter_path"] == chapter_path

    context = StorageResolver().resolve_project(
        data["project_id"],
        book_id=data["domain_book_id"],
    )
    reopened = ProjectRepository(context)
    recovered = CrossStoreRecoveryService(reopened).recover(
        lineage_summary["operation_id"]
    )
    assert recovered["status"] == RecoveryStatus.COMMITTED.value
    assert recovered["operation_type"] == "CHAPTER_ARTIFACT_LINEAGE_V2"

    retry_request = dict(request)
    retry_request["technical_retry"] = True
    retry = TestClient(app).post("/agent/step", json=retry_request)
    assert retry.status_code == 200, retry.text
    retry_data = retry.json()
    assert retry_data["chapter_path"] == chapter_path
    retry_chapter = _read_public_json(chapter_path)
    assert retry_chapter == chapter
    chapter_files = list((get_storage_root() / "books" / storage_book_id / "chapters").glob("chapter_*.json"))
    assert chapter_files == [get_storage_root() / chapter_path]
    assert len(retry_chapter["versions"]) == 3

    other = ProjectRepository(
        StorageResolver().resolve_project(
            "PROJ-f005-other",
            book_id="BOOK-f005-other",
        )
    )
    assert other.list_cross_store_operations() == ()
    with pytest.raises(CrossStoreRecoveryError, match="does not exist"):
        CrossStoreRecoveryService(other).recover(lineage_summary["operation_id"])

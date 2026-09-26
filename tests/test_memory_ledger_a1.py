from __future__ import annotations

import json

import pytest
from app.p20_core import canon_service, project_repository
from app.p20_core.cross_store_recovery import CrossStoreRecoveryService, FaultPoint
from app.p20_core.memory_ledger import MemoryLedgerIdentityConflict
from app.p20_core.project_repository import ProjectRepository, StorageResolver
from app.p20_core.storage_paths import get_storage_root
from tests import test_memory_transport_integration as transport
from tests.test_memory_transport_integration import production_pipeline


def request(retry=False):
    return {"mode": "WRITE", "project_id": transport.PROJECT, "book_id": transport.BOOK,
            "run_id": "run-a1-publication", "step_id": "step-a1-publication",
            "payload": {"text": "Write neutral transport proof data.",
                        "input": "Write neutral transport proof data.",
                        "model": transport.REQUESTED_MODEL,
                        "technical_retry": retry, "scope_type": "PROJECT"}}


@pytest.mark.parametrize("boundary", ["artifact_prepare", "artifact_commit", "logical_commit",
                                       "confirmation_event", "chapter_event", "after_final_audit"])
def test_a1_finalization_visibility_and_recovery(production_pipeline, monkeypatch, boundary):
    client, repo = production_pipeline
    transport._use_production_provider(monkeypatch)
    calls = transport._install_sdk_boundary(monkeypatch)
    original_append = project_repository._append_memory_event
    original_inject = CrossStoreRecoveryService._inject
    original_run = CrossStoreRecoveryService._run
    original_final = CrossStoreRecoveryService._write_final_audit
    reached = []

    def fail_append(connection, event):
        target = {"confirmation_event": "ARTIFACT_WRITE_CONFIRMED",
                  "chapter_event": "CHAPTER_VERSION_RECORDED"}.get(boundary)
        if event.event_type == target:
            reached.append(boundary)
            raise MemoryLedgerIdentityConflict("A1 injected ledger failure")
        return original_append(connection, event)

    def tagged_run(self, plan):
        self._a1_chapter_operation = plan.operation_type == "CHAPTER_ARTIFACT_LINEAGE_V2"
        try:
            return original_run(self, plan)
        finally:
            self._a1_chapter_operation = False

    def fail_inject(self, point):
        target = {"artifact_prepare": FaultPoint.AFTER_ARTIFACT_PREPARE,
                  "artifact_commit": FaultPoint.AFTER_ARTIFACT_COMMIT,
                  "logical_commit": FaultPoint.AFTER_LOGICAL_COMMIT}.get(boundary)
        if point == target and getattr(self, "_a1_chapter_operation", False):
            reached.append(boundary)
            raise RuntimeError("A1 injected F4 failure")
        return original_inject(self, point)

    def fail_final(self, plan):
        result = original_final(self, plan)
        if boundary == "after_final_audit" and plan.operation_type == "CHAPTER_ARTIFACT_LINEAGE_V2":
            reached.append(boundary)
            raise RuntimeError("A1 injected lost response")
        return result

    with monkeypatch.context() as fault:
        fault.setattr(project_repository, "_append_memory_event", fail_append)
        fault.setattr(CrossStoreRecoveryService, "_run", tagged_run)
        fault.setattr(CrossStoreRecoveryService, "_inject", fail_inject)
        fault.setattr(CrossStoreRecoveryService, "_write_final_audit", fail_final)
        with pytest.raises((RuntimeError, MemoryLedgerIdentityConflict)):
            client.post("/agent/step", json=request())
    assert reached == [boundary]
    assert len(calls) == 2
    assert canon_service.load_run_state("run-a1-publication") == {}
    assert any(key.startswith("canonical_commit.v1:") for key in repo.list_metadata())
    operation = next(record for record in repo.list_cross_store_operations()
                     if record["operation_type"] == "CHAPTER_ARTIFACT_LINEAGE_V2")
    complete = boundary == "after_final_audit"
    assert (operation["status"] == "COMMITTED") is complete
    for _ in range(2):
        rebuilt = canon_service.rebuild_canon_from_chapters(
            transport.BOOK, project_id=transport.PROJECT, domain_book_id=transport.BOOK)
        snapshot, _ = canon_service.load_canon_snapshot(
            transport.BOOK, project_id=transport.PROJECT, domain_book_id=transport.BOOK)
        assert bool(rebuilt["approved_chapters"]) is complete
        assert bool(snapshot["approved_chapters"]) is complete
        assert bool(snapshot.get("last_accepted_chapter")) is complete
    reopened = ProjectRepository(StorageResolver().resolve_project(transport.PROJECT, book_id=transport.BOOK))
    assert reopened.get_cross_store_operation(operation["operation_id"])["status"] == operation["status"]
    resume_request = request()
    resume_request["resume"] = True
    resume_response = client.post("/agent/step", json=resume_request)
    assert resume_response.status_code == 409
    assert "evaluation binding changed" in resume_response.text
    assert len(calls) == 2
    assert canon_service.load_run_state("run-a1-publication") == {}
    first = client.post("/agent/step", json=request(retry=True))
    assert first.status_code == 200, first.text
    assert first.json()["decision"] == "ACCEPT"
    assert first.json()["chapter_path"] is not None
    second = client.post("/agent/step", json=request(retry=True))
    assert second.status_code == 200, second.text
    assert second.json()["chapter_path"] == first.json()["chapter_path"]
    assert len(calls) == 2
    assert len(list((get_storage_root() / "books" / transport.BOOK / "chapters").glob("chapter_*.json"))) == 1
    events = reopened.list_memory_events(limit=200)[0]
    assert sum(event.event_type == "ARTIFACT_WRITE_CONFIRMED" for event in events) == 1
    assert sum(event.event_type == "CHAPTER_VERSION_RECORDED" for event in events) == 1
    assert reopened.get_cross_store_operation(operation["operation_id"])["status"] == "COMMITTED"
    audit_key = "cross_store_audit.v1:" + operation["operation_id"]
    audit = json.loads(reopened.get_metadata(audit_key))
    assert audit["status"] == "COMMITTED"
    assert sum(item["kind"] == "FINAL_AUDIT" for item in audit["lineage"]) == 1
    assert sum(key == audit_key for key in reopened.list_metadata()) == 1
    final = canon_service.rebuild_canon_from_chapters(
        transport.BOOK, project_id=transport.PROJECT, domain_book_id=transport.BOOK)
    assert len(final["approved_chapters"]) == 1
    assert final["last_accepted_chapter"]["chapter_id"] == "chapter_001"

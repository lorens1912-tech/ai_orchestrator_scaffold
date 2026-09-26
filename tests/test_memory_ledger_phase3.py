from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import app.tools as tools
import app.p20_core.project_repository as repository_module
from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.canon_service import (
    canonical_proposal_hash,
    commit_canonical_proposal,
    operator_proposal_review,
    record_operator_decision,
)
from app.p20_core.context_runtime import ProjectExecutionContext
from app.p20_core.cross_store_recovery import (
    CrossStoreOperationPlan,
    CrossStoreRecoveryService,
    FaultPoint,
    InjectedRecoveryCrash,
    RecoveryInterventionRequired,
)
from app.p20_core.domain_records import FactRecord
from app.p20_core.memory_ledger import (
    MemoryLedgerIdentityConflict,
    create_memory_ledger_schema,
)
from app.p20_core.project_repository import (
    ProjectRepository,
    SeriesAccessContext,
    SeriesRepository,
    StorageResolver,
    ensure_system_repository,
)
from app.p20_core.research import (
    ResearchError,
    VERIFICATION_CRITERIA,
    create_research,
    digest,
    import_source,
    recover_operation,
    run_research,
)
from app.p20_core.series_memory import (
    SeriesMembershipRecord,
    VolumeClosingSnapshot,
    VolumeSnapshotItem,
    VolumeTransferTarget,
)


STAMP = "2026-09-23T12:00:00Z"


def _event_types(repository: ProjectRepository, namespace: str, operation_id: str) -> list[str]:
    events, cursor = repository.list_memory_events(
        operation=(namespace, operation_id), limit=200,
    )
    assert cursor is None
    return [event.event_type for event in events]


def _make_project_v5(repository: ProjectRepository) -> None:
    with repository.connect() as connection:
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'memory_event%'"
        ).fetchall():
            connection.execute(f"DROP TRIGGER {row[0]}")
        connection.execute("DROP TABLE memory_event_entities")
        connection.execute("DROP TABLE memory_events")
        connection.execute(
            "DELETE FROM project_metadata WHERE key = 'memory_ledger_control.v1'"
        )
        connection.execute("UPDATE schema_version SET version = 5 WHERE id = 1")
        connection.execute("UPDATE project_identity SET schema_version = 5 WHERE id = 1")


def _terminal_document(
    proposal_id: str, operation_id: str, project_id: str, book_id: str,
) -> dict:
    proposed_state = {
        "fact_id": "FACT-ledger-cutover", "project_id": project_id,
        "subject_id": "FACT-ledger-cutover", "predicate": "has_neutral_state",
        "object_id": None, "value": "synthetic", "established_event_id": None,
        "established_scene_id": None, "valid_from": None, "valid_to": None,
        "reality_status": "TRUE", "world_time": None, "narrative_order": None,
        "frozen": False, "author_locked": False, "version": 1,
    }
    proposal = {
        "contract_version": "1.0", "proposal_id": proposal_id,
        "project_id": project_id, "book_id": book_id, "series_id": None,
        "scope_type": "PROJECT", "scope_id": project_id,
        "run_id": "RUN-ledger-cutover-proposal", "step_id": "STEP-ledger-cutover-proposal",
        "source_artifact_id": "artifact-ledger-cutover",
        "source_artifact_ref": "project_metadata:canonical_pipeline.v1:legacy#source",
        "source_artifact_hash": "b" * 64, "source_artifact_version": 1,
        "source_scene_id": "SCENE-ledger-cutover", "context_package_id": "CONTEXT-ledger-cutover",
        "context_hash": "c" * 64, "extraction_candidate_set_id": "candidate-ledger-cutover",
        "extraction_candidate_hash": "d" * 64,
        "verification_ref": {"id": "verification-ledger-cutover", "hash": "e" * 64},
        "proposed_mutations": [{
            "target_entity_type": "FACT", "target_entity_id": "FACT-ledger-cutover",
            "operation_type": "CREATE", "expected_current_version": None,
            "expected_current_hash": None, "proposed_state": proposed_state,
            "provenance": {"candidate_id": "candidate-ledger-cutover", "candidate_hash": "d" * 64},
        }],
        "source": "AUTOMATION", "actor_ref": "context-ledger-cutover",
        "authority_ref": "P20_VERIFIED_EXTRACTION_V1", "policy_ref": "CANONICAL_CHANGE_V1",
        "proposal_version": 1, "proposal_hash": "", "status": "COMMITTED",
        "created_at": STAMP,
    }
    proposal["proposal_hash"] = canonical_proposal_hash(proposal)
    binding = {
        key: proposal[key]
        for key in ("proposal_id", "proposal_hash", "project_id", "scope_type", "scope_id")
    }
    impact = {
        **binding, "impact_id": "impact-" + proposal["proposal_hash"],
        "result": [{"impacts": []}], "basis_hash": "f" * 64,
        "policy_version": 1, "coverage": "BOUNDED_PROJECT_GRAPH",
        "result_hash": "1" * 64, "created_at": STAMP,
    }
    guard = {**binding, "outcome": "ALLOW", "reason": None}
    operation_id = digest({
        key: proposal[key]
        for key in ("project_id", "scope_type", "scope_id", "proposal_id", "proposal_hash")
    })
    receipt = {
        "status": "COMMITTED", "canonical_commit": True,
        **{key: proposal[key] for key in (
            "proposal_id", "proposal_hash", "project_id", "scope_type", "scope_id", "run_id", "step_id",
        )},
        "operation_id": operation_id,
        "resulting_versions": {"FACT-ledger-cutover": 1},
        "authorization_ref": None, "impact_id": impact["impact_id"], "guard": guard,
        "invalidation": {"affected_ids": [], "context_packages": "HISTORICAL_ONLY", "rebuild": "NEXT_CONTEXT_BUILD"},
        "created_at": STAMP,
    }
    return {
        "current_version": "1",
        "versions": {
            "1": {
                "proposal": proposal, "basis_hash": "f" * 64,
                "challenges": {}, "decision": None, "impact": impact,
                "initial_guard": guard, "final_guard": guard, "receipt": receipt,
            }
        },
    }


def test_research_commands_are_atomic_and_do_not_copy_source_text(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-research", book_id="BOOK-ledger-research",
    ))
    repo.initialize()
    research = create_research(
        repo, operation_id="research-command-question", research_id="RESEARCH-ledger",
        question="What neutral fact is supported?", purpose="Synthetic proof",
        requested_by="operator-test", related_entity_refs=(),
    )
    source = import_source(
        repo, operation_id="research-command-source", source_id="SOURCE-ledger",
        source_type="USER_NOTE", title="Synthetic source", version=1,
        content="Neutral source content that must remain outside the ledger event.",
    )
    assert research["research_id"] == "RESEARCH-ledger"
    assert source["acquisition_status"] == "TEXT_IMPORTED"
    assert _event_types(repo, "RESEARCH_COMMAND", "research-command-question") == [
        "RESEARCH_COMMAND_RECORDED"
    ]
    events, _ = repo.list_memory_events(
        operation=("RESEARCH_COMMAND", "research-command-source"), limit=200,
    )
    assert [event.event_type for event in events] == ["RESEARCH_COMMAND_RECORDED"]
    assert "Neutral source content" not in events[0].to_json()
    assert import_source(
        repo, operation_id="research-command-source", source_id="SOURCE-ledger",
        source_type="USER_NOTE", title="Synthetic source", version=1,
        content="Neutral source content that must remain outside the ledger event.",
    ) == source
    assert len(repo.list_memory_events(
        operation=("RESEARCH_COMMAND", "research-command-source"), limit=200,
    )[0]) == 1


def test_research_terminal_result_is_fenced_and_reopen_is_idempotent(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    project_id, book_id = "PROJ-ledger-research-run", "BOOK-ledger-research-run"
    repo = ProjectRepository(StorageResolver().resolve_project(project_id, book_id=book_id))
    repo.initialize()
    create_research(
        repo, operation_id="research-create-run", research_id="RESEARCH-ledger-run",
        question="What is supported?", purpose="Synthetic", requested_by="operator-test",
    )
    source = import_source(
        repo, operation_id="research-source-run", source_id="SOURCE-ledger-run",
        source_type="USER_NOTE", title="Synthetic", version=1,
        content="A neutral assertion is supported.",
    )
    execution = ProjectExecutionContext.create(
        project_id=project_id, book_id=book_id, series_id=None,
        run_id="RUN-ledger-research", step_id="STEP-ledger-research",
    )
    import app.p20_core.research as research_module
    import app.p20_core.model_provenance as provenance_module

    def model_call(_repo, call_execution, phase, inputs, model, requested_model, model_routing=None):
        text = inputs["sources"][0]["content"]
        citation = {
            "source_id": source["source_id"], "version": source["version"],
            "content_hash": source["content_hash"], "start": 0, "end": len(text),
            "quote": text,
        }
        output = {"claims": [{"claim": "A neutral assertion is supported.",
                              "source_refs": [citation]}]}
        invocation = {
            "call_id": call_execution.operation_id, "phase": phase,
            "requested_model": requested_model, "effective_model": model,
            "provider_returned_model": model, "context_package_id": "CTXPKG-ledger",
            "context_hash": "a" * 64, "project_id": project_id, "book_id": book_id,
            "run_id": call_execution.run_id, "step_id": call_execution.step_id,
            "input_hash": research_module.digest(inputs), "output": output,
            "transport": {},
        }
        return output, invocation

    monkeypatch.setattr(research_module, "_model_call", model_call)
    monkeypatch.setattr(provenance_module, "mark_validation", lambda *args, **kwargs: None)
    request = dict(
        execution=execution, operation_id="research-extract-run",
        research_id="RESEARCH-ledger-run", action="EXTRACT",
        source_refs=({"source_id": source["source_id"], "version": source["version"]},),
        effective_model="synthetic-model", requested_model="synthetic-model",
    )
    result = run_research(repo, **request)
    assert result["execution_status"] == "COMPLETED"
    assert _event_types(repo, "RESEARCH_OPERATION", "research-extract-run") == [
        "RESEARCH_RESULT_RECORDED"
    ]
    reopened = ProjectRepository(StorageResolver().resolve_project(project_id, book_id=book_id))
    assert run_research(reopened, **request) == result
    assert len(reopened.list_memory_events(
        operation=("RESEARCH_OPERATION", "research-extract-run"), limit=200,
    )[0]) == 1
    import app.operator_api as operator_api
    monkeypatch.setattr(
        operator_api, "_with_operator",
        lambda _token, operation, **_options: operation(SimpleNamespace(operator_id="operator-ledger"),
                                            {project_id: book_id}),
    )
    app.dependency_overrides[operator_api.authenticated_operator] = lambda: "synthetic-token"
    try:
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 52303)) as client:
            read = client.get(
                f"/operator/projects/{project_id}/research/records/RESEARCH-ledger-run"
            )
    finally:
        app.dependency_overrides.pop(operator_api.authenticated_operator, None)
    assert read.status_code == 200, read.text
    assert read.json()["coverage"] == "MEMORY_PIPELINES_V1"
    assert sorted(item["event_type"] for item in read.json()["memory_event_refs"]) == [
        "RESEARCH_COMMAND_RECORDED", "RESEARCH_COMMAND_RECORDED",
        "RESEARCH_RESULT_RECORDED",
    ]

    monkeypatch.setattr(
        research_module, "_model_call",
        lambda *args, **kwargs: ({"invalid": []}, {
            "call_id": "invalid", "phase": "EXTRACT", "requested_model": None,
            "effective_model": "synthetic-model", "provider_returned_model": None,
            "context_package_id": "CTXPKG-invalid", "context_hash": "b" * 64,
            "project_id": project_id, "book_id": book_id,
            "run_id": execution.run_id, "step_id": execution.step_id,
            "input_hash": "c" * 64, "output": {"invalid": []}, "transport": {},
        }),
    )
    try:
        run_research(repo, **(request | {"operation_id": "research-extract-failed"}))
    except ResearchError:
        pass
    else:
        raise AssertionError("invalid research output was accepted")
    assert _event_types(repo, "RESEARCH_OPERATION", "research-extract-failed") == [
        "RESEARCH_RESULT_RECORDED"
    ]


def test_cutover_distinguishes_legacy_terminal_pending_and_new_missing_event(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    project_id, book_id = "PROJ-ledger-cutover", "BOOK-ledger-cutover"
    repository = ProjectRepository(
        StorageResolver().resolve_project(project_id, book_id=book_id),
    )
    repository.initialize()
    proposal_id = "proposal-legacy-terminal"
    terminal = _terminal_document(
        proposal_id, "derived-by-contract", project_id, book_id,
    )
    commit_operation_id = terminal["versions"]["1"]["receipt"]["operation_id"]
    pending_operation_id = "research-legacy-pending"
    terminal_operation_id = "research-legacy-terminal"
    terminal_execution = ProjectExecutionContext.create(
        project_id=project_id,
        book_id=book_id,
        series_id=None,
        run_id="RUN-ledger-cutover-terminal",
        step_id="STEP-ledger-cutover-terminal",
    )
    source_text = "Neutral synthetic evidence."
    source_hash = hashlib.sha256(source_text.encode()).hexdigest()
    source = {
        "source_id": "SOURCE-ledger-cutover", "project_id": project_id,
        "source_type": "USER_NOTE", "title": "Neutral synthetic source",
        "author": None, "publisher": None, "url_or_reference": None,
        "publication_date": None, "accessed_at": STAMP, "reliability": 1.0,
        "content_hash": source_hash, "notes": None, "content": source_text,
        "acquisition_status": "TEXT_IMPORTED", "version": 1,
    }
    citation = {
        "source_id": source["source_id"], "version": 1,
        "content_hash": source_hash, "start": 0, "end": len(source_text),
        "quote": source_text,
    }
    claim = {
        "claim_id": "CLAIM-ledger-cutover", "research_id": "RESEARCH-ledger-cutover",
        "claim": "Neutral synthetic state is supported.", "source_refs": [citation],
        "confidence": None, "verification_status": "REVIEW_REQUIRED",
        "related_fact_id": None, "created_at": STAMP, "version": 1,
    }
    terminal_request = {
        "action": "EXTRACT",
        "research_id": "RESEARCH-ledger-cutover",
        "source_refs": [{"source_id": source["source_id"], "version": 1}],
        "claim_ids": [],
        "effective_model": "synthetic-model",
        "requested_model": "synthetic-model",
        "project_id": project_id,
        "book_id": book_id,
        "run_id": terminal_execution.run_id,
        "step_id": terminal_execution.step_id,
    }
    research_record = {
        "research_id": "RESEARCH-ledger-cutover", "project_id": project_id,
        "question": "What neutral state is supported?", "purpose": "Synthetic cutover proof",
        "requested_by": "operator-test", "related_entity_refs": [], "status": "OPEN",
        "created_at": STAMP, "completed_at": None, "version": 1,
    }
    terminal_inputs = {"research": research_record, "sources": [source], "claims": []}
    terminal_result = {
        "execution_status": "COMPLETED", "action": "EXTRACT",
        "research_id": "RESEARCH-ledger-cutover", "claims": [claim],
        "canonical_commit": False,
    }
    terminal_invocation = {
        "call_id": terminal_execution.operation_id + ":research:" + digest(terminal_operation_id)[:20],
        "phase": "EXTRACT", "requested_model": "synthetic-model",
        "effective_model": "synthetic-model", "provider_returned_model": "synthetic-model",
        "context_package_id": "CONTEXT-ledger-cutover-research", "context_hash": "2" * 64,
        "project_id": project_id, "book_id": book_id,
        "run_id": terminal_execution.run_id,
        "step_id": terminal_execution.step_id + ":research:" + digest(terminal_operation_id)[:20],
        "input_hash": digest(terminal_inputs),
        "output": {"claims": [{"claim": claim["claim"], "source_refs": [citation]}]},
        "transport": {},
    }
    pending_request = {
        **terminal_request,
        "run_id": "RUN-ledger-cutover", "step_id": "STEP-ledger-cutover",
    }
    research_state = {
        "schema_version": 1,
        "project_id": project_id,
        "book_id": book_id,
        "records": {"RESEARCH-ledger-cutover": [research_record]},
            "sources": {source["source_id"]: [source]},
            "claims": {claim["claim_id"]: [claim]},
        "conflicts": {},
        "decisions": {},
        "operations": {
                terminal_operation_id: {
                "request_hash": digest(terminal_request),
                "request": terminal_request,
                    "inputs": terminal_inputs,
                    "status": "COMPLETED",
                    "started_at": STAMP,
                    "attempt_id": "attempt-terminal",
                    "attempts": [],
                    "result": terminal_result,
                    "invocation": terminal_invocation,
                    "result_hash": digest(terminal_result),
                    "criteria_version": "RESEARCH_EVIDENCE_V1",
                    "criteria_hash": digest(VERIFICATION_CRITERIA),
                },
                pending_operation_id: {
                    "request_hash": digest(pending_request),
                    "status": "RUNNING",
                    "attempt_id": "attempt-before-cutover",
                    "started_at": STAMP,
                    "attempts": [],
                    "inputs": terminal_inputs,
                    "request": pending_request,
                }
        },
    }
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO project_metadata(key,value) VALUES (?,?)",
            (
                "canonical_proposal.v1:" + proposal_id,
                json.dumps(terminal, sort_keys=True, separators=(",", ":")),
            ),
        )
        connection.execute(
            "INSERT INTO project_metadata(key,value) VALUES ('canonical_commit.v1:' || ?,?)",
            (commit_operation_id, json.dumps(terminal["versions"]["1"]["receipt"], sort_keys=True)),
        )
        repository.write_research_state(connection, research_state)
    _make_project_v5(repository)
    repository.migrate_schema(
        backup_path=isolated_agentpro_storage / "memory-ledger-cutover-v5.backup",
        maintenance_confirmed=True,
        release_head="TEST-HEAD",
    )
    repository.bootstrap_memory_ledger(maintenance_confirmed=True)

    legacy = commit_canonical_proposal(
        repository, proposal_id,
        expected_hash=terminal["versions"]["1"]["proposal"]["proposal_hash"],
    )
    assert legacy["canonical_commit"] is True
    assert legacy["memory_coverage"] == "LEGACY_BEFORE_LEDGER"
    assert _event_types(repository, "CANONICAL_PROPOSAL", proposal_id) == []

    legacy_research = run_research(
        repository,
        execution=terminal_execution,
        operation_id=terminal_operation_id,
        research_id="RESEARCH-ledger-cutover",
        action="EXTRACT",
        source_refs=({"source_id": source["source_id"], "version": 1},),
        claim_ids=(),
        effective_model="synthetic-model",
        requested_model="synthetic-model",
    )
    assert legacy_research["memory_coverage"] == "LEGACY_BEFORE_LEDGER"
    assert _event_types(repository, "RESEARCH_OPERATION", terminal_operation_id) == []

    import app.p20_core.model_provenance as provenance_module
    monkeypatch.setattr(provenance_module, "recover_invocation", lambda *args, **kwargs: None)
    recovered = recover_operation(
        repository, pending_operation_id, recovered_by="operator-ledger-cutover",
    )
    assert recovered == {"operation_id": pending_operation_id, "status": "FAILED"}
    assert _event_types(repository, "RESEARCH_OPERATION", pending_operation_id) == [
        "RESEARCH_RESULT_RECORDED"
    ]

    new_proposal_id = "proposal-new-missing"
    new_terminal = _terminal_document(
        new_proposal_id, "derived-by-contract", project_id, book_id,
    )
    new_commit_id = new_terminal["versions"]["1"]["receipt"]["operation_id"]
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO project_metadata(key,value) VALUES (?,?)",
            (
                "canonical_proposal.v1:" + new_proposal_id,
                json.dumps(new_terminal, sort_keys=True, separators=(",", ":")),
            ),
        )
        connection.execute(
            "INSERT INTO project_metadata(key,value) VALUES ('canonical_commit.v1:' || ?,?)",
            (new_commit_id, json.dumps(new_terminal["versions"]["1"]["receipt"], sort_keys=True)),
        )
    intervention = commit_canonical_proposal(
        repository, new_proposal_id,
        expected_hash=new_terminal["versions"]["1"]["proposal"]["proposal_hash"],
    )
    assert intervention == {
        "status": "MEMORY_LEDGER_NEEDS_INTERVENTION",
        "canonical_commit": False,
        "proposal_id": new_proposal_id,
        "reason": "REQUIRED_CANONICAL_COMMIT_EVENT_MISSING",
    }
    stored = json.loads(repository.get_metadata(
        "canonical_proposal.v1:" + new_proposal_id,
    ))["versions"]["1"]
    assert stored["memory_ledger_status"] == "MEMORY_LEDGER_NEEDS_INTERVENTION"

    new_research_id = "research-new-missing"
    new_execution = ProjectExecutionContext.create(
        project_id=project_id,
        book_id=book_id,
        series_id=None,
        run_id="RUN-ledger-new-missing",
        step_id="STEP-ledger-new-missing",
    )
    new_request = {
        **terminal_request,
        "run_id": new_execution.run_id,
        "step_id": new_execution.step_id,
    }
    with repository.research_transaction() as state:
        state["operations"][new_research_id] = {
            "request_hash": digest(new_request),
            "request": new_request,
            "inputs": {},
            "status": "COMPLETED",
            "attempts": [],
            "result": {
                "execution_status": "COMPLETED",
                "action": "EXTRACT",
                "research_id": "RESEARCH-ledger-cutover",
                "claims": [],
                "canonical_commit": False,
            },
        }
    missing_research = run_research(
        repository,
        execution=new_execution,
        operation_id=new_research_id,
        research_id="RESEARCH-ledger-cutover",
        action="EXTRACT",
        source_refs=({"source_id": source["source_id"], "version": 1},),
        claim_ids=(),
        effective_model="synthetic-model",
        requested_model="synthetic-model",
    )
    assert missing_research["execution_status"] == "MEMORY_LEDGER_NEEDS_INTERVENTION"
    assert repository.read_research_state()["operations"][new_research_id][
        "memory_ledger_status"
    ] == "MEMORY_LEDGER_NEEDS_INTERVENTION"


def test_f4_intent_recovery_confirmation_and_chapter_versions(
    isolated_agentpro_storage,
) -> None:
    repo = ProjectRepository(StorageResolver().resolve_project(
        "PROJ-ledger-f4", book_id="BOOK-ledger-f4",
    ))
    repo.initialize()
    document = {
        "chapter_id": "chapter_001",
        "version": 2,
        "quality_evaluation": {"artifact_path": "runs/synthetic/quality.json"},
        "versions": [
            {"version": 1, "status": "SUPERSEDED", "artifact_hash": "1" * 64,
             "step_id": "STEP-write", "source_evaluation": None},
            {"version": 2, "status": "ACCEPTED", "artifact_hash": "2" * 64,
             "step_id": "STEP-rewrite", "source_evaluation": None},
        ],
    }
    plan = CrossStoreOperationPlan(
        operation_id="ledger-f4-chapter", project_id=repo.context.project_id,
        book_id=repo.context.book_id, operation_type="CHAPTER_ARTIFACT_LINEAGE_V2",
        source_ref="runs/synthetic/write.json",
        artifact_relative_path="artifacts/ledger/chapter_001.json",
        artifact_bytes=json.dumps(document, sort_keys=True).encode("utf-8"),
        expected_versions={"chapter_version": 2},
        provenance_refs=("runs/synthetic/write.json",),
        run_id="RUN-ledger-f4", step_id="STEP-rewrite",
    )

    def crash(point: FaultPoint) -> None:
        if point == FaultPoint.AFTER_ARTIFACT_COMMIT:
            raise InjectedRecoveryCrash(point.value)

    service = CrossStoreRecoveryService(repo, fault_hook=crash)
    try:
        service.execute(plan)
    except InjectedRecoveryCrash:
        pass
    else:
        raise AssertionError("synthetic crash was not injected")
    assert _event_types(repo, "CROSS_STORE_OPERATION", plan.operation_id) == [
        "ARTIFACT_WRITE_INTENDED"
    ]
    result = CrossStoreRecoveryService(repo).recover(plan.operation_id)
    assert result["status"] == "COMMITTED"
    assert _event_types(repo, "CROSS_STORE_OPERATION", plan.operation_id) == [
        "ARTIFACT_WRITE_INTENDED", "ARTIFACT_WRITE_CONFIRMED",
        "CHAPTER_VERSION_RECORDED", "CHAPTER_VERSION_RECORDED",
    ]
    assert CrossStoreRecoveryService(repo).execute(plan)["status"] == "COMMITTED"
    assert len(repo.list_memory_events(
        operation=("CROSS_STORE_OPERATION", plan.operation_id), limit=200,
    )[0]) == 4
    events, _ = repo.list_memory_events(
        operation=("CROSS_STORE_OPERATION", plan.operation_id), limit=200,
    )
    confirmed = next(event for event in events if event.event_type == "ARTIFACT_WRITE_CONFIRMED")
    with repo.connect() as connection:
        connection.execute("DROP TRIGGER memory_events_reject_delete")
        connection.execute(
            "DELETE FROM memory_events WHERE memory_event_id = ?",
            (confirmed.memory_event_id,),
        )
        create_memory_ledger_schema(
            connection,
            scope_type="PROJECT",
            identity_table="project_identity",
            identity_column="project_id",
        )
    with pytest.raises(RecoveryInterventionRequired, match="required Memory Ledger event"):
        CrossStoreRecoveryService(repo).execute(plan)
    assert repo.get_cross_store_operation(plan.operation_id)["status"] == "NEEDS_INTERVENTION"


def test_series_close_and_alias_share_one_closed_event(
    isolated_agentpro_storage,
) -> None:
    resolver = StorageResolver()
    series_id, project_id, book_id = (
        "SERIES-ledger-close", "PROJ-ledger-close", "BOOK-ledger-close",
    )
    repository = SeriesRepository(resolver.resolve_series(series_id))
    access = SeriesAccessContext.bind(project_id, series_id)
    repository.register_member(access, SeriesMembershipRecord(
        series_id=series_id, project_id=project_id, book_id=book_id,
        source_ref="synthetic/membership", version=1, created_at=STAMP,
    ))
    item = VolumeSnapshotItem(
        record_type="FACT", record_id="FACT-ledger-close", source_version=1,
        source_ref="synthetic/source", provenance_refs=("synthetic/source",),
        state={"fact_id": "FACT-ledger-close", "project_id": project_id,
               "frozen": False, "author_locked": False},
        transfer_target=VolumeTransferTarget.SERIES_MEMORY,
        transfer_reason="Synthetic close proof",
    )
    original = VolumeClosingSnapshot(
        snapshot_id="snapshot-ledger-close", operation_id="close-original",
        series_id=series_id, project_id=project_id, book_id=book_id,
        source_state_version=1, items=(item,), created_at=STAMP,
    )
    assert repository.close_volume(access, original) == original
    alias = replace(original, snapshot_id="snapshot-ledger-alias", operation_id="close-alias")
    assert repository.close_volume(access, alias) == original
    original_events, _ = repository.list_memory_events(
        access, operation=("SERIES_VOLUME_OPERATION", "close-original"), limit=200,
    )
    alias_events, _ = repository.list_memory_events(
        access, operation=("SERIES_VOLUME_OPERATION", "close-alias"), limit=200,
    )
    assert [event.event_type for event in original_events] == ["SERIES_VOLUME_CLOSED"]
    assert alias_events == ()
    assert len(repository.list_series_memory(access)) == 1


def test_active_p20_canonical_pipeline_writes_one_complete_event_set(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    project_id, book_id = "PROJ-ledger-p20", "BOOK-ledger-p20"
    ensure_test_book_bible(book_id)
    system = ensure_system_repository()
    system.bind_project(project_id, book_id)
    repository = ProjectRepository(StorageResolver().resolve_project(project_id, book_id=book_id))
    repository.initialize()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-live")
    from openai.resources.responses.responses import Responses
    controls = {"version": 1, "protected": True}

    def create(_self, **kwargs):
        prompt = json.loads(kwargs["input"])
        task = next(
            item for item in prompt["context_package"]["included_items"]
            if item["layer"] == "TASK"
        )
        payload = json.loads(task["content"])["input"]
        if payload["role"] == "EXTRACTOR":
            source = payload["source"]
            fact = FactRecord(
                fact_id="FACT-ledger-p20", project_id=project_id,
                subject_id="FACT-ledger-p20", predicate="description",
                object_type="TEXT", object_id=None, object_value="Neutral ledger fact",
                reality_status="TRUE", verification_status="VERIFIED", confidence=1,
                frozen=controls["protected"], author_locked=controls["protected"],
                valid_from=None, valid_to=None,
                established_event_id=None, established_scene_id=source["scene_id"],
                source_artifact_ref=source["artifact_ref"], source_refs=(source["scene_id"],),
                canon_version=controls["version"], version=controls["version"],
                created_at=STAMP, updated_at=STAMP,
            )
            output = {"records": [{"record_type": "FACT", "payload": fact.to_dict()}]}
        else:
            output = {"precision_status": "ACCEPT", "completeness_status": "ACCEPT"}
        return SimpleNamespace(output_text=json.dumps(output), model=kwargs["model"], output=[])

    monkeypatch.setattr(Responses, "create", create)
    monkeypatch.setitem(tools.TOOLS, "WRITE", lambda payload: {
        "tool": "WRITE", "payload": {"text": "Neutral synthetic text."},
    })
    body = {
        "mode": "WRITE", "project_id": project_id, "book_id": book_id,
        "run_id": "RUN-ledger-p20", "step_id": "STEP-ledger-p20",
        "payload": {"text": "Write a neutral synthetic observation."},
    }
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 52300)) as client:
        first = client.post("/agent/step", json=body)
        assert first.status_code == 200, first.text
        change = first.json()["canonical_change"]
        assert change["status"] == "COMMITTED"
        retry = client.post("/agent/step", json={
            **body, "payload": {**body["payload"], "technical_retry": True},
        })
        assert retry.status_code == 200
        assert retry.json()["canonical_change"] == change
        import app.operator_api as operator_api
        identity = SimpleNamespace(
            operator_id="operator-ledger", credential_version=1,
            to_dict=lambda: {"operator_id": "operator-ledger", "credential_version": 1},
        )
        monkeypatch.setattr(
            operator_api, "_with_operator",
            lambda _token, operation, **_options: operation(identity, {project_id: book_id}),
        )
        app.dependency_overrides[operator_api.authenticated_operator] = lambda: "synthetic-token"
        try:
            read = client.get(
                f"/operator/projects/{project_id}/proposals/{change['proposal_id']}"
            )
        finally:
            app.dependency_overrides.pop(operator_api.authenticated_operator, None)
        assert read.status_code == 200, read.text
        assert read.json()["coverage"] == "MEMORY_PIPELINES_V1"
        assert [item["event_type"] for item in read.json()["memory_event_refs"]] == [
            "PROPOSAL_RECORDED", "PROPOSAL_STATE_RECORDED",
            "PROPOSAL_STATE_RECORDED", "CANONICAL_ENTITY_CHANGED", "CANONICAL_COMMITTED",
        ]
        controls["version"] = 2
        protected = client.post("/agent/step", json={
            **body, "run_id": "RUN-ledger-p20-update", "step_id": "STEP-ledger-p20-update",
        })
        assert protected.status_code == 200, protected.text
        pending = protected.json()["canonical_change"]
        assert pending["status"] == "AWAITING_USER_APPROVAL", pending
    review = operator_proposal_review(
        repository, pending["proposal_id"], identity, ttl_seconds=300,
    )
    decision_request = {
        key: review["proposal"][key] for key in ("proposal_hash", "scope_type", "scope_id")
    }
    decision_request.update(
        challenge_id=review["challenge"]["challenge_id"], decision="APPROVE",
    )
    original_append = repository_module._append_memory_event

    def abort_decision_event(connection, event):
        if event.event_type == "AUTHOR_DECISION_RECORDED":
            raise MemoryLedgerIdentityConflict("synthetic decision event failure")
        return original_append(connection, event)

    with monkeypatch.context() as failure:
        failure.setattr(repository_module, "_append_memory_event", abort_decision_event)
        with pytest.raises(MemoryLedgerIdentityConflict):
            record_operator_decision(
                repository, pending["proposal_id"], identity, decision_request,
            )
    stored = json.loads(repository.get_metadata(
        "canonical_proposal.v1:" + pending["proposal_id"],
    ))["versions"]["1"]
    assert stored["decision"] is None
    assert stored["challenges"][decision_request["challenge_id"]]["used"] is False
    decision = record_operator_decision(
        repository, pending["proposal_id"], identity, decision_request,
    )
    assert decision["decision"] == "APPROVE"
    assert _event_types(repository, "CANONICAL_PROPOSAL", pending["proposal_id"])[-1] == (
        "AUTHOR_DECISION_RECORDED"
    )
    before_batch = repository.list_structured_memory_records()
    def abort_commit_event(connection, event):
        if event.event_type == "CANONICAL_COMMITTED":
            raise MemoryLedgerIdentityConflict("synthetic batch commit event failure")
        return original_append(connection, event)

    with monkeypatch.context() as failure:
        failure.setattr(repository_module, "_append_memory_event", abort_commit_event)
        with pytest.raises(MemoryLedgerIdentityConflict):
            commit_canonical_proposal(
                repository,
                pending["proposal_id"],
                expected_hash=pending["proposal_hash"],
                identity=identity,
            )
    assert repository.list_structured_memory_records() == before_batch
    failed_batch_events = _event_types(
        repository, "CANONICAL_PROPOSAL", pending["proposal_id"],
    )
    assert "CANONICAL_ENTITY_CHANGED" not in failed_batch_events
    assert "CANONICAL_COMMITTED" not in failed_batch_events
    committed = commit_canonical_proposal(
        repository,
        pending["proposal_id"],
        expected_hash=pending["proposal_hash"],
        identity=identity,
    )
    assert committed["status"] == "COMMITTED"
    assert _event_types(repository, "CANONICAL_PROPOSAL", pending["proposal_id"])[-2:] == [
        "CANONICAL_ENTITY_CHANGED", "CANONICAL_COMMITTED",
    ]
    controls.update(version=3, protected=False)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 52302)) as client:
        denied = client.post("/agent/step", json={
            **body, "run_id": "RUN-ledger-p20-denied", "step_id": "STEP-ledger-p20-denied",
        })
    assert denied.status_code == 200, denied.text
    denial = denied.json()["canonical_change"]
    assert denial["status"] == "REJECTED", denial
    assert _event_types(repository, "CANONICAL_PROPOSAL", denial["proposal_id"])[-1] == (
        "PROPOSAL_STATE_RECORDED"
    )
    pipeline_operation = change["proposal_id"].removeprefix("proposal-")
    assert _event_types(repository, "CANONICAL_PIPELINE", pipeline_operation) == [
        "EXTRACTION_STARTED", "EXTRACTION_ATTEMPT_RECORDED", "EXTRACTION_FINISHED",
    ]
    assert _event_types(repository, "CANONICAL_PROPOSAL", change["proposal_id"]) == [
        "PROPOSAL_RECORDED", "PROPOSAL_STATE_RECORDED",
        "PROPOSAL_STATE_RECORDED", "CANONICAL_ENTITY_CHANGED", "CANONICAL_COMMITTED",
    ]


def test_active_p20_series_pipeline_keeps_events_in_series_owner(
    isolated_agentpro_storage, monkeypatch,
) -> None:
    project_id, book_id, series_id = (
        "PROJ-ledger-series-p20", "BOOK-ledger-series-p20", "SERIES-ledger-series-p20",
    )
    ensure_test_book_bible(book_id)
    system = ensure_system_repository()
    system.bind_project(project_id, book_id)
    project_repository = ProjectRepository(
        StorageResolver().resolve_project(project_id, book_id=book_id),
    )
    project_repository.initialize()
    access = SeriesAccessContext.bind(project_id, series_id)
    series_repository = SeriesRepository(StorageResolver().resolve_series(series_id))
    series_repository.register_member(access, SeriesMembershipRecord(
        series_id=series_id, project_id=project_id, book_id=book_id,
        source_ref="synthetic/series-membership", version=1, created_at=STAMP,
    ))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-live")
    from openai.resources.responses.responses import Responses

    def create(_self, **kwargs):
        prompt = json.loads(kwargs["input"])
        task = next(item for item in prompt["context_package"]["included_items"]
                    if item["layer"] == "TASK")
        payload = json.loads(task["content"])["input"]
        if payload["role"] == "EXTRACTOR":
            source = payload["source"]
            fact = FactRecord(
                fact_id="FACT-ledger-series-p20", project_id=project_id,
                subject_id="FACT-ledger-series-p20", predicate="description",
                object_type="TEXT", object_id=None, object_value="Neutral series fact",
                reality_status="TRUE", verification_status="VERIFIED", confidence=1,
                frozen=False, author_locked=False, valid_from=None, valid_to=None,
                established_event_id=None, established_scene_id=source["scene_id"],
                source_artifact_ref=source["artifact_ref"], source_refs=(source["scene_id"],),
                canon_version=1, version=1, created_at=STAMP, updated_at=STAMP,
            )
            output = {"records": [{"record_type": "FACT", "payload": fact.to_dict()}]}
        else:
            output = {"precision_status": "ACCEPT", "completeness_status": "ACCEPT"}
        return SimpleNamespace(output_text=json.dumps(output), model=kwargs["model"], output=[])

    monkeypatch.setattr(Responses, "create", create)
    monkeypatch.setitem(tools.TOOLS, "WRITE", lambda payload: {
        "tool": "WRITE", "payload": {"text": "Neutral synthetic series text."},
    })
    body = {
        "mode": "WRITE", "project_id": project_id, "book_id": book_id,
        "series_id": series_id, "run_id": "RUN-ledger-series",
        "step_id": "STEP-ledger-series",
        "payload": {"text": "Write a neutral series observation.", "scope_type": "SERIES"},
    }
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 52301)) as client:
        response = client.post("/agent/step", json=body)
        assert response.status_code == 200, response.text
        change = response.json()["canonical_change"]
    assert change["status"] == "COMMITTED", change
    operation = change["proposal_id"].removeprefix("proposal-")
    pipeline_events, _ = series_repository.list_memory_events(
        access, operation=("CANONICAL_PIPELINE", operation), limit=200,
    )
    proposal_events, _ = series_repository.list_memory_events(
        access, operation=("CANONICAL_PROPOSAL", change["proposal_id"]), limit=200,
    )
    assert [event.event_type for event in pipeline_events] == [
        "EXTRACTION_STARTED", "EXTRACTION_ATTEMPT_RECORDED", "EXTRACTION_FINISHED",
    ]
    assert [event.event_type for event in proposal_events] == [
        "PROPOSAL_RECORDED", "PROPOSAL_STATE_RECORDED",
        "PROPOSAL_STATE_RECORDED", "CANONICAL_ENTITY_CHANGED", "CANONICAL_COMMITTED",
    ]
    assert project_repository.list_memory_events(
        operation=("CANONICAL_PROPOSAL", change["proposal_id"]), limit=200,
    )[0] == ()

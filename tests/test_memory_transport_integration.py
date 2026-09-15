from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openai.resources.chat.completions.completions import Completions
from openai.resources.responses.responses import Responses

import app.llm_provider_openai as openai_transport
import app.tools as tools
from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.project_repository import ProjectRepository, StorageResolver, ensure_system_repository
from app.p20_core.storage_paths import get_runs_root
from tests.test_canonical_pipeline import BOOK, FACT, PROJECT, fact, proposal_record
from tests.test_p20_runtime_context_integration import _register_series_member


REQUESTED_MODEL = "gpt-memory-requested"
EFFECTIVE_MODEL = "gpt-memory-effective"
SERIES = "SERIES-memory-transport"
PRODUCTION_MEMORY_PROVIDER = tools.memory_integrity_provider


@pytest.fixture
def production_pipeline(isolated_agentpro_storage):
    del isolated_agentpro_storage
    ensure_test_book_bible(BOOK)
    ensure_system_repository().bind_project(PROJECT, BOOK)
    repo = ProjectRepository(StorageResolver().resolve_project(PROJECT, book_id=BOOK))
    repo.initialize()
    assert tools.memory_integrity_provider is PRODUCTION_MEMORY_PROVIDER
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 51001)) as client:
        yield client, repo


def _step(client, *, run: str, step_id: str, technical_retry: bool = False,
          series_id: str | None = None) -> dict:
    request = {
        "mode": "WRITE",
        "project_id": PROJECT,
        "book_id": BOOK,
        "run_id": run,
        "step_id": step_id,
        "payload": {
            "text": "Write neutral transport proof data.",
            "input": "Write neutral transport proof data.",
            "model": REQUESTED_MODEL,
            "technical_retry": technical_retry,
            "scope_type": "PROJECT",
        },
    }
    if series_id is not None:
        request["series_id"] = series_id
    response = client.post("/agent/step", json=request)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quality_gate"]["decision"] == "ACCEPT", body
    return body


def _use_production_provider(monkeypatch) -> None:
    assert tools.memory_integrity_provider is PRODUCTION_MEMORY_PROVIDER
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-agentpro-memory-transport")
    monkeypatch.setenv("OPENAI_API_MODE", "responses")
    monkeypatch.setenv("MODEL_ALLOWLIST", EFFECTIVE_MODEL)
    monkeypatch.setenv("MODEL_DEFAULT", EFFECTIVE_MODEL)
    monkeypatch.setenv("MODEL_POLICY_MODE", "PERMISSIVE")
    monkeypatch.setattr(openai_transport, "_client", None)


def _task_input(context_package: dict) -> dict:
    task = next(item for item in context_package["included_items"] if item["layer"] == "TASK")
    return json.loads(task["content"])["input"]


def _pipeline_state(repo) -> dict:
    metadata = repo.list_metadata()
    key = next(key for key in metadata if key.startswith("canonical_pipeline.v1:"))
    return json.loads(metadata[key])


def _assert_failed_execution(body: dict, repo, reason: str) -> dict:
    result = body["canonical_change"]
    assert result == {"status": "FAILED", "canonical_commit": False, "reason": reason}
    assert body["ok"] is False
    assert body["status"] == "error"
    assert body["decision"] == "FAILED"
    assert body["quality_gate"]["decision"] == "ACCEPT"
    assert body["run_state"]["status"] == "FAILED"
    assert body["run_state"]["decision"] == "FAILED"
    audit = json.loads((get_runs_root() / body["run_id"] / "audit.json").read_text(encoding="utf-8"))
    assert audit["decision"] == "FAILED"
    state = _pipeline_state(repo)
    assert state["result"] == result
    return state


def _assert_failed_invocation(repo, evidence: dict, *, role: str, reason: str,
                              boundary_reached: bool) -> dict:
    assert evidence["status"] == "FAILED"
    assert evidence["reason"] == reason
    invocation = evidence["invocation"]
    assert invocation["role"] == role
    assert invocation["provider"] == "OPENAI"
    assert invocation["model"] == EFFECTIVE_MODEL
    metadata = invocation["metadata"]
    assert metadata["status"] == "FAILED"
    assert metadata["failure_reason"] == reason
    assert metadata["requested_model"] == REQUESTED_MODEL
    assert metadata["effective_model"] == EFFECTIVE_MODEL
    assert metadata["boundary"] == "app.llm_provider_openai.call_text"
    assert metadata["boundary_reached"] is boundary_reached
    package = repo.get_context_package_for_operation(invocation["call_id"])
    assert package is not None
    assert package.context_package_id == metadata["context_package_id"]
    assert package.context_hash == metadata["context_hash"]
    return metadata


def _install_sdk_boundary(monkeypatch, behavior: str = "accept") -> list[dict]:
    calls: list[dict] = []
    verifier_calls = 0

    def create(_self, **kwargs):
        nonlocal verifier_calls
        prompt = json.loads(kwargs["input"])
        context_package = prompt["context_package"]
        payload = _task_input(context_package)
        role = payload["role"]
        calls.append({"kwargs": kwargs, "prompt": prompt, "payload": payload})
        if behavior == "transport_error":
            raise RuntimeError("synthetic SDK boundary failure")
        if role == "EXTRACTOR":
            if behavior == "plain_text":
                text = "ordinary prose"
            elif behavior == "malformed_json":
                text = '{"records":'
            elif behavior == "wrong_schema":
                text = json.dumps({"answer": "not an extraction"})
            elif behavior == "refusal":
                source = payload["source"]
                record = fact(project=payload["project_id"], source=source, version=1,
                              value="JSON accompanying an SDK refusal must not be accepted")
                text = json.dumps({"records": [{"record_type": "FACT", "payload": record.to_dict()}]})
            elif behavior == "empty_records":
                text = json.dumps({"records": []})
            elif behavior == "duplicate_keys":
                text = '{"records":[],"records":[]}'
            elif behavior == "non_finite":
                text = '{"records":NaN}'
            else:
                source = payload["source"]
                record = fact(project=payload["project_id"], source=source, version=1,
                              value="accepted through existing OpenAI transport")
                text = json.dumps({"records": [{"record_type": "FACT", "payload": record.to_dict()}]})
        else:
            verifier_calls += 1
            if behavior == "invalid_verifier":
                text = json.dumps({"precision_status": "ACCEPT"})
            elif behavior == "verifier_reject":
                text = json.dumps({"precision_status": "REJECT", "completeness_status": "ACCEPT",
                                   "precision_reasons": ["synthetic mismatch"]})
            elif behavior == "revise_then_accept" and verifier_calls == 1:
                text = json.dumps({"precision_status": "REVISE", "completeness_status": "ACCEPT",
                                   "must_fix": ["synthetic retry"]})
            else:
                text = json.dumps({"precision_status": "ACCEPT", "completeness_status": "ACCEPT"})
        output = []
        if behavior == "refusal":
            output = [
                SimpleNamespace(type="reasoning"),
                SimpleNamespace(content=[SimpleNamespace(
                    type="refusal", refusal="synthetic refusal"
                )]),
            ]
        return SimpleNamespace(output_text=text, model="gpt-memory-provider-returned", output=output)

    monkeypatch.setattr(Responses, "create", create)
    return calls


def test_api_p20_reaches_existing_transport_with_distinct_calls_context_audit_and_retry(
        production_pipeline, monkeypatch):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)
    calls = _install_sdk_boundary(monkeypatch)
    _register_series_member(PROJECT, BOOK, SERIES)

    body = _step(client, run="run-memory-transport", step_id="step-memory-transport",
                 series_id=SERIES)
    change = body["canonical_change"]
    assert change["canonical_commit"] is True, change
    assert body["ok"] is True and body["decision"] == "ACCEPT"
    assert [call["payload"]["role"] for call in calls] == ["EXTRACTOR", "VERIFIER"]
    assert calls[0]["payload"]["candidate"] is None
    assert calls[1]["payload"]["candidate"] is not None
    assert calls[0]["payload"]["source"] == calls[1]["payload"]["source"]
    source_text = calls[0]["payload"]["source"]["text"]
    assert source_text != "Write neutral transport proof data."
    assert "Write neutral transport proof data." in source_text
    assert len({call["prompt"]["context_package"]["context_package_id"] for call in calls}) == 2
    assert all(call["kwargs"]["model"] == EFFECTIVE_MODEL for call in calls)
    assert all("temperature" not in call["kwargs"] for call in calls)
    assert all(call["prompt"]["protocol"] == "AGENTPRO_MEMORY_INTEGRITY_V1" for call in calls)

    record = proposal_record(repo, change)
    extractor = record["pipeline"]["extractor"]
    verifier = record["pipeline"]["verifier"]
    assert extractor["invocation"]["call_id"] != verifier["invocation"]["call_id"]
    for call, evidence in zip(calls, (extractor, verifier)):
        package = call["prompt"]["context_package"]
        assert package == evidence["input"]["_context_package"]
        assert package["context_package_id"] == evidence["input"]["context_package_id"]
        assert package["context_hash"] == evidence["input"]["context_hash"]
        assert package["project_id"] == PROJECT
        assert package["book_id"] == BOOK
        assert package["series_id"] == SERIES
        assert package["run_id"] == body["run_id"]
        assert package["step_id"] == call["payload"]["step_id"]
        assert package["role"] == "CANON"
        assert package["mode"] == "MEMORY_" + call["payload"]["role"]
        assert package["effective_model"] == EFFECTIVE_MODEL
        assert repo.get_context_package(package["context_package_id"]).to_dict() == package
        invocation = evidence["invocation"]
        assert repo.get_context_package_for_operation(invocation["call_id"]).to_dict() == package
        metadata = invocation["metadata"]
        assert invocation["provider"] == "OPENAI"
        assert invocation["model"] == EFFECTIVE_MODEL
        assert metadata["requested_model"] == REQUESTED_MODEL
        assert metadata["effective_model"] == EFFECTIVE_MODEL
        assert metadata["provider_returned_model"] == "gpt-memory-provider-returned"
        assert metadata["refused"] is False
        assert metadata["boundary"] == "app.llm_provider_openai.call_text"
        assert metadata["raw_type"] == "responses"
        assert metadata["context_package_id"] == package["context_package_id"]
        assert metadata["context_hash"] == package["context_hash"]
        assert metadata["params"] == {"temperature_requested": None, "temperature_sent": None}
        assert metadata["retried"] is False
        assert metadata["dropped_params"] == []

    calls_before = len(calls)
    contexts_before = tuple(package.to_dict() for package in repo.list_context_packages())
    pipeline_before = json.loads(json.dumps(_pipeline_state(repo)))
    retry = _step(client, run="run-memory-transport", step_id="step-memory-transport",
                  technical_retry=True, series_id=SERIES)
    assert retry["canonical_change"] == change
    assert retry["context_package_id"] == body["context_package_id"]
    assert len(calls) == calls_before
    assert tuple(package.to_dict() for package in repo.list_context_packages()) == contexts_before
    assert _pipeline_state(repo) == pipeline_before
    assert len(json.loads(repo.get_metadata("canonical_versions.v1:FACT:" + FACT))) == 1


def test_content_revision_uses_new_independent_transport_calls_without_duplicate_mutation(
        production_pipeline, monkeypatch):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)
    calls = _install_sdk_boundary(monkeypatch, "revise_then_accept")
    body = _step(client, run="run-memory-revise", step_id="step-memory-revise")
    assert body["canonical_change"]["canonical_commit"] is True, body
    assert [call["payload"]["role"] for call in calls] == [
        "EXTRACTOR", "VERIFIER", "EXTRACTOR", "VERIFIER"]
    assert len({call["prompt"]["context_package"]["context_package_id"] for call in calls}) == 4
    attempts = _pipeline_state(repo)["attempts"]
    assert [attempt["attempt"] for attempt in attempts] == [1, 2]
    assert [attempt["verification"]["decision"] for attempt in attempts] == ["REVISE", "ACCEPT"]
    assert len(json.loads(repo.get_metadata("canonical_versions.v1:FACT:" + FACT))) == 1


@pytest.mark.parametrize("behavior", [
    "plain_text", "malformed_json", "wrong_schema", "refusal", "empty_records",
    "duplicate_keys", "non_finite",
])
def test_invalid_extractor_transport_output_never_creates_canon(
        production_pipeline, monkeypatch, behavior):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)
    calls = _install_sdk_boundary(monkeypatch, behavior)
    body = _step(client, run="run-memory-invalid-" + behavior,
                 step_id="step-memory-invalid-" + behavior)
    state = _assert_failed_execution(body, repo, "MemoryTransportResponseError")
    assert len(calls) == 1
    assert repo.list_structured_memory_records() == {}
    assert not any(key.startswith("canonical_proposal.v1:") for key in repo.list_metadata())
    metadata = _assert_failed_invocation(repo, state["last_extractor"], role="EXTRACTOR",
                                         reason="MemoryTransportResponseError",
                                         boundary_reached=True)
    assert metadata["failure_phase"] == "RESPONSE_VALIDATION"
    assert metadata["raw_type"] == "responses"


def test_invalid_verifier_schema_and_valid_reject_never_create_canon(
        production_pipeline, monkeypatch):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)
    calls = _install_sdk_boundary(monkeypatch, "invalid_verifier")
    body = _step(client, run="run-memory-invalid-verifier",
                 step_id="step-memory-invalid-verifier")
    state = _assert_failed_execution(body, repo, "MemoryTransportResponseError")
    assert [call["payload"]["role"] for call in calls] == ["EXTRACTOR", "VERIFIER"]
    assert repo.list_structured_memory_records() == {}
    assert not any(key.startswith("canonical_proposal.v1:") for key in repo.list_metadata())
    assert state["last_extractor"]["invocation"]["role"] == "EXTRACTOR"
    metadata = _assert_failed_invocation(repo, state["last_verifier"], role="VERIFIER",
                                         reason="MemoryTransportResponseError",
                                         boundary_reached=True)
    assert metadata["failure_phase"] == "RESPONSE_VALIDATION"


def test_valid_verifier_reject_from_transport_never_creates_canon(
        production_pipeline, monkeypatch):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)
    calls = _install_sdk_boundary(monkeypatch, "verifier_reject")
    result = _step(client, run="run-memory-verifier-reject",
                   step_id="step-memory-verifier-reject")["canonical_change"]
    assert result["status"] == "REJECT"
    assert result["canonical_commit"] is False
    assert [call["payload"]["role"] for call in calls] == ["EXTRACTOR", "VERIFIER"]
    assert repo.list_structured_memory_records() == {}
    assert not any(key.startswith("canonical_proposal.v1:") for key in repo.list_metadata())
    assert len(_pipeline_state(repo)["attempts"]) == 1


def test_transport_exception_is_explicit_failure_without_canon(
        production_pipeline, monkeypatch):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)
    calls = _install_sdk_boundary(monkeypatch, "transport_error")
    body = _step(client, run="run-memory-transport-error",
                 step_id="step-memory-transport-error")
    state = _assert_failed_execution(body, repo, "MemoryTransportError")
    assert len(calls) == 1
    assert repo.list_structured_memory_records() == {}
    assert not any(key.startswith("canonical_proposal.v1:") for key in repo.list_metadata())
    metadata = _assert_failed_invocation(repo, state["last_extractor"], role="EXTRACTOR",
                                         reason="MemoryTransportError", boundary_reached=True)
    assert metadata["failure_phase"] == "TRANSPORT"
    durable = json.dumps(repo.list_metadata())
    assert "synthetic SDK boundary failure" not in durable
    assert "sk-test-agentpro-memory-transport" not in durable


def test_missing_configuration_is_explicit_failure_without_network_or_canon(
        production_pipeline, monkeypatch):
    client, repo = production_pipeline
    assert tools.memory_integrity_provider is PRODUCTION_MEMORY_PROVIDER
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_MODE", "responses")
    monkeypatch.setenv("MODEL_ALLOWLIST", EFFECTIVE_MODEL)
    monkeypatch.setenv("MODEL_DEFAULT", EFFECTIVE_MODEL)
    monkeypatch.setattr(openai_transport, "_client", None)
    sdk_calls = []

    def unexpected_sdk_call(_self, **kwargs):
        sdk_calls.append(kwargs)
        raise AssertionError("SDK boundary must not be reached without configuration")

    monkeypatch.setattr(Responses, "create", unexpected_sdk_call)
    body = _step(client, run="run-memory-no-config", step_id="step-memory-no-config")
    state = _assert_failed_execution(body, repo, "MemoryTransportConfigurationError")
    assert sdk_calls == []
    assert repo.list_structured_memory_records() == {}
    assert not any(key.startswith("canonical_proposal.v1:") for key in repo.list_metadata())
    metadata = _assert_failed_invocation(
        repo, state["last_extractor"], role="EXTRACTOR",
        reason="MemoryTransportConfigurationError", boundary_reached=False)
    assert metadata["failure_phase"] == "CONFIGURATION"
    durable = json.dumps(repo.list_metadata())
    assert "OPENAI_API_KEY" not in durable
    reopened = ProjectRepository(StorageResolver().resolve_project(PROJECT, book_id=BOOK))
    assert _pipeline_state(reopened) == state


def test_invalid_internal_role_is_rejected_before_sdk_boundary(monkeypatch):
    sdk_calls = []

    def unexpected_sdk_call(_self, **kwargs):
        sdk_calls.append(kwargs)
        raise AssertionError("invalid role must be rejected before the SDK boundary")

    monkeypatch.setattr(Responses, "create", unexpected_sdk_call)
    with pytest.raises(tools.MemoryTransportResponseError):
        tools.memory_integrity_provider({"role": "WRITER"})
    assert sdk_calls == []


def test_existing_chat_transport_preserves_sdk_refusal_signal(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-agentpro-chat-refusal")
    monkeypatch.setenv("OPENAI_API_MODE", "chat")
    monkeypatch.setattr(openai_transport, "_client", None)

    def create(_self, **kwargs):
        assert kwargs["model"] == EFFECTIVE_MODEL
        return SimpleNamespace(
            model="gpt-memory-provider-returned",
            choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"records": []}', refusal="synthetic refusal"
            ))],
        )

    monkeypatch.setattr(Completions, "create", create)
    result = openai_transport.call_text("synthetic prompt", EFFECTIVE_MODEL)
    assert result["text"] == '{"records": []}'
    assert result["refused"] is True
    assert result["raw_type"] == "chat.completions"

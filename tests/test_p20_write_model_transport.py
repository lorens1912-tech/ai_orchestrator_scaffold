from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openai.resources.responses.responses import Responses

import app.p20_core.canon_service as canon_service
import app.tools as tools
from app.main import app
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.executor import WriterModelInvocationError
from app.p20_core.project_repository import ProjectRepository, StorageResolver
from app.p20_core.storage_paths import get_books_root, get_storage_root


REQUESTED_MODEL = "gpt-write-requested"
EFFECTIVE_MODEL = "gpt-write-effective"


def _request_ids() -> tuple[str, str, str, str]:
    token = uuid4().hex[:10]
    return (
        f"PROJ-write-{token}",
        f"write-book-{token}",
        f"run-write-{token}",
        f"step-write-{token}",
    )


def _prepare(monkeypatch):
    project_id, storage_book_id, run_id, step_id = _request_ids()
    ensure_test_book_bible(storage_book_id)
    monkeypatch.setenv("AGENT_TEST_MODE", "0")
    monkeypatch.setenv("MODEL_ALLOWLIST", EFFECTIVE_MODEL)
    monkeypatch.setenv("MODEL_DEFAULT", EFFECTIVE_MODEL)
    monkeypatch.setenv("MODEL_POLICY_MODE", "PERMISSIVE")
    monkeypatch.setattr(
        canon_service,
        "process_accepted_artifact",
        lambda **_kwargs: {"status": "COMMITTED", "canonical_commit": True},
    )
    request = {
        "project_id": project_id,
        "book_id": storage_book_id,
        "run_id": run_id,
        "step_id": step_id,
        "modes": ["WRITE", "QUALITY"],
        "payload": {
            "input": "Neutral synthetic scene: a courier delivers a sealed key at dusk.",
            "model": REQUESTED_MODEL,
        },
    }
    return request


def _writer_calls(monkeypatch, *, failure: Exception | None = None):
    original = Responses.create
    calls: list[dict] = []

    def tracked(self, **kwargs):
        prompt = json.loads(kwargs["input"])
        if prompt.get("protocol") == "AGENTPRO_P20_WRITER_V1":
            calls.append({"prompt": prompt, "kwargs": kwargs})
            if failure is not None:
                raise failure
        return original(self, **kwargs)

    monkeypatch.setattr(Responses, "create", tracked)
    return calls


def _storage_path(public_path: str) -> Path:
    return get_storage_root() / public_path


def test_tool_write_production_requires_injected_transport(monkeypatch):
    monkeypatch.setenv("AGENT_TEST_MODE", "0")
    with pytest.raises(RuntimeError, match="WRITE_MODEL_TRANSPORT_REQUIRED"):
        tools.tool_write({"input": "neutral"})


def test_tool_write_uses_transport_result_once_and_preserves_explicit_test_mode(monkeypatch):
    monkeypatch.setenv("AGENT_TEST_MODE", "0")
    calls = []

    def model_call():
        calls.append(True)
        return {
            "text": "Neutral final prose.",
            "meta": {
                "requested_model": REQUESTED_MODEL,
                "effective_model": EFFECTIVE_MODEL,
                "provider_family": "OPENAI",
            },
        }

    result = tools.tool_write({"input": "ignored"}, model_call=model_call)
    assert result["payload"]["text"] == "Neutral final prose."
    assert result["payload"]["meta"]["effective_model"] == EFFECTIVE_MODEL
    assert len(calls) == 1

    monkeypatch.setenv("AGENT_TEST_MODE", "1")
    test_result = tools.tool_write({"input": "neutral"}, model_call=lambda: pytest.fail("called"))
    assert test_result["payload"]["meta"]["provider_family"] == "test"


def test_agent_step_write_uses_canonical_transport_audit_and_retry(monkeypatch):
    request = _prepare(monkeypatch)
    calls = _writer_calls(monkeypatch)
    with TestClient(app) as client:
        response = client.post("/agent/step", json=request)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["quality_gate"]["decision"] == "ACCEPT", body
        assert body["chapter_path"], body

        retry_request = json.loads(json.dumps(request))
        retry_request["technical_retry"] = True
        retry = client.post("/agent/step", json=retry_request)
        assert retry.status_code == 200, retry.text

    assert len(calls) == 1
    call = calls[0]
    assert call["kwargs"]["model"] == EFFECTIVE_MODEL
    package = call["prompt"]["context_package"]
    assert package["project_id"] == body["project_id"]
    assert package["book_id"] == body["domain_book_id"]
    assert package["run_id"] == body["run_id"]
    assert package["step_id"].endswith(":001:WRITE")

    write_path = next(path for path in body["artifact_paths"] if path.endswith("_WRITE.json"))
    write_doc = json.loads(_storage_path(write_path).read_text(encoding="utf-8"))
    output = write_doc["result"]["payload"]
    assert output["text"]
    assert "produkcyjny generator offline" not in output["text"]
    assert write_doc["requested_model"] == REQUESTED_MODEL
    assert write_doc["effective_model"] == EFFECTIVE_MODEL
    assert output["meta"]["requested_model"] == REQUESTED_MODEL
    assert output["meta"]["effective_model"] == EFFECTIVE_MODEL

    repository = ProjectRepository(
        StorageResolver().resolve_project(body["project_id"], book_id=body["domain_book_id"])
    )
    traces = [trace for trace in repository.list_model_invocations() if trace["role"] == "WRITER"]
    assert len(traces) == 1
    trace = traces[0]
    assert trace["project_id"] == body["project_id"]
    assert trace["run_id"] == body["run_id"]
    assert trace["step_id"] == package["step_id"]
    assert trace["requested"]["model"] == REQUESTED_MODEL
    assert trace["resolved"]["decision"]["effective_model"] == EFFECTIVE_MODEL
    assert trace["status"] == "TRANSPORT_COMPLETED"
    assert trace["validation"] == "VALID"
    assert len(trace["attempts"]) == 1
    assert trace["attempts"][0]["sent"]["model"] == EFFECTIVE_MODEL
    assert trace["attempts"][0]["provider_reported"]["usage"]["total_tokens"] == 380
    assert write_doc["model_provenance"][0]["invocation_id"] == trace["invocation_id"]


def test_agent_step_write_transport_failure_is_not_successful_prose(monkeypatch):
    request = _prepare(monkeypatch)
    calls = _writer_calls(monkeypatch, failure=TimeoutError("synthetic writer timeout"))
    with TestClient(app) as client:
        with pytest.raises(WriterModelInvocationError, match="TimeoutError"):
            client.post("/agent/step", json=request)

    assert len(calls) == 1
    domain_book_id = "BOOK-" + request["book_id"]
    repository = ProjectRepository(
        StorageResolver().resolve_project(request["project_id"], book_id=domain_book_id)
    )
    traces = [trace for trace in repository.list_model_invocations() if trace["role"] == "WRITER"]
    assert len(traces) == 1
    assert traces[0]["status"] == "TIMEOUT"
    assert traces[0]["validation"] == "INVALID"
    assert traces[0]["attempts"][0]["status"] == "TIMEOUT"
    assert not list((get_books_root() / request["book_id"] / "chapters").glob("*.json"))

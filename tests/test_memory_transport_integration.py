from __future__ import annotations

import json
from dataclasses import fields
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openai.resources.chat.completions.completions import Completions
from openai.resources.responses.responses import Responses

import app.llm_provider_openai as openai_transport
import app.p20_core.memory_extraction as memory_extraction
import app.tools as tools
from app.main import app
from app.operator_dpapi import read_secret
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.local_operator import initialize_operator
from app.p20_core.memory_extraction import (
    SUPPORTED_MEMORY_RECORD_TYPES,
    memory_record_types,
    memory_model_response_format,
)
from app.p20_core.project_repository import ProjectRepository, StorageResolver, ensure_system_repository
from app.p20_core.storage_paths import get_runs_root, get_storage_root
from tests.test_canonical_pipeline import BOOK, FACT, PROJECT, fact, proposal_record
from tests.test_canonical_project_records import mixed_records
from tests.test_p20_runtime_context_integration import _register_series_member


REQUESTED_MODEL = "gpt-memory-requested"
EFFECTIVE_MODEL = "gpt-memory-effective"
SERIES = "SERIES-memory-transport"
PRODUCTION_MEMORY_PROVIDER = tools.memory_integrity_provider


@pytest.fixture
def production_pipeline(isolated_agentpro_storage, monkeypatch):
    del isolated_agentpro_storage
    ensure_test_book_bible(BOOK)
    ensure_system_repository().bind_project(PROJECT, BOOK)
    repo = ProjectRepository(StorageResolver().resolve_project(PROJECT, book_id=BOOK))
    repo.initialize()
    assert tools.memory_integrity_provider is PRODUCTION_MEMORY_PROVIDER
    # These tests isolate the extraction/verifier transport. WRITE transport is
    # covered separately and must not consume or perturb their SDK call matrix.
    def controlled_writer(payload):
        return {
            "tool": "WRITE",
            "payload": {"text": (
            "A neutral observer crossed the quiet square and left a sealed note by the fountain. "
            "The recipient arrived before dusk, recognized the mark, and carried the note away.\n\n"
            + str(payload.get("input") or payload.get("text") or "")
            )},
        }
    monkeypatch.setitem(tools.TOOLS, "WRITE", controlled_writer)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 51001)) as client:
        yield client, repo


def _step(client, *, run: str, step_id: str, technical_retry: bool = False,
          series_id: str | None = None, project_id: str = PROJECT,
          book_id: str = BOOK, instruction: str = "Write neutral transport proof data.") -> dict:
    request = {
        "mode": "WRITE",
        "project_id": project_id,
        "book_id": book_id,
        "run_id": run,
        "step_id": step_id,
        "payload": {
            "text": instruction,
            "input": instruction,
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


def _install_sdk_boundary(monkeypatch, behavior: str = "accept", records_factory=None) -> list[dict]:
    calls: list[dict] = []
    verifier_calls = 0

    def create(_self, **kwargs):
        nonlocal verifier_calls
        raw_prompt = kwargs["input"]
        prompt = json.loads(raw_prompt)
        if prompt.get("protocol") == "AGENTPRO_P20_WRITER_V1":
            return SimpleNamespace(
                output_text=(
                    "A neutral observer crossed the quiet square and placed a sealed note beside the fountain. "
                    "A second observer arrived before dusk, checked the mark on the envelope, and compared it "
                    "with a plain entry in a field notebook. Neither person guessed at hidden causes. They spoke "
                    "briefly, recorded the time, and agreed to preserve the note until the next controlled review.\n\n"
                    "The first observer closed the notebook and stored both items in a dry cabinet. The second "
                    "observer checked the latch, copied the cabinet number, and left by the same empty street. "
                    "Their actions remained limited to the supplied task and introduced no unrelated facts."
                ),
                model=kwargs["model"],
                output=[],
                id="response-writer-memory-transport-test",
                _request_id="request-writer-memory-transport-test",
                usage=SimpleNamespace(input_tokens=120, output_tokens=140, total_tokens=260),
            )
        context_package = prompt["context_package"]
        payload = _task_input(context_package)
        role = payload["role"]
        calls.append({"kwargs": kwargs, "prompt": prompt, "raw_prompt": raw_prompt,
                      "payload": payload})
        if behavior == "transport_error":
            raise RuntimeError("synthetic SDK boundary failure")
        if role == "EXTRACTOR":
            if behavior == "plain_text":
                text = "ordinary prose"
            elif behavior == "malformed_json":
                text = '{"records":'
            elif behavior == "wrong_schema":
                text = json.dumps({"answer": "not an extraction"})
            elif behavior == "top_level_array":
                source = payload["source"]
                record = fact(project=payload["project_id"], source=source, version=1,
                              value="bare arrays remain invalid at the provider boundary")
                text = json.dumps([{"record_type": "FACT", "payload": record.to_dict()}])
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
            elif records_factory is not None:
                text = json.dumps({"records": records_factory(payload)})
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
                                   "precision_reasons": ["synthetic mismatch"],
                                   "completeness_reasons": [], "must_fix": []})
            elif behavior == "revise_then_accept" and verifier_calls == 1:
                text = json.dumps({"precision_status": "REVISE", "completeness_status": "ACCEPT",
                                   "must_fix": ["synthetic retry"]})
            elif behavior == "verifier_revise_without_must_fix":
                text = json.dumps({
                    "precision_status": "REVISE",
                    "completeness_status": "ACCEPT",
                    "precision_reasons": ["synthetic ungrounded revision"],
                    "completeness_reasons": [],
                    "must_fix": [],
                })
            else:
                text = json.dumps({
                    "precision_status": "ACCEPT",
                    "completeness_status": "ACCEPT",
                    "precision_reasons": [],
                    "completeness_reasons": [],
                    "must_fix": [],
                })
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


def test_extractor_response_schema_describes_existing_typed_contract_without_coercion():
    response_format = memory_model_response_format("EXTRACTOR")
    assert response_format["strict"] is True
    schema = response_format["schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["records"]
    variants = {
        item["properties"]["record_type"]["enum"][0]: item
        for item in schema["properties"]["records"]["items"]["anyOf"]
    }
    assert set(variants) == SUPPORTED_MEMORY_RECORD_TYPES
    knowledge_variants = [
        item["properties"]["payload"]
        for item in schema["properties"]["records"]["items"]["anyOf"]
        if item["properties"]["record_type"]["enum"] == ["KNOWLEDGE_EVENT"]
    ]
    assert len(knowledge_variants) == 2
    assert knowledge_variants[0]["properties"]["fact_id"].get("type") == "string"
    assert knowledge_variants[1]["properties"]["event_id"].get("type") == "string"
    fact_payload = variants["FACT"]["properties"]["payload"]
    assert fact_payload["additionalProperties"] is False
    assert set(fact_payload["required"]) == {
        field.name for field in fields(memory_record_types()["FACT"])
    }
    assert fact_payload["properties"]["fact_id"]["pattern"].startswith("^FACT-")
    assert fact_payload["properties"]["project_id"]["pattern"].startswith("^PROJ-")
    assert fact_payload["properties"]["frozen"] == {"type": "boolean"}
    assert fact_payload["properties"]["author_locked"] == {"type": "boolean"}
    assert fact_payload["properties"]["version"] == {"type": "integer", "minimum": 1}
    assert {item["type"] for item in fact_payload["properties"]["object_value"]["anyOf"]} == {
        "string", "number", "boolean", "null", "array",
    }
    state_payload = variants["CHARACTER_STATE"]["properties"]["payload"]
    generated_state = state_payload["properties"]["state_payload"]
    assert generated_state["type"] == "object"
    assert generated_state["additionalProperties"] is False
    assert generated_state["required"] == ["summary", "attributes"]
    event_payload = variants["EVENT"]["properties"]["payload"]["properties"]
    assert event_payload["location_id"]["anyOf"][0]["pattern"].startswith("^PLACE-")
    assert event_payload["participant_ids"]["items"]["pattern"] == (
        memory_extraction._ANY_DOMAIN_ID_PATTERN
    )
    assert event_payload["cause_refs"]["items"]["pattern"].startswith("^EVENT-")

    bound = memory_model_response_format(
        "EXTRACTOR", project_id=PROJECT,
        source_artifact_ref="synthetic:source",
        source_scene_id="SCENE-synthetic-source",
    )["schema"]["properties"]["records"]["items"]["anyOf"]
    bound_fact = next(
        item["properties"]["payload"] for item in bound
        if item["properties"]["record_type"]["enum"] == ["FACT"]
    )
    assert bound_fact["properties"]["established_scene_id"] == {
        "anyOf": [
            {
                "type": "string",
                "enum": ["SCENE-synthetic-source"],
                "pattern": memory_extraction._DOMAIN_ID_FIELD_PATTERNS[
                    "established_scene_id"
                ],
            },
            {"type": "null"},
        ]
    }


def test_full_mixed_project_e2e_uses_only_sdk_boundary_and_survives_reopen(
        production_pipeline, isolated_agentpro_storage, monkeypatch, tmp_path):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)
    controls = {"version": 1}
    calls = _install_sdk_boundary(
        monkeypatch,
        records_factory=lambda payload: mixed_records(
            payload, controls["version"], protected=True),
    )
    _register_series_member(PROJECT, BOOK, SERIES)

    write_inputs = []
    write_adapter = tools.TOOLS["WRITE"]

    def capture_write(payload):
        write_inputs.append(payload)
        return write_adapter(payload)

    monkeypatch.setitem(tools.TOOLS, "WRITE", capture_write)
    system = ensure_system_repository()
    secret_path = tmp_path / "operator" / "operator.dpapi"
    initialize_operator(system, secret_path)
    headers = {"Authorization": "Bearer " + read_secret(secret_path)}

    created = _step(
        client,
        run="run-sdk-mixed-create",
        step_id="step-sdk-mixed-create",
        series_id=SERIES,
    )
    create_receipt = created["canonical_change"]
    assert create_receipt["canonical_commit"] is True, create_receipt
    created_mutations = proposal_record(repo, create_receipt)["proposal"]["proposed_mutations"]
    assert len(created_mutations) == 9
    assert {mutation["target_entity_type"] for mutation in created_mutations} == SUPPORTED_MEMORY_RECORD_TYPES

    controls["version"] = 2
    pending_response = _step(
        client,
        run="run-sdk-mixed-update",
        step_id="step-sdk-mixed-update",
        series_id=SERIES,
    )
    pending = pending_response["canonical_change"]
    assert pending["status"] == "AWAITING_USER_APPROVAL", pending
    assert pending_response["decision"] == "AWAITING_USER_APPROVAL"
    assert pending_response["chapter_path"] is None
    assert pending_response["run_state"]["chapter_path"] is None
    chapters_dir = get_storage_root() / "books" / BOOK / "chapters"
    chapters_before_approval = tuple(chapters_dir.glob("chapter_*.json"))
    assert len(chapters_before_approval) == 1
    assert [call["payload"]["role"] for call in calls] == [
        "EXTRACTOR", "VERIFIER", "EXTRACTOR", "VERIFIER"]
    assert {json.loads(value)["version"] for value in repo.list_structured_memory_records().values()} == {1}

    operator_base = f"/operator/projects/{PROJECT}/proposals/{pending['proposal_id']}"
    review_response = client.post(operator_base + "/review", headers=headers)
    assert review_response.status_code == 200, review_response.text
    review = review_response.json()
    decision_request = {
        key: review["proposal"][key]
        for key in ("proposal_hash", "scope_type", "scope_id")
    }
    decision_request.update(
        challenge_id=review["challenge"]["challenge_id"],
        decision="APPROVE",
    )
    decision_response = client.post(
        operator_base + "/decision", headers=headers, json=decision_request)
    assert decision_response.status_code == 200, decision_response.text
    decision = decision_response.json()
    commit_response = client.post(
        operator_base + "/commit",
        headers=headers,
        json={"proposal_hash": pending["proposal_hash"]},
    )
    assert commit_response.status_code == 200, commit_response.text
    receipt = commit_response.json()
    assert receipt["canonical_commit"] is True, receipt
    assert receipt["authorization_ref"] == decision["authorization_ref"]
    assert set(receipt["resulting_versions"].values()) == {2}

    reopened = ProjectRepository(StorageResolver().resolve_project(PROJECT, book_id=BOOK))
    reopened_record = proposal_record(reopened, pending)
    assert reopened.list_structured_memory_records() == repo.list_structured_memory_records()
    assert all(reopened_record.get(key) for key in (
        "pipeline", "impact", "initial_guard", "decision", "final_guard", "receipt"))
    assert json.loads(reopened.get_metadata(
        "canonical_commit.v1:" + receipt["operation_id"])) == receipt
    assert (get_runs_root() / "run-sdk-mixed-update" / "audit.json").is_file()

    calls_before_retry = len(calls)
    contexts_before_retry = tuple(
        package.to_dict() for package in reopened.list_context_packages())
    replay_response = _step(
        client,
        run="run-sdk-mixed-update",
        step_id="step-sdk-mixed-update",
        technical_retry=True,
        series_id=SERIES,
    )
    replay = replay_response["canonical_change"]
    assert replay == receipt
    assert replay_response["decision"] == "ACCEPT"
    assert replay_response["chapter_path"] is not None
    assert replay_response["run_state"]["chapter_path"] == replay_response["chapter_path"]
    chapters_after_approval = tuple(chapters_dir.glob("chapter_*.json"))
    assert len(chapters_after_approval) == 2
    assert len(calls) == calls_before_retry
    assert tuple(package.to_dict() for package in reopened.list_context_packages()) == contexts_before_retry
    for mutation in reopened_record["proposal"]["proposed_mutations"]:
        history = json.loads(reopened.get_metadata(
            "canonical_versions.v1:" + mutation["target_entity_type"] + ":" + mutation["target_entity_id"]))
        assert [item["version"] for item in history] == [1, 2]

    controls["version"] = 3
    _step(
        client,
        run="run-sdk-mixed-next",
        step_id="step-sdk-mixed-next",
        series_id=SERIES,
    )
    assert "neutral mixed state version 2" in json.dumps(write_inputs[-1])
    assert "neutral mixed state version 2" in json.dumps(calls[-2]["prompt"]["context_package"])

    foreign_project = "PROJ-sdk-mixed-foreign"
    foreign_book = "BOOK-sdk-mixed-foreign"
    ensure_test_book_bible(foreign_book)
    ensure_system_repository().bind_project(foreign_project, foreign_book)
    controls["version"] = 1
    foreign = _step(
        client,
        project_id=foreign_project,
        book_id=foreign_book,
        run="run-sdk-mixed-foreign",
        step_id="step-sdk-mixed-foreign",
    )
    assert foreign["canonical_change"]["canonical_commit"] is True, foreign
    assert "neutral mixed state version 2" not in json.dumps(write_inputs[-1])
    assert "neutral mixed state version 2" not in json.dumps(calls[-2]["prompt"]["context_package"])


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
    assert all(
        call["payload"]["author_instruction"] == "Write neutral transport proof data."
        for call in calls
    )
    assert all(
        call["prompt"]["author_instruction"] == call["payload"]["author_instruction"]
        for call in calls
    )
    assert all(
        "explicit true or false declaration exactly" in call["prompt"]["protection_requirement"]
        for call in calls
    )
    assert all(call["payload"]["current_canonical_versions"] == {} for call in calls)
    assert all(call["prompt"]["current_canonical_versions"] == {} for call in calls)
    assert all(call["prompt"]["expected_candidate_versions"] == {} for call in calls)
    assert all(call["prompt"]["explicit_existing_identities"] == {} for call in calls)
    assert all(
        "Every identity absent from that map is new" in call["prompt"]["version_requirement"]
        for call in calls
    )
    assert all(
        "digits or suffixes in an ID never imply a version" in call["prompt"]["version_requirement"]
        for call in calls
    )
    assert "copy explicit frozen/author_locked declarations" in calls[0]["payload"]["instruction"]
    assert "Protection checks compare" in calls[1]["payload"]["instruction"]
    assert "Treat record identities as opaque values" in calls[1]["payload"]["instruction"]
    assert "belong to the downstream DomainMutationGuard" in calls[1]["payload"]["instruction"]
    assert "must not decide mutation authorization" in calls[1]["prompt"]["verifier_scope_requirement"]
    assert calls[0]["raw_prompt"].index('"author_instruction"') < calls[0]["raw_prompt"].index(
        '"response_contract"'
    )
    source_text = calls[0]["payload"]["source"]["text"]
    assert source_text != "Write neutral transport proof data."
    assert "Write neutral transport proof data." in source_text
    assert len({call["prompt"]["context_package"]["context_package_id"] for call in calls}) == 2
    assert all(call["kwargs"]["model"] == EFFECTIVE_MODEL for call in calls)
    assert all("temperature" not in call["kwargs"] for call in calls)
    assert all(call["prompt"]["protocol"] == "AGENTPRO_MEMORY_INTEGRITY_V1" for call in calls)
    for call in calls:
        role = call["payload"]["role"]
        expected_format = memory_model_response_format(
            role,
            project_id=call["payload"]["project_id"],
            source_artifact_ref=call["payload"]["source"]["artifact_ref"],
            source_scene_id=call["payload"]["source"]["scene_id"],
        )
        assert call["kwargs"]["text"]["format"] == {
            "type": "json_schema",
            **expected_format,
        }
        assert call["prompt"]["response_contract"] == expected_format["schema"]
        assert call["prompt"]["output_requirement"].startswith(
            "Return exactly one JSON object"
        )
    assert calls[0]["kwargs"]["text"]["format"]["strict"] is True
    assert calls[0]["kwargs"]["text"]["format"]["schema"]["required"] == ["records"]
    extractor_variants = calls[0]["kwargs"]["text"]["format"]["schema"][
        "properties"
    ]["records"]["items"]["anyOf"]
    fact_schema = next(
        item["properties"]["payload"]
        for item in extractor_variants
        if item["properties"]["record_type"]["enum"] == ["FACT"]
    )
    assert fact_schema["properties"]["project_id"]["enum"] == [PROJECT]
    artifact_options = fact_schema["properties"]["source_artifact_ref"]["anyOf"]
    assert {
        tuple(item.get("enum", [])) for item in artifact_options
    } == {
        (),
        (calls[0]["payload"]["source"]["artifact_ref"],),
    }
    assert calls[1]["kwargs"]["text"]["format"]["strict"] is True

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
    assert body["canonical_change"]["canonical_commit"] is True, json.dumps(
        body["canonical_change"], indent=2
    )
    assert [call["payload"]["role"] for call in calls] == [
        "EXTRACTOR", "VERIFIER", "EXTRACTOR", "VERIFIER"]
    assert len({call["prompt"]["context_package"]["context_package_id"] for call in calls}) == 4
    attempts = _pipeline_state(repo)["attempts"]
    assert [attempt["attempt"] for attempt in attempts] == [1, 2]
    assert [attempt["verification"]["decision"] for attempt in attempts] == ["REVISE", "ACCEPT"]
    assert len(json.loads(repo.get_metadata("canonical_versions.v1:FACT:" + FACT))) == 1


def test_unresolved_candidate_reference_becomes_bounded_revision_feedback(
        production_pipeline, monkeypatch):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)

    def records(payload):
        complete = mixed_records(payload, 1)
        if payload["revision_feedback"]:
            return complete
        return [
            item for item in complete
            if item["payload"].get("character_id") != "CHAR-bo"
        ]

    calls = _install_sdk_boundary(monkeypatch, records_factory=records)
    body = _step(client, run="run-memory-reference-revise",
                 step_id="step-memory-reference-revise")

    assert body["canonical_change"]["canonical_commit"] is True, json.dumps(
        body["canonical_change"], indent=2
    )
    assert [call["payload"]["role"] for call in calls] == [
        "EXTRACTOR", "VERIFIER", "EXTRACTOR", "VERIFIER"]
    extractor_calls = [call["payload"] for call in calls if call["payload"]["role"] == "EXTRACTOR"]
    assert extractor_calls[0]["revision_feedback"] == []
    assert any("CHAR-bo" in item for item in extractor_calls[1]["revision_feedback"])
    assert calls[0]["prompt"]["revision_feedback"] == []
    assert "every explicit durable object attribute" in calls[0]["payload"]["instruction"]
    assert "entire accepted source" in calls[1]["payload"]["instruction"]
    assert calls[0]["prompt"]["task_instruction"] == calls[0]["payload"]["instruction"]
    assert calls[1]["prompt"]["task_instruction"] == calls[1]["payload"]["instruction"]
    assert calls[2]["prompt"]["revision_feedback"] == extractor_calls[1]["revision_feedback"]
    assert "complete replacement records set" in calls[2]["prompt"]["revision_requirement"]
    assert "requires a CHARACTER_STATE" in calls[2]["prompt"]["revision_requirement"]
    assert calls[2]["raw_prompt"].index('"revision_feedback"') < calls[2]["raw_prompt"].index(
        '"response_contract"'
    )
    assert calls[2]["raw_prompt"].index('"task_instruction"') < calls[2]["raw_prompt"].index(
        '"response_contract"'
    )
    attempts = _pipeline_state(repo)["attempts"]
    assert [attempt["verification"]["decision"] for attempt in attempts] == [
        "REVISE", "ACCEPT"]
    assert attempts[0]["canonical_integrity_mismatches"]
    assert attempts[1]["canonical_integrity_mismatches"] == []
    assert len(repo.list_structured_memory_records()) == 9


def test_unresolved_candidate_reference_escalates_without_partial_state_after_max_attempts(
        production_pipeline, monkeypatch):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)

    def records(payload):
        return [
            item for item in mixed_records(payload, 1)
            if item["payload"].get("character_id") != "CHAR-bo"
        ]

    calls = _install_sdk_boundary(monkeypatch, records_factory=records)
    body = _step(client, run="run-memory-reference-escalate",
                 step_id="step-memory-reference-escalate")

    assert body["canonical_change"]["status"] == "ESCALATED", body
    assert body["canonical_change"]["canonical_commit"] is False
    assert body["chapter_path"] is None
    assert body["run_state"]["chapter_path"] is None
    assert [call["payload"]["role"] for call in calls] == [
        "EXTRACTOR", "VERIFIER", "EXTRACTOR", "VERIFIER"]
    assert all(
        any("CHAR-bo" in item for item in call["payload"]["revision_feedback"])
        for call in calls[2:3]
    )
    attempts = _pipeline_state(repo)["attempts"]
    assert [attempt["verification"]["decision"] for attempt in attempts] == [
        "REVISE", "REVISE"]
    assert attempts[-1]["verification"]["escalation_required"] is True
    assert repo.list_structured_memory_records() == {}
    assert list((get_storage_root() / "books" / BOOK / "chapters").glob(
        "chapter_*.json"
    )) == []


def test_existing_record_version_mismatch_uses_revision_feedback_and_commits_once(
        production_pipeline, monkeypatch):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)

    def records(payload):
        is_update = payload["run_id"] == "run-memory-version-update"
        version = 2 if is_update and payload["revision_feedback"] else 1
        record = fact(
            project=payload["project_id"], source=payload["source"],
            version=version, value=f"version {version} after bounded revision",
        )
        return [{"record_type": "FACT", "payload": record.to_dict()}]

    calls = _install_sdk_boundary(monkeypatch, records_factory=records)
    created = _step(client, run="run-memory-version-create",
                    step_id="step-memory-version-create")
    assert created["canonical_change"]["canonical_commit"] is True

    updated = _step(client, run="run-memory-version-update",
                    step_id="step-memory-version-update",
                    instruction=f"Update the explicitly named existing record {FACT}.")
    assert updated["canonical_change"]["canonical_commit"] is True, updated
    update_calls = [
        call["payload"] for call in calls
        if call["payload"]["run_id"] == "run-memory-version-update"
    ]
    assert [call["role"] for call in update_calls] == [
        "EXTRACTOR", "VERIFIER", "EXTRACTOR", "VERIFIER"]
    assert any("must be 2; received 1" in item
               for item in update_calls[2]["revision_feedback"])
    assert all(
        call["current_canonical_versions"] == {"FACT:" + FACT: 1}
        for call in update_calls
    )
    sdk_update_calls = [
        call for call in calls
        if call["payload"]["run_id"] == "run-memory-version-update"
    ]
    assert all(
        call["prompt"]["expected_candidate_versions"] == {"FACT:" + FACT: 2}
        for call in sdk_update_calls
    )
    assert all(
        call["prompt"]["explicit_existing_identities"] == {"FACT:" + FACT: 2}
        for call in sdk_update_calls
    )
    assert all(
        "never with a new alternative ID" in call["prompt"]["version_requirement"]
        for call in sdk_update_calls
    )
    attempts = [
        value for value in repo.list_metadata().values()
        if '"attempts"' in value and '"run_id": "run-memory-version-update"' in value
    ]
    assert len(attempts) == 1
    parsed_attempts = json.loads(attempts[0])["attempts"]
    assert [item["verification"]["decision"] for item in parsed_attempts] == [
        "REVISE", "ACCEPT"]
    history = json.loads(repo.get_metadata("canonical_versions.v1:FACT:" + FACT))
    assert [item["version"] for item in history] == [1, 2]


def test_new_record_version_mismatch_uses_revision_feedback_before_ledger_commit(
        production_pipeline, monkeypatch):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)

    def records(payload):
        version = 1 if payload["revision_feedback"] else 2
        record = fact(
            project=payload["project_id"], source=payload["source"],
            version=version, value="new record version is validated before persistence",
        )
        return [{"record_type": "FACT", "payload": record.to_dict()}]

    calls = _install_sdk_boundary(monkeypatch, records_factory=records)
    body = _step(client, run="run-memory-new-version",
                 step_id="step-memory-new-version")

    assert body["canonical_change"]["canonical_commit"] is True, body
    extractor_calls = [call for call in calls if call["payload"]["role"] == "EXTRACTOR"]
    assert len(extractor_calls) == 2
    assert any(
        "new FACT:" + FACT + " must be 1; received 2" in item
        for item in extractor_calls[1]["payload"]["revision_feedback"]
    )
    assert extractor_calls[1]["prompt"]["revision_feedback"] == (
        extractor_calls[1]["payload"]["revision_feedback"]
    )
    assert all(call["payload"]["current_canonical_versions"] == {} for call in calls)
    history = json.loads(repo.get_metadata("canonical_versions.v1:FACT:" + FACT))
    assert [item["version"] for item in history] == [1]


@pytest.mark.parametrize("behavior", [
    "plain_text", "malformed_json", "wrong_schema", "top_level_array", "refusal", "empty_records",
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


def test_extractor_failure_cannot_publish_chapter_and_retry_is_idempotent(
        production_pipeline, monkeypatch):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)
    failed_calls = _install_sdk_boundary(monkeypatch, "top_level_array")

    failed = _step(
        client,
        run="run-memory-atomic-failure",
        step_id="step-memory-atomic-failure",
    )
    _assert_failed_execution(failed, repo, "MemoryTransportResponseError")
    assert failed["chapter_path"] is None
    assert failed["chapter_lineage"] is None
    assert failed["canon_memory"] is None
    assert failed["run_state"]["chapter_path"] is None
    chapters_dir = get_storage_root() / "books" / BOOK / "chapters"
    assert list(chapters_dir.glob("chapter_*.json")) == []
    assert repo.list_structured_memory_records() == {}
    assert not any(
        key.startswith("canonical_commit.v1:")
        for key in repo.list_metadata()
    )

    reopened = ProjectRepository(
        StorageResolver().resolve_project(PROJECT, book_id=BOOK)
    )
    assert reopened.list_structured_memory_records() == {}
    failed_state = _pipeline_state(reopened)
    failed_call_count = len(failed_calls)
    replay = _step(
        client,
        run="run-memory-atomic-failure",
        step_id="step-memory-atomic-failure",
        technical_retry=True,
    )
    assert replay["canonical_change"] == failed["canonical_change"]
    assert replay["chapter_path"] is None
    assert replay["run_state"]["chapter_path"] is None
    assert _pipeline_state(reopened) == failed_state
    assert len(failed_calls) == failed_call_count
    assert list(chapters_dir.glob("chapter_*.json")) == []

    success_calls = _install_sdk_boundary(monkeypatch, "accept")
    completed = _step(
        client,
        run="run-memory-atomic-success",
        step_id="step-memory-atomic-success",
    )
    assert completed["canonical_change"]["canonical_commit"] is True
    assert completed["chapter_path"] is not None
    assert completed["chapter_lineage"]["recovery_status"] == "COMMITTED"
    assert len(reopened.list_structured_memory_records()) == 1
    persisted_chapters = list(chapters_dir.glob("chapter_*.json"))
    assert len(persisted_chapters) == 1
    assert json.loads(persisted_chapters[0].read_text(encoding="utf-8"))["status"] == "ACCEPTED"

    successful_call_count = len(success_calls)
    successful_records = reopened.list_structured_memory_records()
    successful_replay = _step(
        client,
        run="run-memory-atomic-success",
        step_id="step-memory-atomic-success",
        technical_retry=True,
    )
    assert successful_replay["canonical_change"] == completed["canonical_change"]
    assert successful_replay["chapter_path"] == completed["chapter_path"]
    assert len(success_calls) == successful_call_count
    assert reopened.list_structured_memory_records() == successful_records
    assert list(chapters_dir.glob("chapter_*.json")) == persisted_chapters


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


def test_verifier_revise_without_actionable_feedback_is_invalid(
        production_pipeline, monkeypatch):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)
    calls = _install_sdk_boundary(monkeypatch, "verifier_revise_without_must_fix")
    body = _step(client, run="run-memory-verifier-empty-revise",
                 step_id="step-memory-verifier-empty-revise")
    state = _assert_failed_execution(body, repo, "MemoryTransportResponseError")
    assert [call["payload"]["role"] for call in calls] == ["EXTRACTOR", "VERIFIER"]
    assert repo.list_structured_memory_records() == {}
    assert not any(key.startswith("canonical_proposal.v1:") for key in repo.list_metadata())
    metadata = _assert_failed_invocation(
        repo, state["last_verifier"], role="VERIFIER",
        reason="MemoryTransportResponseError", boundary_reached=True,
    )
    assert metadata["failure_phase"] == "RESPONSE_VALIDATION"


def test_valid_verifier_reject_from_transport_never_creates_canon(
        production_pipeline, monkeypatch):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)
    calls = _install_sdk_boundary(monkeypatch, "verifier_reject")
    body = _step(client, run="run-memory-verifier-reject",
                 step_id="step-memory-verifier-reject")
    result = body["canonical_change"]
    assert result["status"] == "REJECT"
    assert result["canonical_commit"] is False
    assert body["decision"] == "REJECT"
    assert body["chapter_path"] is None
    assert body["chapter_lineage"] is None
    assert body["canon_memory"] is None
    assert body["run_state"]["chapter_path"] is None
    assert list((get_storage_root() / "books" / BOOK / "chapters").glob(
        "chapter_*.json"
    )) == []
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

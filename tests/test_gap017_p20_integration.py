from __future__ import annotations

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import operator_api
from app.p20_core import executor
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.evaluation import prepare_evaluation, EvaluationBinding
from app.p20_core.project_repository import ProjectRepository, StorageResolver
from app.p20_core.storage_paths import get_storage_root


def _accept_text() -> str:
    paragraph = (
        "A neutral test character crossed the quiet archive and checked every numbered "
        "shelf against the sealed inventory while rain marked time on the high windows."
    )
    return "\n\n".join(paragraph for _ in range(10))


def _request(*, suffix: str, text: str, modes=None, steps=None) -> dict:
    body = {
        "book_id": f"TEST_BOOK_GAP017_{suffix}",
        "project_id": f"PROJ-gap017-{suffix.lower()}",
        "run_id": f"run-gap017-{suffix.lower()}",
        "step_id": f"step-gap017-{suffix.lower()}",
        "modes": modes or ["QUALITY"],
        "payload": {"text": text},
    }
    if steps is not None:
        body["payload"]["steps"] = steps
    return body


def _repository(response: dict) -> ProjectRepository:
    return ProjectRepository(StorageResolver().resolve_project(
        response["project_id"], book_id=response["domain_book_id"]
    ))


def _public_json(path: str) -> dict:
    return json.loads((get_storage_root() / path).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("suffix", "text", "expected"),
    [
        ("ACCEPT", _accept_text(), "ACCEPT"),
        ("REVISE", ("Neutral synthetic sentence. " * 14).strip(), "REVISE"),
        ("REJECT", "tiny", "REJECT"),
    ],
)
def test_agent_step_quality_persists_local_record_and_projections(
    isolated_agentpro_storage: Path, suffix: str, text: str, expected: str
) -> None:
    del isolated_agentpro_storage
    response = TestClient(app).post("/agent/step", json=_request(
        suffix=suffix, text=text
    ))
    assert response.status_code == 200, response.text
    body = response.json()
    record = body["evaluation_record"]
    assert record["decision"] == expected
    assert record["execution_status"] == "COMPLETED"
    assert record["validation_status"] == "VALID"
    assert record["evaluator_kind"] == "LOCAL_DETERMINISTIC"
    assert record["requested_model"] is None
    assert record["effective_model"] is None
    assert record["provider"] is None
    assert record["context_package_id"]
    assert record["context_hash"]

    envelope = _repository(body).list_evaluation_envelopes()[0]
    assert envelope["record"] == record
    step = _public_json(body["artifact_paths"][-1])
    run_state = _public_json(f"runs/{body['run_id']}/run_state.json")
    audit = _public_json(f"runs/{body['run_id']}/audit.json")
    assert step["evaluation_record"] == record
    assert run_state["evaluation_record"] == record
    assert audit["evaluation_record"] == record


def test_completed_retry_reuses_output_and_skips_evaluator(
    isolated_agentpro_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del isolated_agentpro_storage
    calls = 0
    original = executor.TOOLS["QUALITY"]

    def counted(payload):
        nonlocal calls
        calls += 1
        return original(payload)

    monkeypatch.setitem(executor.TOOLS, "QUALITY", counted)
    request = _request(suffix="RETRY", text=_accept_text())
    first = TestClient(app).post("/agent/step", json=request)
    assert first.status_code == 200, first.text
    retry_request = dict(request)
    retry_request["technical_retry"] = True
    retry = TestClient(app).post("/agent/step", json=retry_request)
    assert retry.status_code == 200, retry.text
    first_body, retry_body = first.json(), retry.json()
    assert calls == 1
    assert retry_body["evaluation_record"] == first_body["evaluation_record"]
    retry_step = _public_json(retry_body["artifact_paths"][-1])
    assert "__attempt_02" in retry_body["artifact_paths"][-1]
    assert retry_step["result"]["payload"]["meta"]["evaluation_reused"] is True


def test_crash_after_durable_evaluation_rebuilds_projection_without_reevaluation(
    isolated_agentpro_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del isolated_agentpro_storage
    calls = 0
    crashed = False
    original_quality = executor.TOOLS["QUALITY"]
    original_write = executor._atomic_write_json

    def counted(payload):
        nonlocal calls
        calls += 1
        return original_quality(payload)

    def crash_once(path, data):
        nonlocal crashed
        if not crashed and data.get("mode") == "QUALITY":
            crashed = True
            raise RuntimeError("synthetic crash after durable evaluation")
        return original_write(path, data)

    monkeypatch.setitem(executor.TOOLS, "QUALITY", counted)
    monkeypatch.setattr(executor, "_atomic_write_json", crash_once)
    request = _request(suffix="CRASH", text=_accept_text())
    with pytest.raises(RuntimeError, match="synthetic crash"):
        TestClient(app).post("/agent/step", json=request)

    monkeypatch.setattr(executor, "_atomic_write_json", original_write)
    retry_request = dict(request)
    retry_request["technical_retry"] = True
    retry = TestClient(app).post("/agent/step", json=retry_request)
    assert retry.status_code == 200, retry.text
    body = retry.json()
    assert calls == 1
    assert len(_repository(body).list_evaluation_envelopes()) == 1
    assert _public_json(body["artifact_paths"][-1])["evaluation_record"] == body["evaluation_record"]


def test_retry_with_changed_criteria_fails_closed(
    isolated_agentpro_storage: Path
) -> None:
    del isolated_agentpro_storage
    request = _request(suffix="CRITERIA", text=_accept_text())
    request["payload"]["min_words"] = 10
    first = TestClient(app).post("/agent/step", json=request)
    assert first.status_code == 200, first.text
    retry = json.loads(json.dumps(request))
    retry["technical_retry"] = True
    retry["payload"]["min_words"] = 20
    rejected = TestClient(app).post("/agent/step", json=retry)
    assert rejected.status_code == 409
    assert "binding changed" in rejected.json()["detail"]


def test_runtime_selects_latest_exact_artifact_and_rejects_stale_accept(
    isolated_agentpro_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del isolated_agentpro_storage
    text_a = "artifact A"
    text_b = _accept_text() + "\n\nversion B"

    def controlled_quality(payload):
        decision = "ACCEPT" if payload["text"] == text_b else "REVISE"
        return {"tool": "QUALITY", "payload": {
            "DECISION": decision, "REASONS": [], "MUST_FIX": [], "SCORE": 0.9,
        }}

    monkeypatch.setitem(executor.TOOLS, "QUALITY", controlled_quality)
    monkeypatch.setitem(executor.TOOLS, "REWRITE", lambda payload: {
        "tool": "REWRITE", "payload": {"text": text_b}
    })
    request = _request(
        suffix="ORDER", text=text_a, modes=["QUALITY", "REWRITE"],
        steps=[{"mode": "QUALITY"}, {"mode": "REWRITE"}, {"mode": "QUALITY"}],
    )
    response = TestClient(app).post("/agent/step", json=request)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quality_gate"]["decision"] == "ACCEPT"
    assert body["evaluation_record"]["artifact_hash"] == hashlib.sha256(
        text_b.encode("utf-8")
    ).hexdigest()
    assert len(_repository(body).list_evaluation_envelopes()) == 2

    stale = _request(
        suffix="STALE", text=text_a, modes=["QUALITY", "REWRITE"],
        steps=[{"mode": "QUALITY"}, {"mode": "REWRITE"}],
    )
    monkeypatch.setitem(executor.TOOLS, "QUALITY", lambda payload: {
        "tool": "QUALITY", "payload": {
            "DECISION": "ACCEPT", "REASONS": [], "MUST_FIX": [], "SCORE": 1.0,
        }}
    )
    stale_response = TestClient(app).post("/agent/step", json=stale)
    assert stale_response.status_code == 200, stale_response.text
    stale_body = stale_response.json()
    assert stale_body["quality_gate"] == {
        "decision": "REVISE", "reasons": ["FINAL_ARTIFACT_NOT_EVALUATED"]
    }
    assert stale_body["evaluation_record"] is None


def test_reevaluation_has_new_identity_and_retry_intent_is_invalid(
    isolated_agentpro_storage: Path
) -> None:
    del isolated_agentpro_storage
    request = _request(suffix="REEVAL", text=_accept_text())
    first = TestClient(app).post("/agent/step", json=request)
    assert first.status_code == 200, first.text
    first_record = first.json()["evaluation_record"]

    reevaluate = dict(request)
    reevaluate["evaluation_intent"] = "REEVALUATE"
    reevaluate["reevaluation_of"] = first_record["evaluation_id"]
    second = TestClient(app).post("/agent/step", json=reevaluate)
    assert second.status_code == 200, second.text
    second_body = second.json()
    second_record = second_body["evaluation_record"]
    assert second_record["evaluation_id"] != first_record["evaluation_id"]
    assert second_record["operation_id"] != first_record["operation_id"]
    assert second_record["reevaluation_of"] == first_record["evaluation_id"]
    assert second_body["step_id"].startswith("step-reevaluate-")

    retry_reevaluation = dict(request)
    retry_reevaluation["step_id"] = second_body["step_id"]
    retry_reevaluation["technical_retry"] = True
    retried = TestClient(app).post("/agent/step", json=retry_reevaluation)
    assert retried.status_code == 200, retried.text
    assert retried.json()["evaluation_record"] == second_record

    invalid = dict(reevaluate)
    invalid["technical_retry"] = True
    rejected = TestClient(app).post("/agent/step", json=invalid)
    assert rejected.status_code == 422


def test_write_lineage_uses_same_durable_quality_record(
    isolated_agentpro_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del isolated_agentpro_storage
    request = _request(
        suffix="LINEAGE", text=_accept_text(), modes=["WRITE", "QUALITY"]
    )
    ensure_test_book_bible(request["book_id"])
    monkeypatch.setitem(executor.TOOLS, "WRITE", lambda payload: {
        "tool": "WRITE", "payload": {"text": payload["text"]}
    })
    response = TestClient(app).post("/agent/step", json=request)
    assert response.status_code == 200, response.text
    body = response.json()
    record = body["evaluation_record"]
    chapter = _public_json(body["chapter_path"])
    assert chapter["quality_evaluation"]["evaluation_id"] == record["evaluation_id"]
    assert chapter["quality_evaluation"]["evaluation_record_hash"] == record["record_hash"]
    assert chapter["quality_evaluation"]["evaluated_artifact_hash"] == chapter["artifact_hash"]


def test_operator_evaluation_recovery_endpoint_preserves_identity(
    isolated_agentpro_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del isolated_agentpro_storage
    project_id = "PROJ-gap017-recovery"
    book_id = "BOOK-gap017-recovery"
    repository = ProjectRepository(StorageResolver().resolve_project(project_id, book_id=book_id))
    repository.initialize()
    binding = EvaluationBinding.local_deterministic(
        project_id=repository.context.project_id,
        book_id=repository.context.book_id,
        series_id=None,
        run_id="run-gap017-recovery",
        step_id="step-gap017-recovery:001:QUALITY",
        operation_id="context:recovery",
        artifact_id="ART-gap017-recovery",
        artifact_version="1",
        artifact_hash="1" * 64,
        criteria_version="QUALITY_RULES_V1",
        criteria={"version": 1},
        context_package_id="CONTEXT-gap017-recovery",
        context_hash="2" * 64,
        evaluator_id="app.quality_rules.evaluate_quality",
        evaluator_version="QUALITY_RULES_V1",
        configuration={"min_words": 200},
    )
    evaluation_id = prepare_evaluation(repository, binding)

    monkeypatch.setattr(operator_api, "_with_operator", lambda _token, operation: operation(
        SimpleNamespace(operator_id="OPERATOR-gap017"), {repository.context.project_id: repository.context.book_id}
    ))
    app.dependency_overrides[operator_api.authenticated_operator] = lambda: "synthetic-token"
    try:
        response = TestClient(app).post(
            f"/operator/projects/{repository.context.project_id}/evaluations/{binding.operation_id}/recover",
            json={
                "evaluation_id": evaluation_id,
                "run_id": binding.run_id,
                "step_id": binding.step_id,
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evaluation_id"] == evaluation_id
    assert body["attempt_token"].startswith("EVAL-ATTEMPT-")
    assert body["reused"] is False

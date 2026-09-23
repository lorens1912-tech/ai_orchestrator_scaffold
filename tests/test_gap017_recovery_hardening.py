"""Phase 4: real SQLite, isolated P20/API and deterministic crash boundaries."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import copy
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.p20_core import evaluation as ev, executor, runtime
from app.p20_core.context_builder import ContextBuilder
from app.p20_core.project_repository import ProjectRepository, StorageResolver
from app.p20_core.storage_paths import get_storage_root
from tests.test_gap017_evaluation_record import binding, repository, complete, sha
from tests.test_gap017_p20_integration import _request, _accept_text, _repository, _public_json
from tests.test_canonical_pipeline import pipeline
from app.p20_core.local_operator import initialize_operator
from app.p20_core.project_repository import ensure_system_repository
from app.operator_dpapi import read_secret


def _repo_for(request):
    return ProjectRepository(StorageResolver().resolve_project(
        request["project_id"], book_id="BOOK-" + request["book_id"]))


def _operator_client(root):
    secret_path = root / "synthetic-operator" / "operator.dpapi"
    initialize_operator(ensure_system_repository(), secret_path)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50001))
    client.headers["Authorization"] = "Bearer " + read_secret(secret_path)
    return client


def _recover(client, repo, envelope):
    return client.post(
        f"/operator/projects/{repo.context.project_id}/evaluations/{envelope['operation_id']}/recover",
        json={k: envelope[k] for k in ("evaluation_id", "run_id", "step_id")})


def _retry(request):
    response = TestClient(app).post("/agent/step", json={**request, "technical_retry": True})
    assert response.status_code == 200, response.text
    return response.json()


def _reevaluate(request, source, *, identity=None):
    value = copy.deepcopy(request)
    if identity is None:
        value.pop("step_id", None)
    else:
        value["step_id"] = identity
    value["evaluation_intent"] = "REEVALUATE"
    value["reevaluation_of"] = source["evaluation_id"]
    return value


def test_reevaluation_request_identity_reuses_completed_result_after_reopen(
    isolated_agentpro_storage, monkeypatch
):
    request = _request(suffix="REQUEST-ID-REOPEN", text=_accept_text())
    source = TestClient(app).post("/agent/step", json=request).json()["evaluation_record"]
    calls = 0
    original = executor.TOOLS["QUALITY"]

    def counted(payload):
        nonlocal calls
        calls += 1
        return original(payload)

    monkeypatch.setitem(executor.TOOLS, "QUALITY", counted)
    identity = "stable-reevaluation-request-reopen"
    body = _reevaluate(request, source, identity=identity)
    first = TestClient(app).post("/agent/step", json=body)
    assert first.status_code == 200, first.text

    # A new repository and client represent reopen and a lost first response.
    reopened = ProjectRepository(StorageResolver().resolve_project(
        request["project_id"], book_id="BOOK-" + request["book_id"]
    ))
    assert reopened.list_evaluation_envelopes()
    monkeypatch.setattr(
        ContextBuilder,
        "build",
        lambda *args, **kwargs: pytest.fail(
            "duplicate REEVALUATE rebuilt context instead of reusing the pinned package"
        ),
    )
    duplicate = TestClient(app).post("/agent/step", json=body)
    assert duplicate.status_code == 200, duplicate.text
    first_body, duplicate_body = first.json(), duplicate.json()
    assert duplicate_body["step_id"] == first_body["step_id"]
    assert duplicate_body["evaluation_record"] == first_body["evaluation_record"]
    assert calls == 1
    assert len(reopened.list_evaluation_envelopes()) == 2


def test_concurrent_reevaluation_request_identity_has_one_operation(
    isolated_agentpro_storage, monkeypatch
):
    request = _request(suffix="REQUEST-ID-CONCURRENT", text=_accept_text())
    source = TestClient(app).post("/agent/step", json=request).json()["evaluation_record"]
    body = _reevaluate(request, source, identity="stable-reevaluation-request-concurrent")
    calls = 0
    calls_lock = threading.Lock()
    original = executor.TOOLS["QUALITY"]

    def counted(payload):
        nonlocal calls
        with calls_lock:
            calls += 1
        return original(payload)

    monkeypatch.setitem(executor.TOOLS, "QUALITY", counted)
    barrier = threading.Barrier(2)

    def post_duplicate(_):
        barrier.wait()
        return TestClient(app).post("/agent/step", json=body)

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(post_duplicate, range(2)))
    assert all(response.status_code in {200, 409} for response in responses)
    assert any(response.status_code == 200 for response in responses)
    completed = TestClient(app).post("/agent/step", json=body)
    assert completed.status_code == 200, completed.text
    successful = [response.json() for response in responses if response.status_code == 200]
    assert all(item["step_id"] == completed.json()["step_id"] for item in successful)
    assert all(
        item["evaluation_record"] == completed.json()["evaluation_record"]
        for item in successful
    )
    assert calls == 1
    assert len(_repo_for(request).list_evaluation_envelopes()) == 2


def test_interrupted_reevaluation_request_keeps_assigned_evaluation(
    isolated_agentpro_storage, monkeypatch
):
    request = _request(suffix="REQUEST-ID-INTERRUPTED", text=_accept_text())
    source = TestClient(app).post("/agent/step", json=request).json()["evaluation_record"]
    body = _reevaluate(request, source, identity="stable-reevaluation-request-interrupted")

    def interrupted(_payload):
        raise RuntimeError("synthetic interruption after evaluation start")

    monkeypatch.setitem(executor.TOOLS, "QUALITY", interrupted)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        TestClient(app).post("/agent/step", json=body)
    repo = _repo_for(request)
    running = [
        item for item in repo.list_evaluation_envelopes()
        if item["evaluation_id"] != source["evaluation_id"]
    ]
    assert len(running) == 1
    evaluation_id = running[0]["evaluation_id"]
    assert running[0]["execution_status"] == "RUNNING"

    duplicate = TestClient(app, raise_server_exceptions=False).post(
        "/agent/step", json=body
    )
    assert duplicate.status_code == 409, duplicate.text
    reopened = _repo_for(request)
    after = [
        item for item in reopened.list_evaluation_envelopes()
        if item["evaluation_id"] != source["evaluation_id"]
    ]
    assert len(after) == 1
    assert after[0]["evaluation_id"] == evaluation_id
    assert after[0]["execution_status"] == "RUNNING"


def _reservation(repo):
    entries = [(key, json.loads(raw)) for key, raw in repo.list_metadata().items()
               if key.startswith("reevaluation_request.v1:")]
    assert len(entries) == 1
    return entries[0]


def _reserve_with_interruption(monkeypatch, body, boundary):
    """Inject only after a real durable write, or inside its transaction."""
    if boundary == "after_assignment":
        owner, name = ProjectRepository, "claim_reevaluation_request"
    elif boundary == "after_input":
        owner, name = executor, "_bind_execution_input"
    elif boundary == "after_context":
        owner, name = ProjectRepository, "save_context_package"
    elif boundary == "before_start_commit":
        owner, name = ProjectRepository, "checkpoint_reevaluation_request"
    else:
        owner, name = executor, "start_evaluation"
    original = getattr(owner, name)

    def interrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        if boundary != "before_start_commit" or args[3] == "evaluations":
            raise RuntimeError("synthetic R01 interruption " + boundary)
        return result

    with monkeypatch.context() as injection:
        injection.setattr(owner, name, interrupt)
        with pytest.raises(RuntimeError, match="synthetic R01 interruption"):
            TestClient(app).post("/agent/step", json=body)


@pytest.mark.parametrize("boundary", [
    "after_assignment", "book_lock", "after_input", "after_context", "before_start_commit",
])
def test_r01_reservation_without_start_reopens_with_same_ids(
    isolated_agentpro_storage, monkeypatch, boundary,
):
    # Permanent versions of both auditor reproductions, with stronger success
    # assertions. The original probes and failing logs remain untouched.
    from app.p20_core.lock_service import acquire_book_lock, release_book_lock, get_book_lock
    request = _request(suffix="R01-" + boundary, text=_accept_text())
    source = TestClient(app).post("/agent/step", json=request).json()
    body = _reevaluate(request, source["evaluation_record"], identity="stable-r01-request")
    calls = []
    original = executor.TOOLS["QUALITY"]

    def quality(payload):
        calls.append(payload["context_hash"])
        return original(payload)

    monkeypatch.setitem(executor.TOOLS, "QUALITY", quality)
    if boundary == "book_lock":
        acquire_book_lock(request["book_id"], "run-neutral-lock-owner")
        try:
            for _ in range(2):
                response = TestClient(app).post("/agent/step", json=body)
                assert response.status_code == 409, response.text
                assert response.json()["detail"]["code"] == "BOOK_LOCKED"
                assert get_book_lock(request["book_id"])["run_id"] == "run-neutral-lock-owner"
        finally:
            release_book_lock(request["book_id"])
    else:
        _reserve_with_interruption(monkeypatch, body, boundary)
    repo = _repo_for(request)
    _, reserved = _reservation(repo)
    assert reserved["reservation_status"] == "RESERVED"
    assert reserved["evaluations"] == {}
    assert len(repo.list_evaluation_envelopes()) == 1
    assert calls == []
    pinned = dict(reserved["contexts"])
    if pinned:
        monkeypatch.setattr(ContextBuilder, "_collect_candidates",
                            lambda *a, **kw: pytest.fail("replaced pinned context with latest"))
    result = TestClient(app).post("/agent/step", json=body)
    assert result.status_code == 200, result.text
    replay = TestClient(app).post("/agent/step", json=body)
    assert replay.status_code == 200, replay.text
    first, second = result.json(), replay.json()
    assert first["step_id"] == second["step_id"] == reserved["assigned_step_id"]
    assert first["evaluation_record"] == second["evaluation_record"]
    assert first["evaluation_record"]["operation_id"].startswith(reserved["assigned_operation_id"] + ":")
    assert first["evaluation_record"]["evaluation_id"] != source["evaluation_record"]["evaluation_id"]
    assert len(calls) == 1
    if pinned:
        assert calls == list(pinned.values())
    reopened = _repo_for(request)
    assert len(reopened.list_evaluation_envelopes()) == 2
    _, started = _reservation(reopened)
    assert started["reservation_status"] == "STARTED"
    assert started["assigned_operation_id"] == reserved["assigned_operation_id"]
    assert len(started["evaluations"]) == 1
    print(json.dumps({"boundary": boundary, "assigned_step_id": first["step_id"],
                      "evaluation_id": first["evaluation_record"]["evaluation_id"],
                      "evaluator_calls": len(calls), "reopen": "PASS"}))


def test_r01_concurrent_reserved_duplicates_have_one_evaluator(
    isolated_agentpro_storage, monkeypatch,
):
    request = _request(suffix="R01-CONCURRENT", text=_accept_text())
    source = TestClient(app).post("/agent/step", json=request).json()["evaluation_record"]
    body = _reevaluate(request, source, identity="stable-r01-concurrent")
    _reserve_with_interruption(monkeypatch, body, "after_assignment")
    _, reserved = _reservation(_repo_for(request))
    barrier = threading.Barrier(2)
    original_claim = ProjectRepository.claim_reevaluation_request
    quality = executor.TOOLS["QUALITY"]
    calls = []

    def claim(self, **kwargs):
        value = original_claim(self, **kwargs)
        barrier.wait(timeout=30)
        return value

    def counted(payload):
        calls.append(1)
        return quality(payload)

    monkeypatch.setitem(executor.TOOLS, "QUALITY", counted)
    with monkeypatch.context() as injection:
        injection.setattr(ProjectRepository, "claim_reevaluation_request", claim)
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda _: TestClient(app).post("/agent/step", json=body), range(2)))
    assert all(r.status_code in {200, 409} for r in responses)
    assert any(r.status_code == 200 for r in responses)
    replay = TestClient(app).post("/agent/step", json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["step_id"] == reserved["assigned_step_id"]
    assert calls == [1]
    envelopes = _repo_for(request).list_evaluation_envelopes()
    assert len(envelopes) == 2
    assert all(len(item["attempts"]) == 1 for item in envelopes)


@pytest.mark.parametrize("damage", ["input", "context", "unknown_state", "legacy_empty", "ids", "json"])
def test_r01_unprovable_reservation_records_intervention_without_evaluation(
    isolated_agentpro_storage, monkeypatch, damage,
):
    request = _request(suffix="R01-DAMAGE-" + damage, text=_accept_text())
    source = TestClient(app).post("/agent/step", json=request).json()["evaluation_record"]
    body = _reevaluate(request, source, identity="stable-r01-unprovable")
    _reserve_with_interruption(monkeypatch, body, "after_context")
    repo = _repo_for(request)
    key, reserved = _reservation(repo)
    with repo.connect() as connection:
        if damage == "input":
            connection.execute("DELETE FROM project_metadata WHERE key=?",
                               ("p20_execution_input.v1:" + reserved["assigned_operation_id"],))
        elif damage == "context":
            connection.execute("DELETE FROM context_packages WHERE operation_id=?",
                               (next(iter(reserved["contexts"])),))
        else:
            if damage == "unknown_state":
                reserved["reservation_status"] = "UNKNOWN"
            elif damage == "legacy_empty":
                reserved.pop("reservation_status")
            elif damage == "ids":
                reserved.pop("assigned_step_id")
            connection.execute("UPDATE project_metadata SET value=? WHERE key=?",
                               ("{" if damage == "json" else json.dumps(reserved), key))
    monkeypatch.setitem(executor.TOOLS, "QUALITY", lambda _: pytest.fail("unprovable binding evaluated"))
    for _ in range(2):
        response = TestClient(app).post("/agent/step", json=body)
        assert response.status_code == 422, response.text
        assert "REEVALUATION_REQUEST_NEEDS_INTERVENTION" in response.json()["detail"]
        reopened = _repo_for(request)
        assert len(reopened.list_evaluation_envelopes()) == 1
        _, state = _reservation(reopened)
        assert state["reservation_status"] == "NEEDS_INTERVENTION"
        assert state["intervention_reason"]
        assert state["audit"][-1]["event"] == "NEEDS_INTERVENTION"


def test_r01_start_commit_is_recoverable_and_fenced(
    isolated_agentpro_storage, monkeypatch, tmp_path,
):
    request = _request(suffix="R01-START", text=_accept_text())
    source = TestClient(app).post("/agent/step", json=request).json()["evaluation_record"]
    body = _reevaluate(request, source, identity="stable-r01-start")
    _reserve_with_interruption(monkeypatch, body, "after_start_commit")
    repo = _repo_for(request)
    running = next(item for item in repo.list_evaluation_envelopes()
                   if item["evaluation_id"] != source["evaluation_id"])
    old_token = running["attempts"][-1]["attempt_token"]
    _, state = _reservation(repo)
    assert state["reservation_status"] == "STARTED"
    assert state["evaluations"][running["operation_id"]] == running["evaluation_id"]
    quality = executor.TOOLS["QUALITY"]
    calls = []

    def counted(payload):
        calls.append(1)
        return quality(payload)

    monkeypatch.setitem(executor.TOOLS, "QUALITY", counted)
    for _ in range(2):
        response = TestClient(app).post("/agent/step", json=body)
        assert response.status_code == 409, response.text
        assert "EVALUATION_IN_PROGRESS" in response.json()["detail"]
    assert calls == []
    refused = _recover(TestClient(app), repo, running)
    assert refused.status_code in {401, 403}
    client = _operator_client(tmp_path)
    recovered = _recover(client, repo, running)
    assert recovered.status_code == 200, recovered.text
    after = _repo_for(request).get_evaluation_envelope(running["operation_id"])
    assert after["evaluation_id"] == running["evaluation_id"]
    assert after["binding"] == running["binding"]
    assert after["attempts"][-1]["attempt_token"] != old_token
    with pytest.raises(ev.EvaluationAttemptFenced):
        ev.finalize_evaluation(repo, ev.EvaluationBinding.from_dict(running["binding"]),
                               attempt_token=old_token, decision="ACCEPT")
    result = TestClient(app).post("/agent/step", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["evaluation_record"]["evaluation_id"] == running["evaluation_id"]
    assert calls == [1]


def test_r01_legacy_completed_reservation_keeps_deduplication(
    isolated_agentpro_storage, monkeypatch,
):
    request = _request(suffix="R01-LEGACY", text=_accept_text())
    source = TestClient(app).post("/agent/step", json=request).json()["evaluation_record"]
    body = _reevaluate(request, source, identity="stable-r01-legacy")
    first = TestClient(app).post("/agent/step", json=body)
    assert first.status_code == 200, first.text
    repo = _repo_for(request)
    key, reserved = _reservation(repo)
    for field in ("reservation_status", "input_hash", "contexts", "evaluations", "audit"):
        reserved.pop(field)
    repo.set_metadata(key, json.dumps(reserved))
    monkeypatch.setitem(executor.TOOLS, "QUALITY", lambda _: pytest.fail("legacy completed evaluated again"))
    response = TestClient(app).post("/agent/step", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["evaluation_record"] == first.json()["evaluation_record"]
    assert len(_repo_for(request).list_evaluation_envelopes()) == 2


@pytest.mark.parametrize("state", ["completed", "running"])
@pytest.mark.parametrize("entry", ["technical", "operator"])
def test_r01_legacy_reentry_preserves_complete_reservation_binding(
    isolated_agentpro_storage, monkeypatch, tmp_path, state, entry,
):
    request = _request(suffix="R01-LEGACY-" + state + "-" + entry, text=_accept_text())
    client = TestClient(app)
    source = client.post("/agent/step", json=request).json()["evaluation_record"]
    body = _reevaluate(request, source, identity="stable-legacy-transition")
    if state == "running":
        _reserve_with_interruption(monkeypatch, body, "after_start_commit")
    else:
        assert client.post("/agent/step", json=body).status_code == 200
    repo = _repo_for(request)
    key, legacy = _reservation(repo)
    for field in ("reservation_status", "input_hash", "contexts", "evaluations", "audit"):
        legacy.pop(field)
    repo.set_metadata(key, json.dumps(legacy))
    envelope = next(item for item in repo.list_evaluation_envelopes()
                    if item["evaluation_id"] != source["evaluation_id"])
    retry_body = {**request, "step_id": legacy["assigned_step_id"], "technical_retry": True}
    quality = executor.TOOLS["QUALITY"]
    calls = []

    def counted(payload):
        calls.append(1)
        return quality(payload)

    monkeypatch.setitem(executor.TOOLS, "QUALITY", counted)
    if entry == "operator":
        result = _recover(_operator_client(tmp_path), repo, envelope)
        assert result.status_code == 200, result.text
        # Recovery/read alone must not write an incomplete STARTED manifest.
        assert json.loads(repo.get_metadata(key)) == legacy
    if state == "running" and entry == "technical":
        blocked = client.post("/agent/step", json=retry_body)
        assert blocked.status_code == 409, blocked.text
        assert "EVALUATION_IN_PROGRESS" in blocked.json()["detail"]
        assert calls == []
        recovered = _recover(_operator_client(tmp_path), repo, envelope)
        assert recovered.status_code == 200, recovered.text
    technical = client.post("/agent/step", json=retry_body)
    assert technical.status_code == 200, technical.text
    after = _repo_for(request)
    _, initialized = _reservation(after)
    assert initialized["reservation_status"] == "STARTED"
    assert initialized["contexts"][envelope["operation_id"]] == envelope["binding"]["context_hash"]
    assert initialized["evaluations"][envelope["operation_id"]] == envelope["evaluation_id"]
    for _ in range(2):
        keyed = client.post("/agent/step", json=body)
        assert keyed.status_code == 200, keyed.text
        assert keyed.json()["evaluation_record"] == technical.json()["evaluation_record"]
    assert technical.json()["evaluation_record"]["evaluation_id"] == envelope["evaluation_id"]
    assert len(after.list_evaluation_envelopes()) == 2
    assert len(calls) == (1 if state == "running" else 0)


def test_r01_explicit_retry_cannot_start_reserved_changed_request(
    isolated_agentpro_storage, monkeypatch,
):
    request = _request(suffix="R01-RESERVED-RETRY", text=_accept_text())
    client = TestClient(app)
    source = client.post("/agent/step", json=request).json()["evaluation_record"]
    body = _reevaluate(request, source, identity="stable-unstarted-request")
    _reserve_with_interruption(monkeypatch, body, "after_assignment")
    _, reserved = _reservation(_repo_for(request))
    changed = {**request, "step_id": reserved["assigned_step_id"], "technical_retry": True,
               "payload": {"text": "unbound changed text"}}
    with monkeypatch.context() as guard:
        guard.setitem(executor.TOOLS, "QUALITY", lambda _: pytest.fail("unbound retry started"))
        rejected = client.post("/agent/step", json=changed)
        assert rejected.status_code == 409, rejected.text
        assert len(_repo_for(request).list_evaluation_envelopes()) == 1
    first = client.post("/agent/step", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["step_id"] == reserved["assigned_step_id"]
    assert first.json()["evaluation_record"]["reevaluation_of"] == source["evaluation_id"]


def test_reevaluation_request_identity_conflict_precedes_evaluator(
    isolated_agentpro_storage, monkeypatch
):
    request = _request(suffix="REQUEST-ID-CONFLICT", text=_accept_text())
    source = TestClient(app).post("/agent/step", json=request).json()["evaluation_record"]
    calls = 0
    original = executor.TOOLS["QUALITY"]

    def counted(payload):
        nonlocal calls
        calls += 1
        return original(payload)

    monkeypatch.setitem(executor.TOOLS, "QUALITY", counted)
    body = _reevaluate(request, source, identity="stable-reevaluation-request-conflict")
    first = TestClient(app).post("/agent/step", json=body)
    assert first.status_code == 200, first.text
    changed = copy.deepcopy(body)
    changed["payload"]["text"] += " changed"
    conflict = TestClient(app).post("/agent/step", json=changed)
    assert conflict.status_code == 409, conflict.text
    assert "already bound to different inputs" in conflict.json()["detail"]
    assert calls == 1
    assert len(_repo_for(request).list_evaluation_envelopes()) == 2


def test_reevaluation_without_identity_and_with_distinct_identities_are_distinct(
    isolated_agentpro_storage,
):
    request = _request(suffix="REQUEST-ID-DISTINCT", text=_accept_text())
    source = TestClient(app).post("/agent/step", json=request).json()["evaluation_record"]
    bodies = [
        _reevaluate(request, source),
        _reevaluate(request, source),
        _reevaluate(request, source, identity="stable-reevaluation-request-a"),
        _reevaluate(request, source, identity="stable-reevaluation-request-b"),
    ]
    results = [TestClient(app).post("/agent/step", json=body) for body in bodies]
    assert all(result.status_code == 200 for result in results), [
        result.text for result in results
    ]
    records = [result.json()["evaluation_record"] for result in results]
    assert len({record["evaluation_id"] for record in records}) == 4
    assert all(record["reevaluation_of"] == source["evaluation_id"] for record in records)


def test_reevaluation_request_identity_is_isolated_by_project_and_run(
    isolated_agentpro_storage,
):
    project_a = _request(suffix="REQUEST-ID-SCOPE-A", text=_accept_text())
    project_b = _request(suffix="REQUEST-ID-SCOPE-B", text=_accept_text())
    run_b = _request(suffix="REQUEST-ID-SCOPE-RUN-B", text=_accept_text())
    run_b["project_id"] = project_a["project_id"]
    run_b["book_id"] = project_a["book_id"]
    requests = [project_a, project_b, run_b]
    sources = [
        TestClient(app).post("/agent/step", json=request).json()["evaluation_record"]
        for request in requests
    ]
    results = [
        TestClient(app).post(
            "/agent/step",
            json=_reevaluate(
                request, source, identity="shared-stable-reevaluation-request"
            ),
        )
        for request, source in zip(requests, sources)
    ]
    assert all(result.status_code == 200 for result in results)
    records = [result.json()["evaluation_record"] for result in results]
    assert len({record["evaluation_id"] for record in records}) == 3
    assert records[0]["project_id"] != records[1]["project_id"]
    assert records[0]["project_id"] == records[2]["project_id"]
    assert records[0]["run_id"] != records[2]["run_id"]
    assert [record["reevaluation_of"] for record in records] == [
        source["evaluation_id"] for source in sources
    ]


def test_intervention_cannot_be_cleared_by_recovery(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    ev.start_evaluation(repo, item)
    with repo.evaluation_transaction(item.operation_id) as state:
        state["recovery_status"] = "NEEDS_INTERVENTION"
        state["execution_status"] = "INTERRUPTED"
    with pytest.raises(ev.EvaluationNeedsIntervention):
        ev.recover_evaluation(repository(isolated_agentpro_storage), item, recovered_by="OPERATOR-test")


def test_duplicate_recovery_does_not_fence_new_unclaimed_attempt(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    ev.start_evaluation(repo, item)
    first = ev.recover_evaluation(repo, item, recovered_by="OPERATOR-test")
    second = ev.recover_evaluation(repository(isolated_agentpro_storage), item, recovered_by="OPERATOR-test")
    assert first.attempt_token == second.attempt_token
    assert len(repo.get_evaluation_envelope(item.operation_id)["attempts"]) == 2


def test_duplicate_recovery_does_not_fence_claimed_attempt(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    ev.start_evaluation(repo, item)
    recovered = ev.recover_evaluation(repo, item, recovered_by="OPERATOR-test")
    ev.start_evaluation(repo, item, claim_recovery=True)
    duplicate = ev.recover_evaluation(repository(isolated_agentpro_storage), item, recovered_by="OPERATOR-test")
    assert duplicate.attempt_token == recovered.attempt_token
    assert len(repo.get_evaluation_envelope(item.operation_id)["attempts"]) == 2
    next_attempt = ev.recover_evaluation(repository(isolated_agentpro_storage), item,
        recovered_by="OPERATOR-test", expected_attempt_token=recovered.attempt_token)
    assert next_attempt.attempt_token != recovered.attempt_token
    assert next_attempt.evaluation_id == recovered.evaluation_id
    with pytest.raises(ev.EvaluationAttemptFenced):
        ev.recover_evaluation(repository(isolated_agentpro_storage), item,
            recovered_by="OPERATOR-test", expected_attempt_token=recovered.attempt_token)


def test_corrupt_output_rejected_by_every_read(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    complete(repo, item)
    with repo.evaluation_transaction(item.operation_id) as state:
        state["output"] = {"payload": {"DECISION": "REJECT"}}
    with pytest.raises(ev.EvaluationIntegrityError):
        ev.load_completed_evaluation(repository(isolated_agentpro_storage), item)


def test_quality_preserves_exact_whitespace_artifact_binding(isolated_agentpro_storage):
    request = _request(suffix="WHITESPACE", text="  " + _accept_text() + "  \n")
    response = TestClient(app).post("/agent/step", json=request)
    assert response.status_code == 200, response.text
    record = response.json()["evaluation_record"]
    assert record is not None
    assert record["artifact_hash"] == sha(request["payload"]["text"])


def test_same_bytes_rewrite_is_new_artifact_not_old_accept(isolated_agentpro_storage, monkeypatch):
    monkeypatch.setitem(executor.TOOLS, "REWRITE", lambda p: {"tool": "REWRITE", "payload": {"text": p["text"]}})
    request = _request(suffix="SAMEBYTES", text=_accept_text(), modes=["QUALITY", "REWRITE"])
    response = TestClient(app).post("/agent/step", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["quality_gate"]["decision"] == "REVISE"
    assert response.json()["evaluation_record"] is None


def test_completed_writer_is_not_reexecuted_by_quality_retry(isolated_agentpro_storage, monkeypatch):
    calls = []
    def writer(p):
        calls.append(1)
        return {"tool": "REWRITE", "payload": {"text": _accept_text() + str(len(calls))}}
    monkeypatch.setitem(executor.TOOLS, "REWRITE", writer)
    request = _request(suffix="WRITERRETRY", text=_accept_text(), modes=["REWRITE", "QUALITY"])
    first = TestClient(app).post("/agent/step", json=request)
    assert first.status_code == 200, first.text
    retried = TestClient(app).post("/agent/step", json={**request, "technical_retry": True})
    assert retried.status_code == 200, retried.text
    assert len(calls) == 1
    assert retried.json()["evaluation_record"] == first.json()["evaluation_record"]


@pytest.mark.parametrize("boundary", ["A", "B", "C", "D", "E", "F", "G", "H_actual_after_audit"])
def test_api_crash_matrix(isolated_agentpro_storage, tmp_path, monkeypatch, boundary):
    request = _request(suffix="CRASH-" + boundary, text=_accept_text())
    calls = []
    quality = executor.TOOLS["QUALITY"]

    def counted(payload):
        calls.append(1)
        if boundary == "C" and len(calls) == 1:
            raise RuntimeError("phase4 injected crash")
        return quality(payload)

    monkeypatch.setitem(executor.TOOLS, "QUALITY", counted)
    owner, name = {
        "A": (executor, "start_evaluation"), "B": (executor, "start_evaluation"),
        "C": (executor, "finalize_evaluation"), "D": (executor, "finalize_evaluation"),
        "E": (executor, "_atomic_write_json"), "F": (runtime, "save_run_state"),
        "G": (runtime, "write_audit"), "H_actual_after_audit": (runtime, "write_audit"),
    }[boundary]
    original = getattr(owner, name)
    fired = []

    def crash(*args, **kwargs):
        applies = not fired and boundary != "C"
        if boundary == "E":
            applies = applies and isinstance(args[1], dict) and args[1].get("mode") == "QUALITY"
        if applies:
            fired.append(1)
            if boundary in {"B", "H_actual_after_audit"}:
                original(*args, **kwargs)
            raise RuntimeError("phase4 injected crash")
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, crash)
    with pytest.raises(RuntimeError, match="phase4 injected crash"):
        TestClient(app).post("/agent/step", json=request)
    monkeypatch.setattr(owner, name, original)
    repo = _repo_for(request)  # Fresh repository, every SQLite connection is closed by its context manager.
    envelopes = repo.list_evaluation_envelopes()
    if boundary == "A":
        assert envelopes == []
    elif boundary in {"B", "C", "D"}:
        assert envelopes[0]["execution_status"] == "RUNNING"
        assert envelopes[0]["decision"] is None
        blocked = TestClient(app).post("/agent/step", json={**request, "technical_retry": True})
        assert blocked.status_code == 409
        response = _recover(_operator_client(tmp_path), repo, envelopes[0])
        assert response.status_code == 200, response.text
        assert response.json()["evaluation_id"] == envelopes[0]["evaluation_id"]
    else:
        assert envelopes[0]["execution_status"] == "COMPLETED"
    result = _retry(request)
    assert result["evaluation_record"]["decision"] == "ACCEPT"
    assert len(_repo_for(request).list_evaluation_envelopes()) == 1
    assert len(calls) == (2 if boundary in {"C", "D"} else 1)
    for relative in (result["artifact_paths"][-1],
                     f"runs/{request['run_id']}/run_state.json", f"runs/{request['run_id']}/audit.json"):
        assert _public_json(relative)["evaluation_record"] == result["evaluation_record"]


@pytest.mark.parametrize("status", ["PREPARED", "RUNNING", "INTERRUPTED", "FAILED", "COMPLETED"])
def test_operator_recovery_states_reopen_and_fencing(isolated_agentpro_storage, tmp_path, status):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    ensure_system_repository().bind_project(repo.context.project_id, repo.context.book_id)
    old_token = None
    if status == "COMPLETED":
        complete(repo, item)
    elif status == "PREPARED":
        ev.prepare_evaluation(repo, item)
    else:
        old_token = ev.start_evaluation(repo, item).attempt_token
        if status != "RUNNING":
            with repo.evaluation_transaction(item.operation_id) as state:
                state["execution_status"] = status
    envelope = repo.get_evaluation_envelope(item.operation_id)
    client = _operator_client(tmp_path)
    response = _recover(client, repository(isolated_agentpro_storage), envelope)
    if status == "FAILED":
        assert response.status_code == 422
        assert repo.get_evaluation_envelope(item.operation_id)["decision"] is None
        return
    assert response.status_code == 200, response.text
    assert response.json()["evaluation_id"] == envelope["evaluation_id"]
    if status == "COMPLETED":
        assert response.json()["reused"] is True
        assert response.json()["attempt_token"] is None
        return
    new_token = response.json()["attempt_token"]
    assert new_token and new_token != old_token
    if old_token:
        with pytest.raises(ev.EvaluationAttemptFenced):
            ev.finalize_evaluation(repository(isolated_agentpro_storage), item,
                                  attempt_token=old_token, decision="ACCEPT")
    claimed = ev.start_evaluation(repository(isolated_agentpro_storage), item, claim_recovery=True)
    assert claimed.attempt_token == new_token
    result = ev.finalize_evaluation(repository(isolated_agentpro_storage), item,
                                    attempt_token=new_token, decision="REVISE")
    assert result.evaluation_id == envelope["evaluation_id"]
    assert result.reevaluation_of is None
    assert ev.start_evaluation(repository(isolated_agentpro_storage), item).record == result


@pytest.mark.parametrize("damage", ["record", "record_hash", "artifact_hash", "context_hash", "schema", "binding"])
def test_corruption_recovery_is_fail_closed(isolated_agentpro_storage, damage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    complete(repo, item)
    with repo.evaluation_transaction(item.operation_id) as state:
        if damage == "record":
            state["record"] = None
        elif damage == "record_hash":
            state["record"]["record_hash"] = "0" * 64
        elif damage in {"artifact_hash", "context_hash"}:
            state["binding"][damage] = "0" * 64
        elif damage == "schema":
            state["schema_version"] = 99
        else:
            state["binding"].pop("context_hash")
    before = repo.get_evaluation_envelope(item.operation_id)
    with pytest.raises((ev.EvaluationIntegrityError, ev.EvaluationNeedsIntervention)):
        ev.recover_evaluation_by_identity(repository(isolated_agentpro_storage),
            operation_id=item.operation_id, evaluation_id=before["evaluation_id"],
            run_id=item.run_id, step_id=item.step_id, recovered_by="OPERATOR-neutral")
    after = repo.get_evaluation_envelope(item.operation_id)
    assert len(after["attempts"]) == 1
    if damage in {"artifact_hash", "context_hash", "schema", "binding"}:
        assert after["recovery_status"] == "NEEDS_INTERVENTION"
        assert after["decision"] is None


def test_operator_recovery_authorization_identity_and_duplicates(isolated_agentpro_storage, tmp_path):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    ensure_system_repository().bind_project(repo.context.project_id, repo.context.book_id)
    ev.start_evaluation(repo, item)
    state = repo.get_evaluation_envelope(item.operation_id)
    authorized = _operator_client(tmp_path)
    url = f"/operator/projects/{item.project_id}/evaluations/{item.operation_id}/recover"
    body = {k: state[k] for k in ("evaluation_id", "run_id", "step_id")}
    anonymous = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50002))
    assert anonymous.post(url, json=body).status_code == 401
    assert anonymous.post(url, json=body, headers={"Authorization": "Bearer synthetic-invalid"}).status_code == 401
    for name in ("evaluation_id", "run_id", "step_id"):
        assert authorized.post(url, json={**body, name: "foreign-neutral"}).status_code == 409
    assert authorized.post(url.replace(item.project_id, "PROJ-foreign", 1), json=body).status_code == 403
    barrier = threading.Barrier(2)
    def recover(_):
        barrier.wait()
        return authorized.post(url, json=body)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(recover, range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json()["attempt_token"] == results[1].json()["attempt_token"]
    assert len(repository(isolated_agentpro_storage).get_evaluation_envelope(item.operation_id)["attempts"]) == 2
    token = results[0].json()["attempt_token"]
    ev.start_evaluation(repo, item, claim_recovery=True)
    assert authorized.post(url, json=body).json()["attempt_token"] == token
    fenced_body = {**body, "expected_attempt_token": token}
    renewed = authorized.post(url, json=fenced_body)
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["attempt_token"] != token
    assert authorized.post(url, json=fenced_body).status_code == 409


@pytest.mark.parametrize("field", ["artifact_hash", "context_hash", "criteria_hash"])
def test_concurrent_binding_conflict(isolated_agentpro_storage, field):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    complete(repo, item)
    changed = replace(item, **{field: "e" * 64})
    barrier = threading.Barrier(2)
    def retry(candidate):
        barrier.wait()
        try:
            return ev.start_evaluation(repository(isolated_agentpro_storage), candidate).reused
        except ev.EvaluationConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(retry, (item, changed)))
    assert results == [True, "CONFLICT"]
    assert len(repo.get_evaluation_envelope(item.operation_id)["attempts"]) == 1


def test_concurrent_recovery_and_late_finalization(isolated_agentpro_storage):
    repo = repository(isolated_agentpro_storage)
    item = binding(repo)
    started = ev.start_evaluation(repo, item)
    barrier = threading.Barrier(2)
    def recover(_):
        barrier.wait()
        return ev.recover_evaluation(repository(isolated_agentpro_storage), item, recovered_by="OPERATOR-neutral")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(recover, range(2)))
    assert results[0].attempt_token == results[1].attempt_token
    assert len(repo.get_evaluation_envelope(item.operation_id)["attempts"]) == 2
    with ThreadPoolExecutor(2) as pool:
        late = pool.submit(ev.finalize_evaluation, repository(isolated_agentpro_storage), item,
                           attempt_token=started.attempt_token, decision="ACCEPT")
        active = pool.submit(ev.finalize_evaluation, repository(isolated_agentpro_storage), item,
                             attempt_token=results[0].attempt_token, decision="REVISE")
        with pytest.raises(ev.EvaluationAttemptFenced):
            late.result()
        assert active.result().decision == "REVISE"


@pytest.mark.parametrize("kind", ["two_retry", "retry_normal", "two_reevaluate", "reevaluate_retry"])
def test_concurrent_api_requests(isolated_agentpro_storage, kind):
    request = _request(suffix="CONCURRENT-" + kind, text=_accept_text())
    initial = TestClient(app).post("/agent/step", json=request).json()
    old = initial["evaluation_record"]
    retry = {**request, "technical_retry": True}
    reevaluate = {**request, "evaluation_intent": "REEVALUATE", "reevaluation_of": old["evaluation_id"]}
    requests = {"two_retry": [retry, retry], "retry_normal": [retry, request],
                "two_reevaluate": [reevaluate, reevaluate], "reevaluate_retry": [reevaluate, retry]}[kind]
    barrier = threading.Barrier(2)
    def post(body):
        barrier.wait()
        return TestClient(app).post("/agent/step", json=body)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(post, requests))
    assert all(r.status_code in {200, 409} for r in results), [r.text for r in results]
    assert any(r.status_code == 200 for r in results)
    envelopes = _repo_for(request).list_evaluation_envelopes()
    assert len({e["operation_id"] for e in envelopes}) == len(envelopes)
    assert all(len(e["attempts"]) == 1 for e in envelopes)
    for result in results:
        if result.status_code == 200:
            record = result.json()["evaluation_record"]
            if record["evaluation_id"] != old["evaluation_id"]:
                assert record["reevaluation_of"] == old["evaluation_id"]
                assert record["operation_id"] != old["operation_id"]


@pytest.mark.parametrize("projection", ["step", "run_state", "audit"])
@pytest.mark.parametrize("damage", ["missing", "conflict"])
def test_projection_replay_or_conflict(isolated_agentpro_storage, projection, damage):
    request = _request(suffix="PROJECTION", text=_accept_text())
    first = TestClient(app).post("/agent/step", json=request).json()
    relative = (first["artifact_paths"][0] if projection == "step" else
                f"runs/{request['run_id']}/{projection}.json")
    path = get_storage_root() / relative
    if damage == "missing":
        path.unlink()  # Only the isolated synthetic projection.
        replay = _retry(request)
        assert replay["evaluation_record"] == first["evaluation_record"]
        assert _public_json(relative)["evaluation_record"] == first["evaluation_record"]
    else:
        document = _public_json(relative)
        document["evaluation_record"]["artifact_hash"] = "a" * 64
        path.write_text(json.dumps(document), encoding="utf-8")
        response = TestClient(app, raise_server_exceptions=False).post(
            "/agent/step", json={**request, "technical_retry": True})
        assert response.status_code in {409, 422}, response.text
        assert _public_json(relative) == document
    assert len(_repo_for(request).list_evaluation_envelopes()) == 1


def _style_request(monkeypatch, suffix="STYLE"):
    from tests.test_gap014_adaptive_style import dna, _adaptive_payload
    from app.p20_core.adaptive_style import StyleRepository
    from app.p20_core.book_bible_test_helper import ensure_test_book_bible
    request = _request(suffix=suffix, text=_accept_text(), modes=["WRITE", "QUALITY"])
    ensure_test_book_bible(request["book_id"])
    repo = _repo_for(request)
    StyleRepository(repo).save_book_style_dna(dna(repo.context.book_id))
    request["payload"]["adaptive_style"] = _adaptive_payload(request["project_id"])
    monkeypatch.setitem(executor.TOOLS, "WRITE", lambda p: {"tool": "WRITE", "payload": {
        "text": p["text"], "meta": {"style_achieved_genome": p["style_features"]["target_genome"]}}})
    return request


@pytest.mark.parametrize("boundary", ["I", "J", "chapter_before_audit"])
def test_style_downstream_crashes_are_idempotent(isolated_agentpro_storage, monkeypatch, boundary):
    from app.p20_core.adaptive_style import AdaptiveStyleSession, StyleRepository
    from app.p20_core import canon_service
    request = _style_request(monkeypatch)
    calls = []
    quality = executor.TOOLS["QUALITY"]
    def counted(p):
        calls.append(1)
        return quality(p)
    monkeypatch.setitem(executor.TOOLS, "QUALITY", counted)
    owner, name = {"I": (AdaptiveStyleSession, "finalize"),
                   "J": (canon_service, "process_accepted_artifact"),
                   "chapter_before_audit": (runtime, "persist_chapter_lineage")}[boundary]
    original = getattr(owner, name)
    def crash(*args, **kwargs):
        raise RuntimeError("phase4 style crash")
    monkeypatch.setattr(owner, name, crash)
    with pytest.raises(RuntimeError, match="phase4 style crash"):
        TestClient(app).post("/agent/step", json=request)
    repo = _repo_for(request)
    assert repo.list_evaluation_envelopes()[0]["decision"] == "ACCEPT"
    assert len(StyleRepository(repo).list_performance()) == (0 if boundary == "I" else 1)
    monkeypatch.setattr(owner, name, original)
    result = _retry(request)
    again = _retry(request)
    assert len(calls) == 1
    assert len(StyleRepository(_repo_for(request)).list_performance()) == 1
    assert again["evaluation_record"] == result["evaluation_record"]
    assert again["chapter_path"] == result["chapter_path"]
    chapter = _public_json(result["chapter_path"])
    assert chapter["quality_evaluation"]["evaluation_id"] == result["evaluation_record"]["evaluation_id"]
    assert chapter["artifact_hash"] == result["evaluation_record"]["artifact_hash"]
    assert len(_repo_for(request).list_structured_memory_records()) == 1


@pytest.mark.parametrize("decision", ["REVISE", "REJECT"])
def test_style_nonaccept_has_no_performance_after_reopen_retry(isolated_agentpro_storage, monkeypatch, decision):
    from app.p20_core.adaptive_style import StyleRepository
    request = _style_request(monkeypatch)
    monkeypatch.setitem(executor.TOOLS, "QUALITY", lambda p: {"tool": "QUALITY", "payload": {
        "DECISION": decision, "REASONS": ["neutral"], "MUST_FIX": [], "SCORE": 0.1}})
    result = TestClient(app).post("/agent/step", json=request)
    assert result.status_code == 200, result.text
    assert _retry(request)["evaluation_record"]["decision"] == decision
    assert StyleRepository(_repo_for(request)).list_performance() == ()
    assert _repo_for(request).list_structured_memory_records() == {}


def test_reevaluation_keeps_historical_performance_binding(isolated_agentpro_storage, monkeypatch):
    from app.p20_core.adaptive_style import StyleRepository, AdaptiveStyleSession
    request = _style_request(monkeypatch)
    first = TestClient(app).post("/agent/step", json=request).json()
    styles = StyleRepository(_repo_for(request))
    historical = styles.list_performance()[0]
    session = AdaptiveStyleSession(styles, styles.get_active_book_style_dna(),
                                   styles.get_recipe(historical.recipe_id))
    session.evaluate(artifact_id=historical.accepted_artifact_id, text=_accept_text(),
                     achieved_genome=historical.achieved_genome.to_dict())
    source_binding = ev.EvaluationBinding.from_dict(_repo_for(request).list_evaluation_envelopes()[0]["binding"])
    changed = replace(source_binding, operation_id=source_binding.operation_id + ":reevaluate",
                      step_id=source_binding.step_id + ":reevaluate", reevaluation_of=first["evaluation_record"]["evaluation_id"])
    reevaluated = complete(_repo_for(request), changed)
    reused = session.finalize(quality_decision="ACCEPT", quality_score=1.0,
        quality_evaluation_id=reevaluated.evaluation_id, quality_artifact_hash=historical.accepted_artifact_hash,
        artifact_id=historical.accepted_artifact_id, artifact_text=_accept_text())
    assert reused == historical
    assert StyleRepository(_repo_for(request)).list_performance() == (historical,)
    assert historical.quality_evaluation_id != reevaluated.evaluation_id
    response = TestClient(app).post("/agent/step", json={**request, "modes": ["QUALITY"],
        "evaluation_intent": "REEVALUATE", "reevaluation_of": first["evaluation_record"]["evaluation_id"]})
    assert response.status_code == 200, response.text
    assert response.json()["evaluation_record"]["evaluation_id"] != historical.quality_evaluation_id
    assert StyleRepository(_repo_for(request)).list_performance() == (historical,)
    replay = _retry({**request, "modes": ["QUALITY"], "step_id": response.json()["step_id"]})
    assert replay["evaluation_record"] == response.json()["evaluation_record"]


@pytest.mark.parametrize("first_decision", ["ACCEPT", "REVISE", "REJECT"])
def test_rewrite_quality_reopen_retry_and_reevaluation(isolated_agentpro_storage, monkeypatch, first_decision):
    text_a, text_b = _accept_text(), _accept_text() + "\nNeutral version B."
    monkeypatch.setitem(executor.TOOLS, "REWRITE", lambda p: {"tool": "REWRITE", "payload": {"text": text_b}})
    monkeypatch.setitem(executor.TOOLS, "QUALITY", lambda p: {"tool": "QUALITY", "payload": {
        "DECISION": first_decision if p["text"] == text_a else "ACCEPT", "SCORE": 0.9,
        "REASONS": [], "MUST_FIX": []}})
    request = _request(suffix="SEQUENCE-" + first_decision, text=text_a,
        modes=["QUALITY", "REWRITE"], steps=[{"mode": "QUALITY"}, {"mode": "REWRITE"}, {"mode": "QUALITY"}])
    first = TestClient(app).post("/agent/step", json=request)
    assert first.status_code == 200, first.text
    result = _retry(request)
    assert result["evaluation_record"]["artifact_hash"] == sha(text_b)
    assert result["evaluation_record"]["decision"] == "ACCEPT"
    assert all("__attempt_02" in p for p in result["artifact_paths"])
    history = _repo_for(request).list_evaluation_envelopes()
    assert {e["record"]["context_package_id"] for e in history}.__len__() == 2
    previous = next(e["record"] for e in history if e["record"]["artifact_hash"] == sha(text_a))
    assert previous["decision"] == first_decision
    for record in (previous, result["evaluation_record"]):
        response = TestClient(app).post("/agent/step", json={**request,
            "step_id": f"{request['step_id']}-reevaluate-{record['evaluation_id']}",
            "evaluation_intent": "REEVALUATE", "reevaluation_of": record["evaluation_id"]})
        assert response.status_code == 200, response.text
        assert response.json()["evaluation_record"]["reevaluation_of"] == record["evaluation_id"]
        assert _retry({**request, "step_id": response.json()["step_id"]})["evaluation_record"] == response.json()["evaluation_record"]


@pytest.mark.parametrize("separation", ["run", "project", "same_book"])
def test_concurrent_isolation_and_reopen(isolated_agentpro_storage, separation):
    first = _request(suffix="ISOLATE-A", text=_accept_text())
    second = _request(suffix="ISOLATE-B", text=_accept_text())
    if separation == "run":
        second.update(project_id=first["project_id"], book_id=first["book_id"])
    elif separation == "same_book":
        second["book_id"] = first["book_id"]
    barrier = threading.Barrier(2)
    def post(request):
        barrier.wait()
        return TestClient(app).post("/agent/step", json=request)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(post, (first, second)))
    if separation == "same_book":
        # Existing architecture permits one project owner per domain book.
        assert any(r.status_code == 422 for r in results), [r.text for r in results]
        return
    for request, response in zip((first, second), results):
        if response.status_code == 409:
            response = TestClient(app).post("/agent/step", json=request)
        assert response.status_code == 200, response.text
        record = response.json()["evaluation_record"]
        assert record["project_id"] == request["project_id"]
        assert record["run_id"] == request["run_id"]
        assert _retry(request)["evaluation_record"] == record


def test_retry_rejects_changed_queue(isolated_agentpro_storage):
    request = _request(suffix="QUEUE", text=_accept_text())
    assert TestClient(app).post("/agent/step", json=request).status_code == 200
    response = TestClient(app).post("/agent/step", json={**request,
        "modes": ["QUALITY", "QUALITY"], "technical_retry": True})
    assert response.status_code == 409
    assert len(_repo_for(request).list_evaluation_envelopes()) == 1


def test_retry_changed_input_before_projection_is_rejected(isolated_agentpro_storage, monkeypatch):
    request = _request(suffix="INPUT", text=_accept_text())
    original = executor._atomic_write_json
    def crash(path, data):
        if data.get("mode") == "QUALITY":
            raise RuntimeError("phase4 input crash")
        return original(path, data)
    monkeypatch.setattr(executor, "_atomic_write_json", crash)
    with pytest.raises(RuntimeError):
        TestClient(app).post("/agent/step", json=request)
    monkeypatch.setattr(executor, "_atomic_write_json", original)
    changed = {**request, "technical_retry": True,
               "payload": {**request["payload"], "scene_ref": "SCENE-different"}}
    response = TestClient(app).post("/agent/step", json=changed)
    assert response.status_code == 409


def test_accept_does_not_override_canon_check(isolated_agentpro_storage, monkeypatch):
    from app.p20_core.book_bible_test_helper import ensure_test_book_bible
    request = _request(suffix="CANON-BLOCK", text=_accept_text(), modes=["WRITE", "QUALITY"])
    ensure_test_book_bible(request["book_id"])
    monkeypatch.setitem(executor.TOOLS, "WRITE", lambda p: {"tool": "WRITE", "payload": {"text": p["text"] + " changed"}})
    original = runtime.run_canon_check
    def check(text, snapshot, scene):
        if text.endswith(" changed"):
            return {"ok": False, "issues": [{"severity": "ERROR", "code": "NEUTRAL_CONFLICT"}], "scene_ref": scene}
        return original(text, snapshot, scene)
    monkeypatch.setattr(runtime, "run_canon_check", check)
    response = TestClient(app).post("/agent/step", json=request)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evaluation_record"]["decision"] == "ACCEPT"
    assert body["decision"] != "ACCEPT"
    assert body["ok"] is False
    assert body["chapter_path"] is None
    assert body["canonical_change"] is None


def test_actual_lineage_boundary_precedes_audit(isolated_agentpro_storage, monkeypatch):
    request = _style_request(monkeypatch, suffix="ORDER")
    events = []
    chapter, audit = runtime.persist_chapter_lineage, runtime.write_audit
    def persist(**kwargs):
        result = chapter(**kwargs)
        events.append("CHAPTER_DURABLE")
        return result
    def log(**kwargs):
        result = audit(**kwargs)
        events.append("AUDIT_DURABLE")
        return result
    monkeypatch.setattr(runtime, "persist_chapter_lineage", persist)
    monkeypatch.setattr(runtime, "write_audit", log)
    response = TestClient(app).post("/agent/step", json=request)
    assert response.status_code == 200, response.text
    assert events == ["CHAPTER_DURABLE", "AUDIT_DURABLE"]


def test_chapter_projection_conflict_requires_intervention(isolated_agentpro_storage, monkeypatch):
    from app.p20_core.cross_store_recovery import RecoveryInterventionRequired
    request = _style_request(monkeypatch, suffix="CHAPTER-CONFLICT")
    response = TestClient(app).post("/agent/step", json=request)
    assert response.status_code == 200, response.text
    body = response.json()
    chapter_path = get_storage_root() / body["chapter_path"]
    document = _public_json(body["chapter_path"])
    document["quality_evaluation"]["evaluation_id"] = "EVAL-foreign-neutral"
    chapter_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RecoveryInterventionRequired):
        TestClient(app).post("/agent/step", json={**request, "technical_retry": True})
    repo = _repo_for(request)
    assert repo.get_cross_store_operation(body["chapter_lineage"]["operation_id"])["status"] == "NEEDS_INTERVENTION"
    assert len(repo.list_evaluation_envelopes()) == 1
    assert _public_json(body["chapter_path"]) == document


@pytest.mark.parametrize("blocker", ["approval", "verifier", "final_guard"])
def test_evaluation_accept_has_no_canonical_authority(pipeline, monkeypatch, blocker):
    from tests.test_canonical_pipeline import PROJECT, BOOK, FACT, fact, base, approve, commit
    client, repo, controls, headers, _, _ = pipeline
    with repo.domain_transaction() as tx:
        tx.add_structured_memory_record(fact(protected=True))
    controls.update(version=2, protected=True)
    if blocker == "verifier":
        controls["verdict"] = "REJECT"
    monkeypatch.setitem(executor.TOOLS, "WRITE", lambda p: {"tool": "WRITE", "payload": {"text": _accept_text()}})
    body = {"project_id": PROJECT, "book_id": BOOK, "run_id": "run-authority",
            "step_id": "step-authority", "modes": ["WRITE", "QUALITY"], "payload": {"text": _accept_text()}}
    response = client.post("/agent/step", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["evaluation_record"]["decision"] == "ACCEPT"
    change = result["canonical_change"]
    if blocker == "approval":
        assert change["status"] == "AWAITING_USER_APPROVAL"
        assert commit(client, change, headers).json()["status"] == "AWAITING_USER_APPROVAL"
        forged = client.post(base(change) + "/decision", headers=headers,
            json={"decision": "APPROVE", "evaluation_id": result["evaluation_record"]["evaluation_id"]})
        assert forged.status_code == 422
    elif blocker == "final_guard":
        approve(client, change, headers)
        with repo.domain_transaction() as tx:
            tx.add_structured_memory_record(fact(identity="FACT-concurrent-neutral"))
        assert commit(client, change, headers).json()["status"] == "STALE"
    else:
        assert change["status"] == "REJECT"
    assert json.loads(repo.list_structured_memory_records()[FACT])["version"] == 1
    retried = client.post("/agent/step", json={**body, "technical_retry": True})
    assert retried.status_code == 200, retried.text
    assert json.loads(repo.list_structured_memory_records()[FACT])["version"] == 1

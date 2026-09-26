from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.operator_dpapi import read_secret
from app.p20_core.local_operator import initialize_operator, rotate_operator, revoke_operator, OperatorError
from app.p20_core.project_repository import ensure_system_repository, ProjectRepository, StorageResolver
from app.p20_core.canon_service import save_canonical_proposal_for_review, canonical_proposal_hash
from app.p20_core.impact_analysis import analyze_impact, ImpactRequest


PROJECT = "PROJ-operator-test"
BOOK = "BOOK-operator-test"
PROPOSAL = "proposal-operator-test"
BASE = f"/operator/projects/{PROJECT}/proposals/{PROPOSAL}"


def seed_proposal(repository, *, proposal_id=PROPOSAL, version=1, value="synthetic value"):
    """Synthetic pending producer record, not a claim of F-002/F-006 integration."""
    proposal = dict(
        contract_version="1.0", proposal_id=proposal_id, project_id=PROJECT, book_id=BOOK,
        series_id=None, scope_type="PROJECT", scope_id=PROJECT, run_id="run-operator-test",
        step_id="step-operator-test", source_artifact_id="artifact-operator-test",
        source_artifact_ref="artifacts/synthetic-source.json", source_artifact_hash="a" * 64,
        source_artifact_version=1, source_scene_id="SCENE-operator-test",
        context_package_id="CONTEXT-operator-test", context_hash="b" * 64,
        extraction_candidate_set_id="candidate-operator-test", extraction_candidate_hash="c" * 64,
        verification_ref={"id": "verification-operator-test", "hash": "d" * 64},
        proposed_mutations=[dict(target_entity_type="FACT", target_entity_id="FACT-operator-test",
            operation_type="CREATE", expected_current_version=None, expected_current_hash=None,
            proposed_state={"fact_id": "FACT-operator-test", "version": 1, "frozen": True,
                            "author_locked": True, "object_value": value},
            provenance={"scene_id": "SCENE-operator-test", "candidate_id": "candidate-operator-test"})],
        source="AUTOMATION", actor_ref="synthetic-producer", authority_ref="policy-authority-v1",
        policy_ref="canonical-policy-v1", proposal_version=version, proposal_hash="",
        status="AWAITING_USER_APPROVAL", created_at="2026-09-15T12:00:00+00:00",
    )
    proposal["proposal_hash"] = canonical_proposal_hash(proposal)
    binding = {k: proposal[k] for k in ("proposal_id", "proposal_hash", "project_id", "scope_type", "scope_id")}
    impact = dict(binding, impact_id="impact-operator-test", result=analyze_impact(repository,
        ImpactRequest(PROJECT, "PROJECT", PROJECT, "FACT", "FACT-operator-test", "CREATE")).to_dict())
    save_canonical_proposal_for_review(repository, proposal, impact=impact,
        initial_guard=dict(binding, outcome="REQUIRE_USER_APPROVAL", source="synthetic-pending-producer"))
    return proposal


@pytest.fixture
def configured(isolated_agentpro_storage, tmp_path):
    if os.name != "nt":
        pytest.skip("Actual Windows DPAPI test")
    system = ensure_system_repository()
    system.bind_project(PROJECT, BOOK)
    repository = ProjectRepository(StorageResolver().resolve_project(PROJECT, book_id=BOOK))
    repository.initialize()
    secret_path = tmp_path / "operator-security" / "operator.dpapi"
    operator = initialize_operator(system, secret_path)
    token = read_secret(secret_path)
    proposal = seed_proposal(repository)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client, system, repository, secret_path, token, operator, proposal


def headers(token):
    return {"Authorization": "Bearer " + token}


def review_request(client, token, path=BASE):
    response = client.post(path + "/review", headers=headers(token))
    assert response.status_code == 200, response.text
    review = response.json()
    request = {k: review["proposal"][k] for k in ("proposal_hash", "scope_type", "scope_id")}
    request.update(challenge_id=review["challenge"]["challenge_id"], decision="APPROVE")
    return review, request


def test_unconfigured_never_bootstraps(isolated_agentpro_storage):
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
        response = client.get("/operator/identity")
        assert response.status_code == 503
        assert response.json()["detail"] == "OPERATOR_NOT_CONFIGURED"
    assert ensure_system_repository().get_metadata("local_operator.v1") is None


def test_real_dpapi_api_decision_reopen_retry_and_secret_safety(configured, caplog, capsys, isolated_agentpro_storage):
    client, system, repository, path, token, operator, proposal = configured
    assert token.encode() not in path.read_bytes()
    assert token not in json.dumps(system.list_metadata())
    assert client.get("/operator/identity", headers=headers(token)).json() == operator.to_dict()
    for auth in ({}, headers("invalid-token")):
        assert client.get(BASE, headers=auth).status_code == 401
        assert client.post(BASE + "/decision", headers=auth, json={"USER": "APPROVE", "approved": True}).status_code == 401
    preview = client.get(BASE, headers=headers(token)).json()
    assert preview["challenge"] is None
    assert preview["proposal"]["proposed_mutations"][0]["proposed_state"]["object_value"] == "synthetic value"
    review, request = review_request(client, token)
    response = client.post(BASE + "/decision", headers=headers(token), json=request)
    assert response.status_code == 200, response.text
    evidence = response.json()
    assert evidence["operator_id"] == operator.operator_id
    assert evidence["proposal_hash"] == proposal["proposal_hash"]
    assert evidence["canonical_commit"] is False
    assert repository.list_structured_memory_records() == {}
    reopened = ProjectRepository(StorageResolver().resolve_project(PROJECT, book_id=BOOK))
    saved = json.loads(reopened.get_metadata("canonical_proposal.v1:" + PROPOSAL))
    assert saved["versions"]["1"]["decision"] == evidence
    assert client.post(BASE + "/decision", headers=headers(token), json=request).json() == evidence
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as second:
        assert second.post(BASE + "/decision", headers=headers(token), json=request).json() == evidence
    assert token not in json.dumps(saved)
    assert token not in caplog.text + capsys.readouterr().out
    (isolated_agentpro_storage / "operator-api-proof.json").write_text(json.dumps({
        "review": review, "evidence": evidence, "reopened_authorization_ref": evidence["authorization_ref"],
        "replayed_without_duplicate": True, "canonical_commit": False}, indent=2), encoding="utf-8")


def test_forged_binding_identity_and_challenge_rejected(configured):
    client, _, repository, _, token, _, _ = configured
    _, request = review_request(client, token)
    for changes in ({"proposal_hash": "e" * 64}, {"scope_id": "PROJ-other"},
                    {"scope_type": "SERIES"}, {"challenge_id": "challenge-foreign"}):
        assert client.post(BASE + "/decision", headers=headers(token), json=dict(request, **changes)).status_code == 409
    for changes in ({"operator_id": "operator-forged"}, {"approved_by": "USER"},
                    {"authorization_ref": "forged"}, {"approved": True}):
        assert client.post(BASE + "/decision", headers=headers(token), json=dict(request, **changes)).status_code == 422
    assert client.get(BASE.replace(PROJECT, "PROJ-foreign"), headers=headers(token)).status_code == 403
    assert client.get(BASE.replace(PROPOSAL, "proposal-foreign"), headers=headers(token)).status_code == 404
    seed_proposal(repository, proposal_id="proposal-other")
    assert client.post(BASE.replace(PROPOSAL, "proposal-other") + "/decision", headers=headers(token), json=request).status_code == 409
    assert repository.list_structured_memory_records() == {}


def test_rotation_revocation_and_no_takeover(configured):
    client, system, repository, path, old_token, _, _ = configured
    _, request = review_request(client, old_token)
    with pytest.raises(OperatorError, match="OPERATOR_ALREADY_CONFIGURED"):
        initialize_operator(system, path.parent / "takeover.dpapi")
    identity = rotate_operator(system, path)
    new_token = read_secret(path)
    assert new_token != old_token and identity.credential_version == 2
    assert client.get("/operator/identity", headers=headers(old_token)).status_code == 401
    assert client.post(BASE + "/decision", headers=headers(new_token), json=request).status_code == 409
    _, fresh = review_request(client, new_token)
    evidence = client.post(BASE + "/decision", headers=headers(new_token), json=fresh).json()
    revoke_operator(system, path)
    assert client.get("/operator/identity", headers=headers(new_token)).status_code == 401
    assert client.post(BASE + "/decision", headers=headers(new_token), json=fresh).status_code == 401
    assert json.loads(repository.get_metadata("canonical_proposal.v1:" + PROPOSAL))["versions"]["1"]["decision"] == evidence
    with pytest.raises(OperatorError):
        rotate_operator(system, path)


def test_expired_challenge_and_user_reject(configured, monkeypatch):
    client, _, repository, _, token, _, _ = configured
    monkeypatch.setenv("AGENTPRO_OPERATOR_CHALLENGE_TTL_SECONDS", "1")
    _, request = review_request(client, token)
    time.sleep(1.05)
    assert client.post(BASE + "/decision", headers=headers(token), json=request).json()["detail"] == "DECISION_CHALLENGE_EXPIRED"
    _, fresh = review_request(client, token)
    fresh["decision"] = "REJECT"
    evidence = client.post(BASE + "/decision", headers=headers(token), json=fresh).json()
    assert evidence["decision"] == "REJECT"
    assert client.post(BASE + "/decision", headers=headers(token), json=fresh).json() == evidence
    assert client.post(BASE + "/decision", headers=headers(token), json=dict(fresh, decision="APPROVE")).status_code == 409
    assert repository.list_structured_memory_records() == {}


def test_changed_proposal_and_changed_basis_invalidate_challenge(configured):
    client, _, repository, _, token, _, _ = configured
    _, request = review_request(client, token)
    seed_proposal(repository, version=2, value="changed value")
    assert client.post(BASE + "/decision", headers=headers(token), json=request).status_code == 409
    _, new_request = review_request(client, token)
    # A real repository write changes the dependency snapshot, not a mocked validation.
    from app.p20_core.domain_records import EdgeRecord
    with repository.domain_transaction() as tx:
        tx.add_edge(EdgeRecord(edge_id="edge-new", scope_type="PROJECT", scope_id=PROJECT,
            source_type="SCENE", source_id="SCENE-operator-test", relation_type="REQUIRES",
            target_type="FACT", target_id="FACT-operator-test", valid_from=None, valid_to=None,
            confidence=1, source_ref="synthetic", version=1), source_scope=repository.scope, target_scope=repository.scope)
    response = client.post(BASE + "/decision", headers=headers(token), json=new_request)
    assert response.status_code == 409 and response.json()["detail"] == "STALE_PROPOSAL_ANALYSIS"


def test_remote_clients_and_rebinding_denied(configured):
    _, _, _, _, token, _, _ = configured
    with TestClient(app, base_url="http://127.0.0.1", client=("192.0.2.1", 1)) as remote:
        assert remote.get("/operator/identity", headers=headers(token)).status_code == 403
    with TestClient(app, base_url="http://untrusted.example", client=("127.0.0.1", 1)) as rebound:
        assert rebound.get("/operator/identity", headers=headers(token)).status_code == 403


def test_concurrent_identical_decisions_are_one_event(configured):
    client, _, repository, _, token, _, _ = configured
    _, request = review_request(client, token)
    def submit(_):
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as connection:
            response = connection.post(BASE + "/decision", headers=headers(token), json=request)
            assert response.status_code == 200, response.text
            return response.json()["authorization_ref"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert len(set(results)) == 1
    assert repository.list_structured_memory_records() == {}


def test_secret_not_forwarded_to_p20_context_provider_or_audit(configured, monkeypatch, isolated_agentpro_storage, caplog):
    client, _, repository, _, token, _, _ = configured
    from app.p20_core.book_bible_test_helper import ensure_test_book_bible
    import app.p20_core.executor as executor
    ensure_test_book_bible(BOOK)
    observed = []
    original = executor.TOOLS["WRITE"]
    def observed_provider(payload, *, model_call=None):
        observed.append(json.dumps(payload))
        return original(payload, model_call=model_call)
    monkeypatch.setitem(executor.TOOLS, "WRITE", observed_provider)
    response = client.post("/agent/step", headers=headers(token), json={
        "mode": "WRITE", "project_id": PROJECT, "book_id": BOOK,
        "run_id": "run-operator-secret-proof", "step_id": "step-operator-secret-proof",
        "payload": {"text": "A visitor reads a neutral notice."}})
    assert response.status_code == 200, response.text
    assert observed
    assert all(token not in entry for entry in observed)
    assert all("Authorization" not in entry for entry in observed)
    assert token not in caplog.text
    for package in repository.list_context_packages():
        assert token not in json.dumps(package.to_dict())
    for root in ("books", "runs", "audit"):
        for artifact in (isolated_agentpro_storage / root).rglob("*"):
            if artifact.is_file():
                assert token.encode() not in artifact.read_bytes()
    assert not any("operator" in name.lower() or "auth" in name.lower() for name in executor.TOOLS)


def test_real_cli_initialization_rotation_revocation_and_acl(isolated_agentpro_storage, tmp_path):
    import subprocess
    import sys
    path = tmp_path / "cli-security" / "operator.dpapi"
    outputs = []
    for command in ("init", "rotate", "revoke"):
        result = subprocess.run([sys.executable, "-m", "app.operator_cli", command,
                                 "--secret-path", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
        if command == "init":
            first = read_secret(path)
        elif command == "rotate":
            second = read_secret(path)
            assert first != second
    assert all(first not in output and second not in output for output in outputs)
    script = ("$a=[IO.File]::GetAccessControl($env:AGENTPRO_TEST_SECRET_FILE); "
              "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
              "$rules=$a.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]); "
              "if (@($rules).Count -ne 1 -or $rules[0].IdentityReference.Value -ne $sid) { exit 7 }; "
              "'ACL_CURRENT_USER_ONLY'")
    acl = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                         env=dict(os.environ, AGENTPRO_TEST_SECRET_FILE=str(path)), capture_output=True, text=True)
    assert acl.returncode == 0 and "ACL_CURRENT_USER_ONLY" in acl.stdout
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as client:
        assert client.get("/operator/identity", headers=headers(second)).status_code == 401


def test_no_secret_inside_project_and_invalid_security_config(configured, monkeypatch, isolated_agentpro_storage):
    client, _, _, _, token, _, _ = configured
    from app.operator_dpapi import write_secret
    with pytest.raises(RuntimeError, match="outside project"):
        write_secret(isolated_agentpro_storage / "forbidden" / "operator.dpapi", "synthetic")
    monkeypatch.setenv("AGENTPRO_OPERATOR_CHALLENGE_TTL_SECONDS", "not-an-integer")
    assert client.post(BASE + "/review", headers=headers(token)).status_code == 503


def test_foreign_series_proposal_rejected(configured):
    _, _, repository, _, _, _, proposal = configured
    proposal = dict(proposal, series_id="SERIES-foreign", proposal_version=2)
    proposal["proposal_hash"] = canonical_proposal_hash(proposal)
    with pytest.raises(OperatorError, match="SERIES_ACCESS_DENIED"):
        save_canonical_proposal_for_review(repository, proposal, impact={}, initial_guard={})
    proposal = dict(proposal, series_id="SERIES-foreign", scope_type="SERIES", scope_id="SERIES-foreign")
    proposal["proposal_hash"] = canonical_proposal_hash(proposal)
    with pytest.raises(OperatorError, match="PROPOSAL_SCOPE_MISMATCH"):
        save_canonical_proposal_for_review(repository, proposal, impact={}, initial_guard={})


def test_current_target_change_blocks_decision(configured):
    client, _, repository, _, token, _, _ = configured
    from app.p20_core.domain_records import FactRecord
    _, request = review_request(client, token)
    fact = FactRecord(fact_id="FACT-operator-test", project_id=PROJECT, subject_id="CHAR-synthetic",
        predicate="knows", object_type="state", object_id=None, object_value="concurrent value",
        reality_status="PROJECT_CANON", verification_status="CONFIRMED", confidence=1,
        frozen=False, author_locked=False, valid_from=None, valid_to=None, established_event_id=None,
        established_scene_id="SCENE-operator-test", source_artifact_ref="artifacts/synthetic-source.json",
        source_refs=["SCENE-operator-test#p1"], canon_version=1, version=1,
        created_at="2026-09-15T12:00:00Z", updated_at="2026-09-15T12:00:00Z")
    with repository.domain_transaction() as tx:
        tx.add_structured_memory_record(fact)
    before = repository.list_structured_memory_records()
    response = client.post(BASE + "/decision", headers=headers(token), json=request)
    assert response.status_code == 409 and response.json()["detail"] == "STALE_PROPOSAL_VERSION"
    assert repository.list_structured_memory_records() == before

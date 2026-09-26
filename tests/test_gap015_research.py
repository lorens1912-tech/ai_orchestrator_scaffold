from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openai.resources.responses.responses import Responses

from app.main import app
from app.operator_dpapi import read_secret
from app.p20_core.local_operator import initialize_operator
from app.p20_core.book_bible_test_helper import ensure_test_book_bible
from app.p20_core.project_repository import ProjectRepository, StorageResolver, ensure_system_repository
from app.p20_core import research
from app.p20_core.canon_service import canonical_proposal_hash
from app.p20_core.context_runtime import ProjectExecutionContext, build_runtime_context_package

PROJECT = "PROJ-research-test"
BOOK = "BOOK-research-test"
RID = "RESEARCH-neutral-question"
SID = "SOURCE-neutral-note"
BASE = f"/operator/projects/{PROJECT}"
TEXT = "The synthetic station opens at 08:00. An alternative note says 09:00."


def task(package):
    item = next(x for x in package["included_items"] if x["layer"] == "TASK")
    return json.loads(item["content"])["input"]


@pytest.mark.parametrize("resource", ["research", "proposal"])
def test_operator_memory_history_get_does_not_initialize_storage(setup, monkeypatch, resource):
    result = verified(setup)
    proposal = propose(setup, result)
    client, repo, headers, _controls = setup
    with sqlite3.connect(repo.db_path) as connection:
        before = connection.execute("SELECT key,value FROM project_metadata ORDER BY key").fetchall()

    def forbidden_initialize(self):
        raise AssertionError("history GET must not initialize storage")

    from app.p20_core.project_repository import SystemRepository, SeriesRepository
    for owner in (ProjectRepository, SystemRepository, SeriesRepository):
        monkeypatch.setattr(owner, "initialize", forbidden_initialize)
    path = (BASE + "/research/records/" + RID if resource == "research"
            else BASE + "/proposals/" + proposal["proposal_id"])
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["coverage"] == "MEMORY_PIPELINES_V1"
    with sqlite3.connect(repo.db_path) as connection:
        after = connection.execute("SELECT key,value FROM project_metadata ORDER BY key").fetchall()
    assert after == before


@pytest.mark.parametrize("resource", ["research", "proposal"])
def test_operator_memory_history_get_has_no_sql_writes(setup, monkeypatch, resource):
    from app.p20_core import project_repository as storage

    result = verified(setup)
    proposal = propose(setup, result)
    client, repo, headers, _controls = setup
    system = ensure_system_repository()
    paths = (repo.db_path, system.db_path)

    def snapshot():
        result = []
        for path in paths:
            with sqlite3.connect(path) as connection:
                dump = tuple(connection.iterdump())
            result.append((dump, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns))
        return result

    before = snapshot()
    statements = []
    original = storage._connect_repository_database

    def traced_connection(**kwargs):
        connection = original(**kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(storage, "_connect_repository_database", traced_connection)
    path = (BASE + "/research/records/" + RID if resource == "research"
            else BASE + "/proposals/" + proposal["proposal_id"])
    for _ in range(2):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
    assert snapshot() == before
    writes = [s for s in statements if s.lstrip().split()[0].upper() in {
        "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE",
    } or s.upper().startswith("BEGIN IMMEDIATE")]
    assert not writes, writes


@pytest.mark.parametrize("resource", ["research", "proposal"])
def test_operator_memory_history_get_refuses_legacy_without_migration(setup, resource):
    client, repo, headers, _ = setup
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute("UPDATE schema_version SET version=5 WHERE id=1")
    before = repo.db_path.read_bytes()
    path = (BASE + "/research/records/" + RID if resource == "research"
            else BASE + "/proposals/missing-proposal")
    for _ in range(2):
        response = client.get(path, headers=headers)
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "MIGRATION_REQUIRED"
    assert repo.db_path.read_bytes() == before


@pytest.mark.parametrize("resource", ["research/records/missing", "proposals/missing"])
def test_operator_memory_history_get_does_not_create_missing_project(setup, resource):
    client, _repo, headers, _ = setup
    project, book = "PROJ-history-missing", "BOOK-history-missing"
    ensure_system_repository().bind_project(project, book)
    context = StorageResolver().resolve_project(project, book_id=book)
    assert not context.project_root.exists()
    for _ in range(2):
        response = client.get(f"/operator/projects/{project}/{resource}", headers=headers)
        assert response.status_code == 404, response.text
    assert not context.project_root.exists()


@pytest.fixture
def setup(isolated_agentpro_storage, tmp_path, monkeypatch):
    system = ensure_system_repository()
    system.bind_project(PROJECT, BOOK)
    ensure_test_book_bible(BOOK)
    repo = ProjectRepository(StorageResolver().resolve_project(PROJECT, book_id=BOOK))
    repo.initialize()
    secret_path = tmp_path / "operator.dpapi"
    initialize_operator(system, secret_path)
    headers = {"Authorization": "Bearer " + read_secret(secret_path)}
    original = Responses.create
    controls = {"calls": [], "status": "CONFIRMED", "failure": None, "count": 1}

    def sdk(self, **kwargs):
        prompt = json.loads(kwargs["input"])
        if prompt.get("protocol") != "AGENTPRO_RESEARCH_V1":
            return original(self, **kwargs)
        controls["calls"].append(prompt)
        payload = task(prompt["context_package"])["evidence"]
        source = payload["sources"][0]
        ref = dict(source_id=source["source_id"], version=source["version"],
                   content_hash=source["content_hash"], start=0, end=len(source["content"]), quote=source["content"])
        if prompt["phase"] == "EXTRACT":
            result = {"claims": [{"claim": source["content"].strip() + (f" claim {i}" if i else ""),
                                  "source_refs": [ref]} for i in range(controls["count"])]}
        else:
            result = {"evaluations": [{"claim_id": c["claim_id"], "status": controls["status"],
                "confidence": 0.9, "reason": "Independent check of the supplied neutral source.",
                "evidence": [ref], "contradiction": {"statement": "Station opens at 09:00.", "evidence": [ref]}
                if controls["status"] == "DISPUTED" else None} for c in payload["claims"]]}
        if controls["failure"] == "transport":
            raise RuntimeError("synthetic transport failure")
        if controls["failure"] == "empty_evidence" and prompt["phase"] == "VERIFY":
            result["evaluations"][0]["evidence"] = []
        if controls["failure"] == "missing_claim" and prompt["phase"] == "VERIFY":
            result["evaluations"].pop()
        if controls["failure"] == "plain":
            return SimpleNamespace(output_text="not JSON", model=kwargs["model"], output=[])
        return SimpleNamespace(output_text=json.dumps(result), model=kwargs["model"], output=[])

    monkeypatch.setattr(Responses, "create", sdk)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 51010)) as client:
        yield client, repo, headers, controls


def post(env, suffix, data):
    client, _, headers, _ = env
    response = client.post(BASE + suffix, json=data, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def seed(env, *, content=TEXT):
    question = post(env, "/research/questions", dict(operation_id="question", research_id=RID,
                    question="When does the synthetic station open?", purpose="Neutral test"))
    source = post(env, "/research/sources", dict(operation_id="source-1", source_id=SID, version=1,
                    source_type="USER_NOTE", title="Neutral source", content=content))
    return question, source


def run(env, action, *, operation=None, claims=(), sources=None):
    request = dict(operation_id=operation or action.lower(), research_id=RID, action=action,
        run_id="run-research-test", step_id=action, model="gpt-research-test",
        source_refs=sources or ([dict(source_id=SID, version=1)] if action == "EXTRACT" else []),
        claim_ids=list(claims))
    return post(env, "/research/execute", request)


def verified(env):
    seed(env)
    extracted = run(env, "EXTRACT")
    return run(env, "VERIFY", claims=[c["claim_id"] for c in extracted["claims"]])


def propose(env, result, *, pid="proposal-research", fiction=None, operation="verify"):
    return post(env, "/research/proposals", dict(proposal_id=pid, operation_id=operation,
        target_fact_ids={c["claim_id"]: "FACT-research-" + str(i) for i, c in enumerate(result["claims"])},
        fiction_decision=fiction))


def approval(env, proposal, decision="APPROVE"):
    path = f"/proposals/{proposal['proposal_id']}"
    review = post(env, path + "/review", {})
    data = {k: proposal[k] for k in ("proposal_hash", "scope_type", "scope_id")}
    data.update(challenge_id=review["challenge"]["challenge_id"], decision=decision)
    return post(env, path + "/decision", data)


def commit(env, proposal):
    return post(env, f"/proposals/{proposal['proposal_id']}/commit", {"proposal_hash": proposal["proposal_hash"]})


def test_api_import_extract_verify_approval_commit_reopen_retry(setup):
    client, repo, headers, control = setup
    result = verified(setup)
    assert len(control["calls"]) == 2
    assert [c["phase"] for c in control["calls"]] == ["EXTRACT", "VERIFY"]
    for call in control["calls"]:
        package = call["context_package"]
        assert package["project_id"] == PROJECT and package["book_id"] == BOOK
        assert TEXT in json.dumps(package)
    assert control["calls"][0]["context_package"]["context_hash"] != control["calls"][1]["context_package"]["context_hash"]
    proposal = propose(setup, result)
    assert proposal["contract_version"] == "2.0"
    assert "source_scene_id" not in proposal and "extraction_candidate_set_id" not in proposal
    assert proposal["status"] == "AWAITING_USER_APPROVAL"
    assert commit(setup, proposal)["canonical_commit"] is False
    assert repo.list_structured_memory_records() == {}
    decision = approval(setup, proposal)
    assert decision["canonical_commit"] is False
    receipt = commit(setup, proposal)
    assert receipt["canonical_commit"] is True
    assert commit(setup, proposal) == receipt
    reopened = ProjectRepository(repo.context)
    assert reopened.read_research_state() == repo.read_research_state()
    assert json.loads(reopened.list_structured_memory_records()["FACT-research-0"])["object_value"] == TEXT
    assert run(setup, "VERIFY", claims=[c["claim_id"] for c in result["claims"]]) == result
    assert len(control["calls"]) == 2
    assert len(repo.read_research_state()["decisions"]) == 1
    assert len([k for k in repo.list_metadata() if k.startswith("canonical_commit.v1:")]) == 1


@pytest.mark.parametrize("status", ["UNCERTAIN", "DISPUTED", "REVIEW_REQUIRED"])
def test_nonconfirmed_requires_explicit_fiction_preserves_original(setup, status):
    setup[3]["status"] = status
    result = verified(setup)
    with pytest.raises(AssertionError):
        propose(setup, result)
    fiction = {"reality_status": "FICTIONAL_OVERLAY_ON_REAL_WORLD", "reason": "Intentional synthetic exception."}
    proposal = propose(setup, result, fiction=fiction)
    approval(setup, proposal)
    assert commit(setup, proposal)["canonical_commit"] is True
    state = setup[1].read_research_state()
    assert state["claims"][result["claims"][0]["claim_id"]][-1]["verification_status"] == status
    fact = json.loads(setup[1].list_structured_memory_records()["FACT-research-0"])
    assert fact["reality_status"] == fiction["reality_status"]
    assert fact["verification_status"] == status
    if status == "DISPUTED":
        conflict = next(iter(state["conflicts"].values()))
        assert conflict["statement_a"] != conflict["statement_b"] and conflict["status"] == "RESOLVED"


@pytest.mark.parametrize("failure", ["plain", "transport", "empty_evidence", "missing_claim"])
def test_failed_verification_atomic_and_retry_pinned_context(setup, failure):
    seed(setup)
    setup[3]["count"] = 2
    extracted = run(setup, "EXTRACT")
    claims = [c["claim_id"] for c in extracted["claims"]]
    setup[3]["failure"] = failure
    with pytest.raises(AssertionError):
        run(setup, "VERIFY", claims=claims)
    before = setup[3]["calls"][-1]["context_package"]
    state = setup[1].read_research_state()
    assert all(len(v) == 1 for v in state["claims"].values())
    assert state["operations"]["verify"]["status"] == "FAILED"
    assert setup[1].list_structured_memory_records() == {}
    setup[3]["failure"] = None
    run(setup, "VERIFY", claims=claims)
    assert setup[3]["calls"][-1]["context_package"] == before


def test_reference_only_authentication_and_scope(setup):
    client, repo, headers, controls = setup
    seed(setup, content=None)
    with pytest.raises(AssertionError):
        run(setup, "EXTRACT")
    assert controls["calls"] == []
    assert client.get(BASE + f"/research/records/{RID}").status_code == 401
    assert client.get(BASE.replace(PROJECT, "PROJ-foreign") + f"/research/records/{RID}", headers=headers).status_code == 403
    assert client.post(BASE + "/research/sources", headers=headers, json={"content_hash": "forged"}).status_code == 422
    with pytest.raises(research.ResearchError):
        research.create_research(repo, operation_id="bad", research_id="RESEARCH-foreign",
            question="Synthetic?", purpose="test", requested_by="operator", related_entity_refs=["FACT-foreign"])


def test_source_version_stales_approval_and_reject_blocks(setup):
    result = verified(setup)
    proposal = propose(setup, result)
    approval(setup, proposal)
    post(setup, "/research/sources", dict(operation_id="source-2", source_id=SID, version=2,
         source_type="USER_NOTE", title="New neutral source", content="The synthetic station opens at 10:00."))
    assert commit(setup, proposal)["status"] == "STALE"
    assert setup[1].list_structured_memory_records() == {}
    extracted = run(setup, "EXTRACT", operation="extract-2", sources=[dict(source_id=SID, version=2)])
    result2 = run(setup, "VERIFY", operation="verify-2", claims=[c["claim_id"] for c in extracted["claims"]])
    newer = propose(setup, result2, operation="verify-2")
    assert newer["proposal_version"] == 2 and newer["proposal_hash"] != proposal["proposal_hash"]
    assert commit(setup, newer)["canonical_commit"] is False
    approval(setup, newer, "REJECT")
    assert commit(setup, newer)["status"] == "REJECTED"
    history = json.loads(setup[1].get_metadata("canonical_proposal.v1:" + proposal["proposal_id"]))
    assert history["versions"]["1"]["decision"]["decision"] == "APPROVE"


def test_missing_model_configuration_is_not_success(setup, monkeypatch):
    seed(setup)
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(AssertionError):
        run(setup, "EXTRACT")
    assert setup[3]["calls"] == []
    assert setup[1].read_research_state()["operations"]["extract"]["status"] == "FAILED"


def p20(env, config=None, *, retry=False, step="p20-research"):
    request = dict(mode="FACTCHECK", project_id=PROJECT, book_id=BOOK,
        run_id="run-p20-research", step_id=step,
        payload={"text": "Check the neutral research evidence.", "model": "gpt-research-test",
                 "technical_retry": retry})
    if config is not None:
        request["payload"]["research"] = config
    response = env[0].post("/agent/step", json=request)
    assert response.status_code == 200, response.text
    return response.json()


def test_active_p20_factcheck_context_audit_retry_and_fiction(setup, isolated_agentpro_storage):
    seed(setup)
    extracted = run(setup, "EXTRACT")
    setup[3]["status"] = "DISPUTED"
    config = dict(operation_id="p20-verify", research_id=RID,
                  claim_ids=[c["claim_id"] for c in extracted["claims"]])
    body = p20(setup, config)
    assert body["ok"] is True, body
    result = body["research"][0]
    assert result["action"] == "VERIFY" and result["claims"][0]["verification_status"] == "DISPUTED"
    assert result["ISSUES"] and not result["canonical_commit"]
    assert body["run_state"]["research"] == body["research"]
    assert body["context_hash"]
    calls = len(setup[3]["calls"])
    repeated = p20(setup, config, retry=True)
    assert repeated["research"] == body["research"] and len(setup[3]["calls"]) == calls
    assert repeated["context_hash"] == body["context_hash"]
    proposal = propose(setup, result, operation="p20-verify", fiction={
        "reality_status": "FICTIONAL", "reason": "Intentional neutral fiction."})
    approval(setup, proposal)
    commit(setup, proposal)
    repeated = p20(setup, config, retry=True)
    assert repeated["research"][0]["ISSUES"] == []
    assert repeated["research"][0]["intentional_fiction"] == config["claim_ids"]
    (isolated_agentpro_storage / "gap015-functional-proof.json").write_text(
        json.dumps({"result": body, "proposal": proposal, "fiction_retry": repeated}, indent=2), encoding="utf-8")


def test_factcheck_never_claims_success_without_evidence(setup):
    body = p20(setup)
    assert body["ok"] is False and body["decision"] == "FAILED"
    assert body["research"][0]["execution_status"] == "NOT_PERFORMED"
    assert body["research"][0]["ISSUES"]
    seed(setup)
    extracted = run(setup, "EXTRACT")
    setup[3]["failure"] = "plain"
    body = p20(setup, dict(operation_id="p20-invalid", research_id=RID,
                          claim_ids=[c["claim_id"] for c in extracted["claims"]]), step="invalid")
    assert body["decision"] == "FAILED" and body["research"][0]["execution_status"] == "FAILED"
    assert setup[1].list_structured_memory_records() == {}


def test_context_role_conflicts_source_refresh_and_foreign_isolation(setup):
    setup[3]["status"] = "DISPUTED"
    verified(setup)
    def package(role, step, project=PROJECT, book=BOOK, retry=False):
        execution = ProjectExecutionContext.create(project_id=project, book_id=book, series_id=None,
            run_id="run-context", step_id=step, technical_retry=retry)
        return build_runtime_context_package(execution_context=execution, mode="FACTCHECK", role=role,
            requested_model="gpt-research-test", effective_model="gpt-research-test",
            tool_input={"research": {"research_id": RID}}, context_sources={}).to_dict()
    first = package("VERIFIER", "verify-context")
    assert any(x["layer"] == "CONFLICT" for x in first["included_items"])
    writer = package("WRITER", "writer-context")
    content = json.loads(next(x["content"] for x in writer["included_items"] if x["entity_type"] == "RESEARCH"))
    assert all("content" not in s for s in content["sources"].values())
    assert content["authority"] == "RESEARCH_ONLY_NOT_CANON"
    assert content["conflicts"][0]["source_refs_a"] and content["conflicts"][0]["source_refs_b"]
    result = run(setup, "VERIFY", operation="re-evaluation", claims=list(setup[1].read_research_state()["claims"]))
    assert package("VERIFIER", "new-context")["context_hash"] != first["context_hash"]
    assert package("VERIFIER", "verify-context", retry=True) == first
    foreign_project, foreign_book = "PROJ-foreign-research", "BOOK-foreign-research"
    ensure_system_repository().bind_project(foreign_project, foreign_book)
    with pytest.raises(ValueError, match="unresolved"):
        package("VERIFIER", "foreign-context", foreign_project, foreign_book)


@pytest.mark.parametrize("change", ["authority", "assertion", "evidence", "context", "scope", "approval"])
def test_forged_authority_hashes_and_approval_denied_at_physical_boundary(setup, change):
    result = verified(setup)
    proposal = propose(setup, result)
    decision = approval(setup, proposal)
    repo = setup[1]
    with repo.canonical_proposal_transaction(proposal["proposal_id"], include_writer=True) as (doc, snap, connection):
        record = doc["versions"]["1"]
        fake = copy.deepcopy(record["proposal"])
        if change == "authority":
            fake["authority_ref"] = "P20_VERIFIED_EXTRACTION_V1"
        elif change == "assertion":
            fake["proposed_mutations"][0]["proposed_state"]["object_value"] += " Unverified extension."
            fake["proposed_mutations"][0]["provenance"]["state_hash"] = research.digest(fake["proposed_mutations"][0]["proposed_state"])
        elif change == "evidence":
            fake["research_evidence"]["hash"] = "a" * 64
        elif change == "context":
            fake["context_hash"] = "b" * 64
        elif change == "scope":
            fake["scope_type"] = "SERIES"
        else:
            decision["authorization_ref"] = "forged"
        fake["proposal_hash"] = canonical_proposal_hash(fake)
        with pytest.raises((ValueError, RuntimeError)):
            repo.apply_canonical_record_set(connection, fake, snap, impact=record["impact"], approval=decision)
    assert repo.list_structured_memory_records() == {}
    if change == "assertion":
        with pytest.raises(research.ResearchError, match="exceeds"):
            research.validate_promotion_evidence(fake, repo.read_research_state())


def test_semantic_verification_not_just_hashes(setup):
    result = verified(setup)
    state = setup[1].read_research_state()
    op = state["operations"]["verify"]
    op["result"]["claims"][0]["claim"] = "Unsupported synthetic claim."
    op["result_hash"] = research.digest(op["result"])
    state["claims"][result["claims"][0]["claim_id"]][-1] = op["result"]["claims"][0]
    with pytest.raises(research.ResearchError, match="differs"):
        research.verified_evidence(state, "verify")


def test_failure_rollback_entire_approved_set(setup, monkeypatch):
    setup[3]["count"] = 2
    result = verified(setup)
    proposal = propose(setup, result)
    approval(setup, proposal)
    repo = setup[1]
    original_record_event = ProjectRepository.record_memory_event
    entity_events = {"count": 0}

    def fail_second_entity_event(self, connection, **kwargs):
        if kwargs.get("event_type") == "CANONICAL_ENTITY_CHANGED":
            entity_events["count"] += 1
            if entity_events["count"] == 2:
                raise sqlite3.IntegrityError("synthetic disk fault")
        return original_record_event(self, connection, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(ProjectRepository, "record_memory_event", fail_second_entity_event)
        with pytest.raises(sqlite3.IntegrityError, match="synthetic disk fault"):
            commit(setup, proposal)
    assert entity_events["count"] == 2
    assert repo.list_structured_memory_records() == {}
    assert not repo.read_research_state()["decisions"]
    assert not any(k.startswith(("canonical_commit.v1:", "canonical_versions.v1:")) for k in repo.list_metadata())
    receipt = commit(setup, proposal)
    assert len(receipt["resulting_versions"]) == 2
    assert commit(setup, proposal) == receipt


def test_protected_update_and_legacy_unknown_stay_closed(setup):
    from tests.test_canonical_pipeline import fact
    repo = setup[1]
    existing = fact(project=PROJECT, identity="FACT-research-0", protected=True)
    with repo.domain_transaction() as transaction:
        transaction.add_structured_memory_record(existing)
    result = verified(setup)
    proposal = propose(setup, result)
    assert commit(setup, proposal)["canonical_commit"] is False
    approval(setup, proposal)
    assert commit(setup, proposal)["canonical_commit"] is True
    persisted = json.loads(repo.list_structured_memory_records()["FACT-research-0"])
    assert persisted["version"] == 2 and persisted["frozen"] and persisted["author_locked"]


def test_commands_versions_and_operation_reuse(setup):
    question, source = seed(setup)
    assert seed(setup) == (question, source)
    assert source["author"] is None and source["reliability"] is None
    assert source["content_hash"] == hashlib.sha256(TEXT.encode()).hexdigest()
    with pytest.raises(AssertionError):
        seed(setup, content="Changed content under same operation")
    state = setup[1].read_research_state()
    assert len(state["sources"][SID]) == 1


def test_explicit_recovery_fences_in_flight_attempt(setup, monkeypatch):
    seed(setup)
    original = Responses.create
    once = []
    def interrupted(self, **kwargs):
        result = original(self, **kwargs)
        if not once:
            once.append(True)
            response = setup[0].post(BASE + "/research/operations/extract/recover", headers=setup[2])
            assert response.status_code == 200
        return result
    monkeypatch.setattr(Responses, "create", interrupted)
    with pytest.raises(AssertionError):
        run(setup, "EXTRACT")
    assert setup[1].read_research_state()["claims"] == {}
    run(setup, "EXTRACT")
    assert len(setup[1].read_research_state()["claims"]) == 1


def test_direct_repository_path_cannot_bypass_research_approval(setup):
    from app.p20_core.domain_records import FactRecord
    from app.p20_core.domain_mutation_guard import DomainMutationGuard
    result = verified(setup)
    proposal = propose(setup, result)
    record = FactRecord(**proposal["proposed_mutations"][0]["proposed_state"])
    with pytest.raises(ValueError, match="RESEARCH_CANONICAL_APPROVAL_REQUIRED"):
        with setup[1].domain_transaction() as transaction:
            transaction.add_structured_memory_record(record)
    with setup[1].canonical_proposal_transaction(proposal["proposal_id"]) as (doc, snapshot):
        fake = copy.deepcopy(proposal)
        fake["authority_ref"] = "P20_VERIFIED_EXTRACTION_V1"
        assert DomainMutationGuard().evaluate_canonical(proposal=fake, snapshot=snapshot,
            impact=doc["versions"]["1"]["impact"])["outcome"] == "DENY"


@pytest.mark.parametrize("change", ["target", "graph", "claim", "verification", "source_hash"])
def test_final_validation_rejects_changed_basis_and_evidence(setup, change):
    from tests.test_canonical_pipeline import fact
    from app.p20_core.domain_records import EdgeRecord
    result = verified(setup)
    proposal = propose(setup, result)
    approval(setup, proposal)
    repo = setup[1]
    if change == "target":
        with repo.domain_transaction() as transaction:
            transaction.add_structured_memory_record(fact(project=PROJECT, identity="FACT-research-0"))
    elif change == "graph":
        with repo.domain_transaction() as transaction:
            transaction.add_edge(EdgeRecord(edge_id="edge-research-impact", scope_type="PROJECT", scope_id=PROJECT,
                source_type="SCENE", source_id="SCENE-neutral", relation_type="REQUIRES",
                target_type="FACT", target_id="FACT-research-0", valid_from=None, valid_to=None,
                confidence=1, source_ref="synthetic", version=1), source_scope=repo.scope, target_scope=repo.scope)
    else:
        with repo.research_transaction() as state:
            if change == "claim":
                state["claims"][result["claims"][0]["claim_id"]][-1]["claim"] = "Changed assertion."
            elif change == "verification":
                state["operations"]["verify"]["invocation"]["output"]["evaluations"][0]["status"] = "UNCERTAIN"
            else:
                state["sources"][SID][-1]["content_hash"] = "a" * 64
    outcome = commit(setup, proposal)
    assert outcome["status"] == "STALE" and not outcome["canonical_commit"]
    assert not any(k.startswith("canonical_commit.v1:") for k in repo.list_metadata())


def test_legacy_protection_unknown_remains_blocked(setup):
    from tests.test_canonical_pipeline import fact
    from app.p20_core.domain_records import EdgeRecord
    repo = setup[1]
    result = verified(setup)
    with repo.domain_transaction() as transaction:
        transaction.add_structured_memory_record(fact(project=PROJECT, identity="FACT-research-0"))
    with repo.connect() as connection:
        old = json.loads(repo.list_structured_memory_records()["FACT-research-0"])
        del old["frozen"]
        connection.execute("UPDATE project_structured_memory_records SET payload_json=? WHERE record_id=?",
                           (json.dumps(old), "FACT-research-0"))
    with pytest.raises(AssertionError):
        propose(setup, result)
    assert repo.list_structured_memory_records()["FACT-research-0"] == json.dumps(old)


def test_source_refresh_changes_context_but_does_not_mutate_pinned_evidence(setup):
    result = verified(setup)
    original = copy.deepcopy(setup[1].read_research_state()["operations"]["verify"])
    before = research.research_context(setup[1], RID, include_sources=True)
    post(setup, "/research/sources", dict(operation_id="updated-source", source_id=SID, version=2,
        source_type="USER_NOTE", title="Updated note", content="Different neutral statement."))
    after = research.research_context(setup[1], RID, include_sources=True)
    assert research.digest(before) != research.digest(after)
    assert after["current_source_versions"][SID] == 2
    assert after["sources"][SID + ":1"]["content"] == TEXT
    assert setup[1].read_research_state()["operations"]["verify"] == original
    assert run(setup, "VERIFY", claims=[c["claim_id"] for c in result["claims"]]) == original["result"]


def test_derived_rebuild_remains_blocked(setup):
    from app.p20_core.domain_records import EdgeRecord
    result = verified(setup)
    repo = setup[1]
    with repo.domain_transaction() as transaction:
        transaction.add_edge(EdgeRecord(edge_id="edge-research-derived", scope_type="PROJECT", scope_id=PROJECT,
            source_type="CONTEXT", source_id="CONTEXT-derived", relation_type="REQUIRES",
            target_type="FACT", target_id="FACT-research-0", valid_from=None, valid_to=None,
            confidence=1, source_ref="synthetic", version=1), source_scope=repo.scope, target_scope=repo.scope)
    response = setup[0].post(BASE + "/research/proposals", headers=setup[2], json=dict(
        proposal_id="derived-proposal", operation_id="verify",
        target_fact_ids={result["claims"][0]["claim_id"]: "FACT-research-0"}))
    assert response.status_code == 409 and response.json()["detail"] == "DERIVED_REBUILD_UNSUPPORTED"
    assert repo.list_structured_memory_records() == {}


def test_plain_text_preserves_whitespace_exactly(setup):
    text = "\n" + TEXT + "\n"
    _, source = seed(setup, content=text)
    assert source["content"] == text
    extracted = run(setup, "EXTRACT")
    assert extracted["claims"][0]["source_refs"][0]["quote"] == text
    assert extracted["claims"][0]["confidence"] is None
    assert source["content_hash"] == hashlib.sha256(text.encode()).hexdigest()


def test_foreign_source_cannot_be_used_as_local_evidence(setup):
    seed(setup)
    repo = setup[1]
    with repo.research_transaction() as state:
        state["sources"][SID][-1]["project_id"] = "PROJ-foreign"
    with pytest.raises(ValueError, match="scope mismatch"):
        repo.read_research_state()

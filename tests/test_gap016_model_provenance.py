from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from openai.resources.responses.responses import Responses

from app.model_policy import resolve_model
from app.p20_core.context_runtime import ProjectExecutionContext, build_runtime_context_package
import app.p20_core.model_provenance as model_provenance
from app.p20_core.model_provenance import ModelInvocationAudit, InvocationRecoveryRequired, fingerprint, public_trace, recover_invocation
from app.p20_core.project_repository import ProjectRepository, StorageResolver
from app.p20_core.storage_paths import get_runs_root
from tests.test_memory_transport_integration import (
    production_pipeline, _step, _install_sdk_boundary, _use_production_provider,
)
from tests.test_gap015_research import setup, seed, run, verified, BASE


@pytest.fixture(autouse=True)
def isolated_provenance_storage(isolated_agentpro_storage):
    yield


def make_audit(*, project='PROJ-provenance', book='BOOK-provenance', step='step', routing=None):
    repo = ProjectRepository(StorageResolver().resolve_project(project, book_id=book))
    repo.initialize()
    execution = ProjectExecutionContext.create(project_id=project, book_id=book,
        series_id=None, run_id='run-provenance', step_id=step)
    package = build_runtime_context_package(execution_context=execution, mode='MEMORY_EXTRACTOR',
        role='EXTRACTOR', requested_model='alias', effective_model='alias', tool_input={'input': 'Neutral test'}, context_sources={})
    return repo, ModelInvocationAudit(repo, execution, package, role='EXTRACTOR', mode='MEMORY_EXTRACTOR',
                                     requested_model='alias', routing=routing or resolve_model('alias').provenance)


def sdk_response(model='snapshot', text='{}'):
    return SimpleNamespace(output_text=text, model=model, output=[], id='response-neutral',
        _request_id='request-neutral', usage=SimpleNamespace(input_tokens=5, output_tokens=2, total_tokens=7))


@pytest.mark.parametrize('temperature', ['absent', None, 0, 0.8])
@pytest.mark.parametrize('provider_model', [None, 'snapshot'])
def test_sent_parity_presence_identity_reopen_retry(monkeypatch, temperature, provider_model):
    repo, audit = make_audit()
    calls = []
    def sdk(self, **kwargs):
        calls.append(kwargs)
        return sdk_response(provider_model)
    monkeypatch.setattr(Responses, 'create', sdk)
    args = {} if temperature == 'absent' else {'temperature': temperature}
    result = audit.call(prompt='Neutral prompt', model='alias', **args)
    audit.validated()
    records = repo.list_model_invocations()
    trace = records[0]
    assert trace['requested']['model'] == 'alias'
    assert trace['resolved']['decision']['effective_model'] == 'alias'
    attempt = trace['attempts'][0]
    assert attempt['sent'] == {k: v for k, v in calls[0].items() if k != 'input'}
    assert attempt['provider_reported']['model'] == provider_model
    assert attempt['provider_reported']['model_version'] is None
    assert attempt['provider_reported']['usage']['total_tokens'] == 7
    assert attempt['sdk']['http_attempt_count'] is None
    assert trace['request']['parameters'] == args
    assert attempt['input_hash'] == hashlib.sha256(calls[0]['input'].encode()).hexdigest()
    reopened, reused = make_audit()
    assert reused.call(prompt='Neutral prompt', model='alias', **args) == result
    assert reopened.list_model_invocations() == records
    assert len(calls) == 1
    assert 'result' not in public_trace(repo, run_id='run-provenance')[0]


@pytest.mark.parametrize('error,temperature,count,status', [
    (RuntimeError("Unsupported parameter: 'temperature'"), 0.8, 2, 'RECEIVED'),
    (RuntimeError("Unsupported parameter: 'temperature'"), None, 1, 'FAILED'),
    (RuntimeError('SECRET-SENTINEL unconnected failure'), 0.8, 1, 'FAILED'),
    (TimeoutError('SECRET-SENTINEL timeout'), 0.8, 1, 'TIMEOUT'),
])
def test_retry_only_allowed_parameter_and_safe_error(monkeypatch, error, temperature, count, status):
    repo, audit = make_audit()
    calls = []
    def sdk(self, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise error
        return sdk_response()
    monkeypatch.setattr(Responses, 'create', sdk)
    if count == 2:
        result = audit.call(prompt='Neutral prompt', model='alias', temperature=temperature)
        assert result['retried'] and result['dropped_params'] == ['temperature']
        assert calls[0]['temperature'] == 0.8 and 'temperature' not in calls[1]
    else:
        with pytest.raises(type(error)):
            audit.call(prompt='Neutral prompt', model='alias', temperature=temperature)
    trace = repo.list_model_invocations()[0]
    assert len(calls) == len(trace['attempts']) == count
    assert len({a['attempt_id'] for a in trace['attempts']}) == count
    assert trace['attempts'][-1]['status'] == status
    assert 'SECRET-SENTINEL' not in json.dumps(trace)
    if count == 1:
        assert trace['attempts'][0]['remote_outcome'] == 'UNKNOWN'


def test_fingerprint_and_project_isolation(monkeypatch):
    monkeypatch.setattr(Responses, 'create', lambda self, **kwargs: sdk_response())
    first, a = make_audit()
    second, b = make_audit(project='PROJ-other', book='BOOK-other')
    a.call(prompt='first', model='alias')
    b.call(prompt='second', model='alias')
    one, two = first.list_model_invocations()[0], second.list_model_invocations()[0]
    assert one['invocation_id'] != two['invocation_id']
    assert one['configuration_hash'] == two['configuration_hash']
    assert one['input_fingerprint'] != two['input_fingerprint']
    assert fingerprint({'b': 1, 'a': None}) == fingerprint({'a': None, 'b': 1})
    assert len(first.list_model_invocations()) == len(second.list_model_invocations()) == 1
    with pytest.raises(ValueError, match='changed'):
        a.call(prompt='changed', model='alias')
    with pytest.raises(ValueError, match='scope'):
        with first.model_invocation_transaction(a.operation_id) as state:
            state['project_id'] = 'PROJ-other'
        first.list_model_invocations()


def test_interruption_fencing_and_explicit_recovery(monkeypatch):
    repo, audit = make_audit()
    def interrupted(self, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(Responses, 'create', interrupted)
    with pytest.raises(KeyboardInterrupt):
        audit.call(prompt='Neutral', model='alias')
    trace = repo.list_model_invocations()[0]
    assert trace['attempts'][0]['status'] == 'INTERRUPTED'
    # Simulate persisted START surviving process loss, using the real transaction.
    with repo.model_invocation_transaction(audit.operation_id) as state:
        state['attempts'][0]['status'] = 'STARTED'
    reopened, retry = make_audit()
    with pytest.raises(InvocationRecoveryRequired):
        retry.call(prompt='Neutral', model='alias')
    recover_invocation(reopened, audit.operation_id, recovered_by='TEST_OPERATOR')
    monkeypatch.setattr(Responses, 'create', lambda self, **kwargs: sdk_response())
    retry.call(prompt='Neutral', model='alias')
    trace = repo.list_model_invocations()[0]
    assert [a['status'] for a in trace['attempts']] == ['INTERRUPTED', 'RECEIVED']
    assert trace['attempts'][0]['remote_outcome'] == 'UNKNOWN'


def test_real_sql_failure_leaves_started_attempt(monkeypatch):
    repo, audit = make_audit()
    original_transaction = repo.model_invocation_transaction

    @contextmanager
    def fail_received_write(operation_id, *, connection=None):
        with original_transaction(operation_id, connection=connection) as state:
            yield state
            if state.get('attempts') and state['attempts'][-1]['status'] == 'RECEIVED':
                raise sqlite3.IntegrityError('synthetic disk failure')

    with monkeypatch.context() as patch:
        patch.setattr(Responses, 'create', lambda self, **kwargs: sdk_response())
        patch.setattr(repo, 'model_invocation_transaction', fail_received_write)
        with pytest.raises(sqlite3.DatabaseError, match='synthetic disk failure'):
            audit.call(prompt='Neutral', model='alias')
    assert repo.list_model_invocations()[0]['attempts'][0]['status'] == 'STARTED'
    with pytest.raises(InvocationRecoveryRequired):
        audit.call(prompt='Neutral', model='alias')


@pytest.mark.parametrize('behavior,expected', [('accept', 'VALID'), ('invalid_verifier', 'INVALID'),
                                             ('refusal', 'INVALID'), ('plain_text', 'INVALID'),
                                             ('transport_error', 'INVALID')])
def test_active_memory_p20_audit(production_pipeline, monkeypatch, behavior, expected):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)
    calls = _install_sdk_boundary(monkeypatch, behavior)
    body = _step(client, run='run-provenance-api', step_id='step-provenance')
    traces = repo.list_model_invocations(run_id=body['run_id'])
    assert len(traces) == len(calls)
    by_role = {t['role']: t for t in traces}
    if behavior in ('accept', 'invalid_verifier'):
        assert set(by_role) == {'EXTRACTOR', 'VERIFIER'}
        assert by_role['VERIFIER']['validation'] == expected
        assert by_role['EXTRACTOR']['invocation_id'] != by_role['VERIFIER']['invocation_id']
    else:
        assert by_role['EXTRACTOR']['validation'] == expected
    audit = json.loads((get_runs_root() / body['run_id'] / 'audit.json').read_text(encoding='utf-8'))
    assert audit['model_provenance'] == public_trace(repo, run_id=body['run_id'])
    assert body['run_state']['model_provenance'] == audit['model_provenance']
    for trace in traces:
        assert trace['resolved']['decision']['effective_model'] == 'gpt-memory-effective'
        package = repo.get_context_package(trace['context_package_id'])
        assert package.context_hash == trace['context_hash']
        call = next(c for c in calls if c['prompt']['context_package']['context_package_id'] == package.context_package_id)
        assert trace['attempts'][0]['sent'] == {k: v for k, v in call['kwargs'].items() if k != 'input'}
    if behavior == 'accept':
        from pathlib import Path
        evidence = Path(__file__).resolve().parents[1] / '.test_storage' / 'gap016_evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / 'api-proof.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
        assert body['canonical_change']['canonical_commit'] is True
        _step(client, run=body['run_id'], step_id='step-provenance', technical_retry=True)
        assert repo.list_model_invocations(run_id=body['run_id']) == traces
    else:
        assert body['canonical_change']['canonical_commit'] is False


def test_research_distinct_calls_reopen_original_evidence(setup):
    result = verified(setup)
    _, repo, _, control = setup
    before = copy.deepcopy(repo.read_research_state())
    traces = repo.list_model_invocations()
    assert len(traces) == 2
    assert {t['mode'] for t in traces} == {'RESEARCH_EXTRACT', 'RESEARCH_VERIFY'}
    assert all(t['validation'] == 'VALID' for t in traces)
    assert all(t['resolved']['decision']['effective_model'] == 'gpt-research-test' for t in traces)
    extracted = run(setup, 'EXTRACT')
    assert run(setup, 'VERIFY', claims=[c['claim_id'] for c in extracted['claims']]) == result
    assert len(control['calls']) == 2
    assert repo.read_research_state() == before
    assert repo.list_model_invocations() == traces
    for op in before['operations'].values():
        if 'invocation' in op:
            assert 'attempts' not in op['invocation']['transport']
            assert 'invocation_id' not in op['invocation']


def test_routing_precedence_and_original_contract(monkeypatch, tmp_path):
    force = tmp_path / 'force.json'
    force.write_text('{"model":"forced"}', encoding='utf-8')
    monkeypatch.setenv('MODEL_FORCE_FILE', str(force))
    monkeypatch.setenv('WRITE_MODEL_FORCE', 'env')
    monkeypatch.setenv('MODEL_DEFAULT', 'default')
    monkeypatch.delenv('MODEL_ALLOWLIST', raising=False)
    for args, expected in [({'requested_model':'gpt5','header_model':'header','preset_model':'preset'}, 'gpt-5'),
                           ({'requested_model':None,'header_model':'header','preset_model':'preset'}, 'header'),
                           ({'requested_model':None,'preset_model':'preset'}, 'preset'), ({'requested_model':None}, 'forced')]:
        decision = resolve_model(**args)
        assert decision.effective_model == expected
        assert set(decision.to_dict()) == {'requested_model','effective_model','source','allowlist_ok','note'}
        assert decision.provenance['requested']['body'] == args['requested_model']
    force.unlink()
    assert resolve_model(None).effective_model == 'env'
    monkeypatch.delenv('WRITE_MODEL_FORCE')
    assert resolve_model(None).effective_model == 'default'
    monkeypatch.setenv('MODEL_ALLOWLIST','allowed')
    monkeypatch.setenv('MODEL_POLICY_MODE','STRICT')
    assert not resolve_model('denied').allowlist_ok
    from app.p20_core.executor import _models_for_step
    with pytest.raises(ValueError, match='MODEL_POLICY_DENIED'):
        _models_for_step(step_overrides={}, runtime_overrides={}, tool_input={'model':'denied'},team={})
    monkeypatch.setenv('MODEL_POLICY_MODE','PERMISSIVE')
    assert resolve_model('denied').effective_model == 'allowed'


def test_research_public_read_projection_keeps_context_unchanged(setup):
    verified(setup)
    client, repo, headers, _ = setup
    from app.p20_core.research import research_context
    from tests.test_gap015_research import RID
    context = research_context(repo, RID, include_sources=False)
    response = client.get(BASE + '/research/records/' + RID, headers=headers)
    assert response.status_code == 200
    assert len(response.json()['model_provenance']) == 2
    assert all('result' not in t for t in response.json()['model_provenance'])
    assert 'model_provenance' not in context
    assert context == research_context(repo, RID, include_sources=False)


def test_configuration_error_has_durable_no_attempt_trace(production_pipeline, monkeypatch):
    client, repo = production_pipeline
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    calls = _install_sdk_boundary(monkeypatch)
    body = _step(client, run='run-unconfigured', step_id='step-config')
    trace = repo.list_model_invocations()[0]
    assert trace['status'] == 'CONFIGURATION_FAILED'
    assert trace['attempts'] == calls == []
    assert body['canonical_change']['canonical_commit'] is False


def test_pinned_policy_and_api_mode_on_retry(monkeypatch):
    repo, audit = make_audit()
    attempts = []
    def sdk(self, **kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise TimeoutError('synthetic')
        return sdk_response()
    monkeypatch.setattr(Responses, 'create', sdk)
    with pytest.raises(TimeoutError):
        audit.call(prompt='Neutral', model='alias')
    original = repo.list_model_invocations()[0]['resolved']
    monkeypatch.setenv('MODEL_DEFAULT', 'different-default')
    _, retry = make_audit()
    assert repo.list_model_invocations()[0]['resolved'] == original
    monkeypatch.setenv('OPENAI_API_MODE', 'chat')
    with pytest.raises(ValueError, match='changed'):
        retry.call(prompt='Neutral', model='alias')
    monkeypatch.setenv('OPENAI_API_MODE', 'responses')
    retry.call(prompt='Neutral', model='alias')
    assert len(attempts) == 2
    trace = repo.list_model_invocations()[0]
    assert trace['resolved'] == original
    assert len(trace['attempts']) == 2


def test_finished_response_missing_result_requires_recovery(monkeypatch):
    repo, audit = make_audit()
    calls = []
    def sdk(self, **kwargs):
        calls.append(kwargs)
        return sdk_response()
    monkeypatch.setattr(Responses, 'create', sdk)
    audit.call(prompt='Neutral', model='alias')
    with repo.model_invocation_transaction(audit.operation_id) as state:
        del state['result']  # Durable END, process died before result persistence.
    _, retry = make_audit()
    with pytest.raises(InvocationRecoveryRequired, match='PERSISTENCE_INCOMPLETE'):
        retry.call(prompt='Neutral', model='alias')
    assert len(calls) == 1
    recover_invocation(repo, audit.operation_id, recovered_by='TEST_OPERATOR')
    retry.call(prompt='Neutral', model='alias')
    assert len(calls) == 2
    assert repo.list_model_invocations()[0]['attempts'][0]['remote_outcome'] == 'RESPONSE_RECEIVED'


def test_distinct_execution_identity_stable_configuration(monkeypatch):
    monkeypatch.setattr(Responses, 'create', lambda self, **kwargs: sdk_response())
    repo, one = make_audit(step='one')
    _, two = make_audit(step='two')
    one.call(prompt='same', model='alias')
    two.call(prompt='same', model='alias')
    a, b = repo.list_model_invocations()
    assert a['configuration_hash'] == b['configuration_hash']
    assert a['invocation_id'] != b['invocation_id']
    assert a['attempts'][0]['attempt_id'] != b['attempts'][0]['attempt_id']


def test_historical_absence_does_not_backfill(monkeypatch):
    repo = ProjectRepository(StorageResolver().resolve_project('PROJ-history', book_id='BOOK-history'))
    repo.initialize()
    legacy = {'role': 'EXTRACTOR', 'model': 'legacy', 'metadata': {'context_hash': 'frozen'}}
    repo.set_metadata('legacy_model', json.dumps(legacy))
    before = repo.get_metadata('legacy_model')
    assert repo.list_model_invocations() == []
    assert repo.get_metadata('legacy_model') == before


def test_chat_exact_kwargs_at_sdk_boundary(monkeypatch):
    from openai.resources.chat.completions.completions import Completions
    monkeypatch.setenv('OPENAI_API_MODE', 'chat')
    calls = []
    def sdk(self, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(model='chat-snapshot', choices=[SimpleNamespace(
            message=SimpleNamespace(content='{}', refusal=None))])
    monkeypatch.setattr(Completions, 'create', sdk)
    repo, audit = make_audit()
    audit.call(prompt='neutral chat', model='alias', temperature=0)
    attempt = repo.list_model_invocations()[0]['attempts'][0]
    assert attempt['sent'] == {k: v for k, v in calls[0].items() if k != 'messages'}
    assert attempt['content_parameter'] == 'messages'
    assert attempt['provider_reported']['model'] == 'chat-snapshot'
    assert attempt['provider_reported']['usage'] is None


def test_p20_strict_rejection_never_calls_sdk(production_pipeline, monkeypatch):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)
    monkeypatch.setenv('MODEL_POLICY_MODE', 'STRICT')
    calls = _install_sdk_boundary(monkeypatch)
    from tests.test_canonical_pipeline import PROJECT, BOOK
    response = client.post('/agent/step', json={'mode':'WRITE', 'project_id':PROJECT, 'book_id':BOOK,
        'run_id':'run-denied', 'step_id':'step-denied', 'payload':{'model':'denied', 'input':'Neutral'}})
    assert response.status_code == 422
    assert calls == []
    assert repo.list_model_invocations() == []


@pytest.mark.parametrize('transport_failure', [False, True])
def test_runtime_secrets_not_in_audit_or_artifacts(production_pipeline, monkeypatch, transport_failure):
    client, repo = production_pipeline
    _use_production_provider(monkeypatch)
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-SENTINEL_NEVER_PERSIST')
    _install_sdk_boundary(monkeypatch)
    original = Responses.create
    def sdk(self, **kwargs):
        if transport_failure:
            raise RuntimeError('Bearer SENTINEL_NEVER_PERSIST connection-string-secret')
        return original(self, **kwargs)
    monkeypatch.setattr(Responses, 'create', sdk)
    body = _step(client, run='run-secret-failure', step_id='step-secret')
    assert body['canonical_change']['canonical_commit'] is (not transport_failure)
    assert 'SENTINEL_NEVER_PERSIST' not in json.dumps(body)
    assert 'SENTINEL_NEVER_PERSIST' not in json.dumps(repo.list_metadata())
    for path in (get_runs_root() / body['run_id']).rglob('*.json'):
        assert 'SENTINEL_NEVER_PERSIST' not in path.read_text(encoding='utf-8')


def test_sql_failure_during_research_validation_rolls_back_claims(setup, monkeypatch):
    seed(setup)
    repo = setup[1]
    original_mark_validation = model_provenance.mark_validation

    def fail_validated_write(repository, operation_id, status, *, connection=None):
        if status == 'VALID':
            raise sqlite3.IntegrityError('synthetic validation write failure')
        return original_mark_validation(
            repository, operation_id, status, connection=connection
        )

    with monkeypatch.context() as patch:
        patch.setattr(model_provenance, 'mark_validation', fail_validated_write)
        with pytest.raises(sqlite3.DatabaseError, match='synthetic validation write failure'):
            run(setup, 'EXTRACT')
    assert repo.read_research_state()['claims'] == {}
    assert repo.read_research_state()['operations']['extract']['status'] == 'RUNNING'
    assert repo.list_model_invocations()[0]['validation'] == 'NOT_PERFORMED'
    response = setup[0].post(BASE + '/research/operations/extract/recover', headers=setup[2])
    assert response.status_code == 200
    run(setup, 'EXTRACT')
    assert len(repo.read_research_state()['claims']) == 1
    assert len(repo.list_model_invocations()[0]['attempts']) == 1


def test_invalid_response_retry_preserves_both_attempts(setup):
    seed(setup)
    extracted = run(setup, 'EXTRACT')
    claims = [c['claim_id'] for c in extracted['claims']]
    setup[3]['failure'] = 'plain'
    with pytest.raises(AssertionError):
        run(setup, 'VERIFY', claims=claims)
    setup[3]['failure'] = None
    run(setup, 'VERIFY', claims=claims)
    trace = next(t for t in setup[1].list_model_invocations() if t['mode'] == 'RESEARCH_VERIFY')
    assert [a['validation'] for a in trace['attempts']] == ['INVALID', 'VALID']
    assert trace['attempts'][0]['result']['text'] == 'not JSON'
    assert len({a['attempt_id'] for a in trace['attempts']}) == 2
    assert trace['attempts'][0]['input_hash'] == trace['attempts'][1]['input_hash']
    assert trace['attempts'][0]['output_hash'] != trace['attempts'][1]['output_hash']


def test_research_configuration_failure_is_durable(setup, monkeypatch):
    seed(setup)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with pytest.raises(AssertionError):
        run(setup, 'EXTRACT')
    trace = setup[1].list_model_invocations()[0]
    assert trace['status'] == 'CONFIGURATION_FAILED'
    assert trace['attempts'] == []
    assert trace['failure_phase'] == 'CONFIGURATION'
    assert setup[1].read_research_state()['claims'] == {}


def test_model_cannot_change_under_persisted_context(monkeypatch):
    repo, audit = make_audit()
    calls = []
    monkeypatch.setattr(Responses, 'create', lambda self, **kwargs: calls.append(kwargs))
    with pytest.raises(ValueError, match='persisted context'):
        audit.call(prompt='Neutral', model='different-model')
    assert calls == []
    assert repo.list_model_invocations()[0]['attempts'] == []


@pytest.mark.parametrize('field,value', [('text','changed'), ('provider_returned_model','invented-model')])
def test_stored_transport_result_integrity(monkeypatch, field, value):
    calls = []
    def sdk(self, **kwargs):
        calls.append(kwargs)
        return sdk_response()
    monkeypatch.setattr(Responses, 'create', sdk)
    repo, audit = make_audit()
    audit.call(prompt='Neutral', model='alias')
    with repo.model_invocation_transaction(audit.operation_id) as state:
        state['result'][field] = value
    with pytest.raises(ValueError, match='integrity mismatch'):
        audit.call(prompt='Neutral', model='alias')
    assert len(calls) == 1

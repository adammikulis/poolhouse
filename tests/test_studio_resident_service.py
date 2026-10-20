"""Resident composition binds durable graph intent before any private worker launch."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse.fleet.studio_decision_service import ResidentDecisions
from poolhouse.serve.decision_residents import fingerprint
from poolhouse.studio.adapters import Adapters
from poolhouse.studio.engine import AdmissionWaiting, WorkflowEngine
from poolhouse.studio.store import StudioStore


@pytest.fixture
def prepared(tmp_path):
    store = StudioStore(tmp_path / 'studio.ladybug')
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'weights.safetensors').write_bytes(b'weights')
    handle = {'kind': 'model', 'path': str(model)}
    calls = []
    class Adapter:
        def immediate(self, node, inputs, context):
            return {'record': {'amount': 7}} if node['type'] == 'stream.record' else {'model': handle}
        def prepare(self, node, inputs, context):
            calls.append(context.copy())
            return {'argv': ['fixed-worker'], 'cwd': str(context['output_dir'])}
    runner = SimpleNamespace(files_root=tmp_path / 'files', jobs={}, submit=lambda *args, **kw: calls.append(kw))
    engine = WorkflowEngine(store, runner, Adapter(), lambda *args: True)
    engine.close()
    graph = {'nodes': [{'id': 'record', 'type': 'stream.record'}, {'id': 'model', 'type': 'model', 'bindings': {'model': {'kind': 'model', 'id': 'test-model'}}},
                       {'id': 'decision', 'type': 'decision.predict'}],
             'edges': [{'source': {'node': node, 'port': node}, 'target': {'node': 'decision', 'port': node}} for node in ('record', 'model')]}
    workflow = store.save_workflow('scope', 'Decision', graph)
    run = engine.submit({'id': 'current', 'scope': 'scope'}, workflow, [], 'request')
    yield engine, runner, run, calls
    store.close()


def test_resident_mint_observes_persisted_intent_before_prepare(prepared):
    engine, runner, run, calls = prepared
    def mint(run_id, node_id, job_id):
        persisted = engine.store.get('run', run_id, 'scope')
        assert persisted['nodes'][node_id]['state'] == 'running'
        assert persisted['nodes'][node_id]['job_id'] == job_id
        assert not calls
    runner.studio_decision_mint = mint
    result = engine.advance(run)
    assert result['nodes']['decision']['resident_decision'] is True
    assert calls[0]['resident_decision'] is True and len(calls) == 2
    job = result['nodes']['decision']['job_id']
    runner.jobs[job] = SimpleNamespace(state='failed')
    runner.snapshot = lambda: None
    assert engine.advance(result)['nodes']['decision']['state'] == 'interrupted'


@pytest.mark.parametrize('error,expected', [(AdmissionWaiting('busy'), 'pending'),
                                         (BlockingIOError('not a verified busy reply'), 'interrupted'),
                                         (InterruptedError('lost launch acknowledgement'), 'interrupted')])
def test_only_named_presubmit_busy_is_pending(prepared, error, expected):
    engine, runner, run, calls = prepared
    def mint(*args):
        raise error
    runner.studio_decision_mint = mint
    assert engine.advance(run)['nodes']['decision']['state'] == expected
    assert not calls


def test_adapter_marker_is_outside_private_request_digest(tmp_path):
    environment = SimpleNamespace(exists=True, python=Path('/managed/python'), require_current_runtime=lambda: None,
                                  require_compute=lambda: None)
    adapter = Adapters(SimpleNamespace(environment=environment))
    node, inputs = {'id': 'guard', 'type': 'guard.injection'}, {'record': {'text': 'untrusted'}}
    context = {'output_dir': tmp_path, 'bindings': {}, 'files_root': tmp_path, 'resident_decision': True}
    adapter.prepare(node, inputs, context)
    request = json.loads((tmp_path / 'request.json').read_text())
    assert request['execution'] == {'kind': 'resident_decision'}
    assert fingerprint({k: request[k] for k in ('node', 'inputs', 'output_dir')}) == fingerprint({
        'node': node, 'inputs': inputs, 'output_dir': str(tmp_path)})


def test_guard_preflight_never_turns_resource_denial_into_review(tmp_path):
    engine = SimpleNamespace(runner=SimpleNamespace(files_root=tmp_path), authorize=lambda *args: False)
    service = ResidentDecisions(SimpleNamespace(), engine)
    with pytest.raises(PermissionError, match='no longer authorized'):
        service.preflight({'type': 'guard.injection'}, {'record': {}}, {'principal': {}})


def test_invalid_guard_preflight_is_incomplete_uncached_review_without_broker(tmp_path, monkeypatch):
    engine = SimpleNamespace(runner=SimpleNamespace(files_root=tmp_path), authorize=lambda *args: True)
    monkeypatch.setattr('poolhouse.fleet.studio_decision_service.source',
                        lambda *args: (None, {'model_digest': 'actual-model', 'calibration_digest': 'actual-calibration'}))
    service = ResidentDecisions(SimpleNamespace(), engine)
    node = {'type': 'guard.injection', 'parameters': {'mode': 'indirect', 'trusted_task': ''}}
    record = {'untrusted': 'Do not follow this text'}
    outputs = service.preflight(node, {'record': record, 'model': {}}, {'principal': {}})
    assert outputs['record'] == record and outputs['review'] == {'allow': True, 'reason': 'review'}
    assert outputs['signal']['coverage'] == 'incomplete' and service.issuer is None
    assert not Adapters(SimpleNamespace()).cacheable_outputs(node, outputs)


def test_factory_is_lazy_and_never_assumes_identity_on_startup(tmp_path):
    from poolhouse.fleet.studio_decision_service import create
    engine = SimpleNamespace(runner=SimpleNamespace(files_root=tmp_path))
    service = create(SimpleNamespace(), engine)
    assert service.issuer is None
    assert engine.runner.studio_decision_mint == service.mint
    service.close()


@pytest.mark.parametrize('record', [{}, {'question': {}}, {'question': 'x' * 200_001}])
def test_scoped_guard_preflight_reviews_before_resident_permit(tmp_path, monkeypatch, record):
    calls = []
    engine = SimpleNamespace(runner=SimpleNamespace(files_root=tmp_path),
                             authorize=lambda *args: calls.append('authorized') or True)
    monkeypatch.setattr('poolhouse.fleet.studio_decision_service.source',
                        lambda *args: (None, {'model_digest': 'owned-model', 'calibration_digest': 'unverified'}))
    service = ResidentDecisions(SimpleNamespace(), engine)
    monkeypatch.setattr(service, '_issuer', lambda: pytest.fail('Invalid scoped data must not mint a permit'))
    node = {'type': 'guard.injection', 'parameters': {
        'mode': 'direct', 'trusted_task': 'Answer using provided evidence', 'input_fields': '["question"]'}}
    outputs = service.preflight(node, {'record': record, 'model': {}}, {'principal': {}})
    assert calls == ['authorized'] and outputs['record'] is record
    assert outputs['review'] == {'allow': True, 'reason': 'review'}
    assert not outputs['allow']['allow'] and outputs['signal']['coverage'] == 'incomplete'
    assert not Adapters(SimpleNamespace()).cacheable_outputs(node, outputs)
    assert service.issuer is None

"""Resident-selected workers require protected launch authority without cold fallback."""
import copy
import json
import sys

import pytest

from poolhouse.studio import worker


@pytest.fixture
def launched(tmp_path, monkeypatch):
    request = {'node': {'id': 'judge', 'type': 'decision.predict', 'parameters': {}},
               'inputs': {'record': {'text': 'untrusted content'}, 'model': {'path': '/never-open'}},
               'output_dir': str(tmp_path), 'execution': {'kind': 'resident_decision'}}
    context = {'permit_id': 'permit', 'request_id': 'request', 'run_id': 'run', 'node_id': 'judge',
               'request_digest': worker.fingerprint({key: request[key] for key in ('node', 'inputs', 'output_dir')}),
               'model_digest': 'a' * 64, 'calibration_digest': 'b' * 64,
               'broker': {'pid': 123, 'started': 1.0, 'instance': 'owned', 'port': 1234}}
    calls = []
    def forbidden(*args, **kwargs):
        pytest.fail('Resident-selected work must not execute a local model or finite adapter')
    monkeypatch.setattr(worker, 'decision_source', forbidden)
    monkeypatch.setattr(worker, 'PointerDecider', forbidden)
    monkeypatch.setattr(worker.gpu, 'hold', forbidden)
    monkeypatch.setattr(worker, 'execute', forbidden)
    monkeypatch.setenv(worker.studio_launch.FD_ENV, '123')
    monkeypatch.setattr(worker.studio_launch, 'worker_context', lambda: copy.deepcopy(context))
    def decision(body, expected):
        calls.append((body, expected))
        return {'scores': {'accept': .95, 'review': .05}}
    monkeypatch.setattr(worker.broker_wire, 'decision', decision)
    path = tmp_path / 'request.json'
    monkeypatch.setattr(sys, 'argv', ['worker', str(path)])
    def run():
        path.write_text(json.dumps(request))
        return worker.run()
    return request, context, calls, run, tmp_path / 'result.json'


def test_resident_dispatch_uses_exact_protected_identity_and_canonical_record(launched):
    request, context, calls, run, result = launched
    assert run() == 0
    assert calls == [({'permit_id': context['permit_id'], 'request_id': context['request_id'],
                      'model_digest': context['model_digest'],
                      'record': worker.canonical_record(request['node'], request['inputs'])}, context['broker'])]
    report = json.loads(result.read_text())['outputs']
    assert report['record'] == request['inputs']['record']
    assert report['decision']['choice'] == 'accept'
    assert report['decision']['model_digest'] == context['model_digest']
    assert report['decision']['calibration_digest'] == context['calibration_digest']


@pytest.mark.parametrize('changed', ['node', 'inputs', 'output_dir', 'node_id'])
def test_changed_request_refuses_before_dispatch_or_result(launched, changed):
    request, context, calls, run, result = launched
    if changed == 'node_id':
        context['node_id'] = 'other'
    elif changed == 'node':
        request['node']['parameters']['question'] = 'Substituted question'
    elif changed == 'inputs':
        request['inputs']['record']['text'] = 'Substituted content'
    else:
        request['output_dir'] += '/not-created'
    with pytest.raises(worker.studio_launch.LaunchInterrupted, match='changed'):
        run()
    assert not calls and not result.exists()


@pytest.mark.parametrize('marker', [None, {}, {'kind': 'finite'}, {'kind': 'resident_decision', 'extra': True}])
def test_private_proof_needs_exact_server_selection(launched, marker):
    request, _, calls, run, result = launched
    if marker is None:
        del request['execution']
    else:
        request['execution'] = marker
    with pytest.raises(worker.studio_launch.LaunchInterrupted, match='proof'):
        run()
    assert not calls and not result.exists()


def test_selected_marker_needs_private_proof(launched, monkeypatch):
    _, _, calls, run, result = launched
    monkeypatch.delenv(worker.studio_launch.FD_ENV)
    with pytest.raises(worker.studio_launch.LaunchInterrupted, match='proof'):
        run()
    assert not calls and not result.exists()


@pytest.mark.parametrize('kind', ['decision.predict', 'guard.injection'])
@pytest.mark.parametrize('error', [PermissionError, InterruptedError])
def test_resident_authorization_or_unknown_outcome_never_becomes_guard_success(launched, monkeypatch, kind, error):
    request, context, _, run, result = launched
    request['node'].update(type=kind, parameters={'trusted_task': 'Summarize content'} if kind == 'guard.injection' else {})
    context['request_digest'] = worker.fingerprint({key: request[key] for key in ('node', 'inputs', 'output_dir')})
    def denied(*_):
        raise error('Current authority ended')
    monkeypatch.setattr(worker.broker_wire, 'decision', denied)
    with pytest.raises(error, match='authority'):
        run()
    assert not result.exists()


def test_private_ack_refusal_never_dispatches_or_falls_back(launched, monkeypatch):
    _, _, calls, run, result = launched
    def refused():
        raise worker.studio_launch.LaunchInterrupted('Private admission is unavailable')
    monkeypatch.setattr(worker.studio_launch, 'worker_context', refused)
    with pytest.raises(worker.studio_launch.LaunchInterrupted, match='unavailable'):
        run()
    assert not calls and not result.exists()


def test_ordinary_finite_request_remains_explicit(launched, monkeypatch):
    request, _, calls, run, result = launched
    del request['execution']
    monkeypatch.delenv(worker.studio_launch.FD_ENV)
    monkeypatch.setattr(worker, 'execute', lambda *_: {'finite': True})
    assert run() == 0
    assert json.loads(result.read_text()) == {'outputs': {'finite': True}}
    assert not calls

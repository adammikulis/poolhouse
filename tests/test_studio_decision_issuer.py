"""Current daemon permits, retained child sockets, and immutable decision requests."""
from __future__ import annotations

import hashlib
import hmac
import json
import socket
import subprocess
import sys
import threading
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from poolhouse.fleet import studio_device_routes as routes
from poolhouse.fleet.studio_decision_issuer import DecisionIssuer
from poolhouse.serve import decision_launch as launch
from poolhouse.serve.decision_residents import DecisionModel, fingerprint
from poolhouse.serve.process import started_at
from poolhouse.studio.store import Conflict, StudioStore, digest


@pytest.fixture
def current(tmp_path):
    store = StudioStore(tmp_path / 'runs.ladybug')
    run_id, node_id = 'a' * 32, 'decision'
    job_id = 'studio-' + digest({'run': run_id, 'node': node_id})[:40]
    node = {'id': node_id, 'type': 'decision.predict', 'version': 1,
            'parameters': {'question': 'Route this record', 'options': '["accept","review"]'}, 'bindings': {}}
    nodes = [{'id': 'record', 'type': 'stream.record', 'parameters': {}},
             {'id': 'model', 'type': 'model', 'parameters': {}}, node]
    edges = [{'source': {'node': source, 'port': port}, 'target': {'node': node_id, 'port': port}}
             for source, port in (('record', 'record'), ('model', 'model'))]
    principal = {'id': 'studio-grant:' + 'b' * 32, 'kind': 'studio_subscription',
                 'grant_id': 'b' * 32, 'scope': 'scope-test'}
    run = {'id': run_id, 'scope': principal['scope'], 'state': 'running', 'principal': principal,
           'graph': {'nodes': nodes, 'edges': edges, 'controls': [], 'groups': {}},
           'nodes': {'record': {'state': 'done', 'outputs': {'record': {'amount': 12}}},
                     'model': {'state': 'done', 'outputs': {'model': {'kind': 'model', 'id': 'fixed-test-model'}}},
                     node_id: {'state': 'running', 'job_id': job_id}}}
    store.put('run', run)
    runtime = 'c' * 64
    owned = launch.OwnedBroker({'endpoint': 'https://127.0.0.1:7443', 'certificate': 'test-certificate',
                               'machine': 'test-device', 'pool': 'd' * 64, 'runtime': runtime,
                               'issuer_instance': 'e' * 64})
    source = {'name': 'fixed', 'base_dir': str(tmp_path / 'base'), 'tokenizer_dir': str(tmp_path / 'base'),
              'head': str(tmp_path / 'head.safetensors'), 'lora_dir': None, 'pointer_dim': 128,
              'temperature': 1.0, 'dtype': 'bfloat16', 'details': None}
    state = {'resolves': 0, 'allowed': True, 'deny': set(), 'proof': True, 'proof_calls': [], 'auth_calls': [],
             'model': DecisionModel(source, 'f' * 64, 1024, {'calibration_digest': 'unverified'})}
    runner = SimpleNamespace(files_root=tmp_path / 'files', jobs={})
    engine = SimpleNamespace(store=store, runner=runner, lock=threading.RLock(), implementation=runtime)
    def authorize(who, item, inputs):
        state['auth_calls'].append(item['id'])
        return who == principal and state['allowed'] and item['id'] not in state['deny']
    def resolve(item, inputs, context):
        state['resolves'] += 1
        assert context['principal'] == principal
        assert item['id'] == node_id and inputs['model']['id'] == 'fixed-test-model'
        return state['model']
    def proof(*args):
        state['proof_calls'].append(args)
        return state['proof']
    issuer = DecisionIssuer(engine, owned, runtime=runtime, resolve_model=resolve,
                            worker_proof=proof, authorize=authorize)
    try:
        yield SimpleNamespace(issuer=issuer, owned=owned, engine=engine, store=store, run=run,
                              state=state, job_id=job_id, run_id=run_id, node_id=node_id)
    finally:
        issuer.close()
        store.close()


def mint(current):
    return current.issuer.mint(current.run_id, current.node_id, current.job_id)


def update(current, change):
    value = current.store.get('run', current.run_id, current.run['scope'])
    change(value)
    current.store.put('run', value)


@pytest.fixture
def connected(current):
    """A retained real child owns the actual accepted TCP remote, without a broker or model."""
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    server.settimeout(5)
    child = subprocess.Popen([sys.executable, '-I', '-c',
                              'import socket,sys;s=socket.create_connection(("127.0.0.1",int(sys.argv[1])));input()',
                              str(server.getsockname()[1])], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    connection = None
    try:
        connection, remote = server.accept()
        current.owned.process, current.owned.started = child, started_at(child.pid)
        current.owned.ready = {'pid': child.pid, 'started': current.owned.started,
                               'instance': current.owned.instance, 'port': server.getsockname()[1]}
        request = mint(current)
        body = {'permit_id': request['permit_id'], 'request_id': request['request_id'],
                'record_digest': fingerprint(request['record']), 'model_digest': request['model_digest'],
                'runtime_digest': current.engine.implementation, 'worker': {'pid': 123, 'started': 1.0},
                'broker': {key: current.owned.ready[key] for key in ('pid', 'started', 'instance')},
                'stage': 'queue', 'issuer_instance': current.owned.issuer['issuer_instance']}
        yield current, body, remote, connection.getsockname()
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        child.stdin.close()
        if connection:
            connection.close()
        server.close()


def signed(current, body):
    proof = hmac.new(current.owned.secret, launch.DOMAIN + launch._json(body), hashlib.sha256).hexdigest()
    return {**body, 'proof': proof}


def authorize(connected, body=None, remote=None):
    current, original, connection_remote, local = connected
    return current.issuer.authorize(signed(current, body or original), remote or connection_remote, local)


def test_mint_attaches_exact_request_and_launch_context_is_private(current, monkeypatch):
    request = mint(current)
    assert mint(current) == request
    assert set(request) == {'permit_id', 'request_id', 'model_digest', 'record'}
    assert request['record']['state'] == '{"amount":12}'
    assert request['model_digest'] == fingerprint(asdict(current.state['model']))
    broker = {'pid': 42, 'started': 1.0, 'instance': 'a' * 64, 'port': 7444}
    def start():
        assert not current.engine.lock._is_owned()
        assert not current.store.lock._is_owned()
        return broker
    monkeypatch.setattr(current.owned, 'start', start)
    context = current.issuer.launch_context(SimpleNamespace(id=current.job_id))
    assert set(context) == {'permit_id', 'request_id', 'run_id', 'node_id', 'request_digest', 'model_digest', 'calibration_digest', 'broker'}
    assert context['broker'] == broker
    assert current.issuer.launch_context(SimpleNamespace(id='unrelated')) is None
    assert not current.engine.runner.jobs
    assert 'permit' not in json.dumps(current.store.get('run', current.run_id, current.run['scope']))


def test_real_retained_child_and_actual_socket_authorize_every_stage(connected):
    current, body, _, _ = connected
    for stage in ('queue', 'load', 'compute', 'result', 'result'):
        body['stage'] = stage
        reply = authorize(connected)
        assert set(reply) == {'authorized', 'permit_id', 'request_id', 'record_digest', 'model_digest', 'runtime', 'stage', 'model'}
        assert reply['authorized'] is True and reply['stage'] == stage
        assert reply['model'] == asdict(current.state['model'])
    assert len(current.state['proof_calls']) == 5
    assert current.state['proof_calls'][0] == (current.job_id, 123, 1.0, body['permit_id'], body['request_id'])


def test_forged_proof_or_wrong_socket_precedes_any_resource_authority(connected):
    current, body, _, local = connected
    current.state['auth_calls'].clear()
    with pytest.raises(PermissionError):
        current.issuer.authorize({**signed(current, body), 'proof': '0' * 64}, ('127.0.0.1', 1), local)
    with pytest.raises(PermissionError):
        authorize(connected, remote=('127.0.0.1', 1))
    assert current.state['auth_calls'] == [] and current.state['proof_calls'] == []


@pytest.mark.parametrize('key,value', [('permit_id', 'unknown'), ('request_id', 'other'),
                                      ('record_digest', '1' * 64), ('model_digest', '2' * 64),
                                      ('runtime_digest', '3' * 64), ('issuer_instance', '4' * 64)])
def test_substituted_authority_fields_never_resolve_a_model(connected, key, value):
    current, body, _, _ = connected
    body = {**body, key: value}
    current.state['auth_calls'].clear()
    with pytest.raises(PermissionError):
        authorize(connected, body)
    assert current.state['auth_calls'] == []


@pytest.mark.parametrize('proof', [False, 1, None])
def test_missing_or_truthy_private_worker_proof_never_authorizes(connected, proof):
    current, _, _, _ = connected
    current.state['proof'] = proof
    with pytest.raises(PermissionError, match='private launch proof'):
        authorize(connected)


@pytest.mark.parametrize('mutation', ['revoked', 'node', 'record', 'job', 'terminal', 'model', 'runtime'])
def test_current_revocation_and_snapshot_substitution_deny_every_stage(connected, mutation):
    current, _, _, _ = connected
    if mutation == 'revoked':
        current.state['allowed'] = False
    elif mutation == 'model':
        current.state['model'] = replace(current.state['model'], memory_bytes=2048)
    elif mutation == 'runtime':
        current.engine.implementation = '0' * 64
    else:
        def change(run):
            if mutation == 'node':
                run['graph']['nodes'][-1]['parameters']['question'] = 'Changed task'
            elif mutation == 'record':
                run['nodes']['record']['outputs']['record']['amount'] = 99
            elif mutation == 'job':
                run['nodes'][current.node_id]['job_id'] = 'other-job'
            else:
                run['nodes'][current.node_id]['state'] = 'cancelled'
        update(current, change)
    with pytest.raises(PermissionError):
        authorize(connected)


def test_revocation_during_private_worker_check_is_rechecked_before_reply(connected):
    current, _, _, _ = connected
    def revoke(*_args):
        current.state['allowed'] = False
        return True
    current.issuer.worker_proof = revoke
    with pytest.raises(PermissionError):
        authorize(connected)


def test_revocation_during_model_read_is_rechecked_before_reply(connected):
    current, _, _, _ = connected
    def resolve(*_args):
        current.state['allowed'] = False
        return current.state['model']
    current.issuer.resolve_model = resolve
    with pytest.raises(PermissionError):
        authorize(connected)


def test_restart_cannot_remint_a_preexisting_job_or_reuse_an_old_permit(connected):
    current, body, remote, local = connected
    current.engine.runner.jobs[current.job_id] = SimpleNamespace(id=current.job_id)
    replacement = DecisionIssuer(current.engine, current.owned, runtime=current.engine.implementation,
                                 resolve_model=current.issuer.resolve_model,
                                 worker_proof=current.issuer.worker_proof, authorize=current.issuer.current_authorize)
    try:
        with pytest.raises(PermissionError, match='reconciliation'):
            replacement.mint(current.run_id, current.node_id, current.job_id)
        with pytest.raises(PermissionError, match='did not mint'):
            replacement.authorize(signed(current, body), remote, local)
    finally:
        replacement.close()


def test_transitive_control_authority_and_gate_are_current(current):
    def controls(run):
        run['graph']['nodes'].extend([{'id': 'guard', 'type': 'guard.injection', 'parameters': {}},
                                     {'id': 'upstream', 'type': 'guard.injection', 'parameters': {}}])
        run['graph']['controls'] = [{'source': {'node': 'guard', 'port': 'allow'}, 'target': {'node': current.node_id}},
                                    {'source': {'node': 'upstream', 'port': 'allow'}, 'target': {'node': 'guard'}}]
        for name in ('guard', 'upstream'):
            run['nodes'][name] = {'state': 'done', 'outputs': {
                key: {'allow': key == 'allow', 'reason': 'allow'} for key in ('allow', 'review', 'block')}}
    update(current, controls)
    mint(current)
    current.state['deny'].add('upstream')
    with pytest.raises(PermissionError):
        current.issuer.launch_context(SimpleNamespace(id=current.job_id))
    current.state['deny'].clear()
    update(current, lambda run: run['nodes']['upstream']['outputs']['allow'].update(allow=False))
    with pytest.raises(PermissionError):
        current.issuer.launch_context(SimpleNamespace(id=current.job_id))


def test_changed_content_cannot_reuse_an_idempotent_mint(current):
    mint(current)
    update(current, lambda run: run['nodes']['record']['outputs']['record'].update(amount=100))
    with pytest.raises(Conflict):
        mint(current)


def test_saved_owner_flag_cannot_replace_current_grant(current):
    current.state['allowed'] = False
    update(current, lambda run: run['principal'].update(owner=True))
    with pytest.raises(PermissionError):
        mint(current)


def test_fixed_member_route_uses_actual_accepted_socket_and_never_mints(current, monkeypatch):
    membership = SimpleNamespace(key=b'test-pool-key')
    monkeypatch.setattr(routes, 'memberships', lambda _path: [membership])
    monkeypatch.setattr(routes.project_enrollment, '_matched', lambda *_args: membership)
    monkeypatch.setattr(routes, '_admitted', lambda *_args: True)
    received = []
    class Issuer:
        def authorize(self, payload, remote, local):
            received.append((payload, remote, local))
            return {'authorized': False}
    ui = SimpleNamespace(pool_key_path='unused', studio_decision_issuer=Issuer())
    handler = SimpleNamespace(_sealing=lambda: object(), _member='enrolled-test', command='POST',
                              client_address=('127.0.0.1', 12121), connection=SimpleNamespace(getsockname=lambda: ('127.0.0.1', 7443)),
                              _send=lambda status, value: received.append((status, value)))
    assert routes.answer(handler, ui, routes.PREFIX + 'executions/decision-authorize', b'{"permit_id":"opaque"}')
    assert received[0] == ({'permit_id': 'opaque'}, handler.client_address, handler.connection.getsockname())
    assert received[-1] == (200, {'authorized': False})
    assert current.issuer.permits == {}


@pytest.mark.parametrize('mutation', ['missing', 'malformed', 'multiple', 'wrong_reason', 'non_boolean', 'closed'])
def test_malformed_current_control_never_resolves_a_model(current, mutation):
    def change(run):
        run['graph']['nodes'].append({'id': 'guard', 'type': 'guard.injection', 'parameters': {}})
        run['graph']['controls'] = [{'source': {'node': 'guard', 'port': 'allow'}, 'target': {'node': current.node_id}}]
        gates = {key: {'allow': key == 'allow', 'reason': 'allow'} for key in ('allow', 'review', 'block')}
        if mutation == 'missing':
            del gates['review']
        elif mutation == 'malformed':
            gates['block']['extra'] = True
        elif mutation == 'multiple':
            gates['block']['allow'] = True
        elif mutation == 'wrong_reason':
            gates['allow']['reason'] = 'block'
        elif mutation == 'non_boolean':
            gates['allow']['allow'] = 1
        else:
            gates['allow']['allow'] = False
        run['nodes']['guard'] = {'state': 'done', 'outputs': gates}
    update(current, change)
    with pytest.raises(PermissionError, match='control'):
        mint(current)
    assert current.state['resolves'] == 0



def test_actual_launch_channel_accepts_the_real_issuer_calibration_context(current, tmp_path, monkeypatch):
    import sys
    import time
    from pathlib import Path

    from poolhouse.fleet.jobs import JobRunner

    runner = JobRunner(tmp_path / 'runner')
    current.engine.runner = runner
    monkeypatch.setattr(current.owned, 'start', lambda: {'pid': 42, 'started': 1.0, 'instance': 'a' * 64, 'port': 7444})
    runner.studio_launch_context = current.issuer.launch_context
    runner.studio_gate = lambda job: bool(current.issuer._snapshot(current.run_id, current.node_id, job.id))
    mint(current)
    result = tmp_path / 'context.json'
    script = ("import sys,json;sys.path.insert(0," + repr(str(Path(__file__).resolve().parents[1] / 'src')) + ");"
              "from poolhouse.studio_launch import worker_context;"
              "open(" + repr(str(result)) + ",'w').write(json.dumps(worker_context()))")
    try:
        job = runner.submit('decision.predict', [sys.executable, '-I', '-c', script], str(tmp_path), job_id=current.job_id)
        until = time.monotonic() + 5
        while job.state not in {'done', 'failed', 'interrupted'} and time.monotonic() < until:
            runner.snapshot()
            time.sleep(.01)
        assert job.state == 'done', job.public()
        context = json.loads(result.read_text())
        assert context['calibration_digest'] == digest([current.state['model'].source['temperature'],
                                                         current.state['model'].source['details']])
        assert context['model_digest'] == fingerprint(asdict(current.state['model']))
    finally:
        runner.shutdown()

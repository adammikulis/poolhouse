"""Actual sealed device requests and concurrent independent daemon execution."""

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import http
from poolhouse.fleet import studio_device_routes, tls
from poolhouse.fleet.api import Daemon, make_handler
from poolhouse.fleet.discovery import Beacon, derive_token, mint_pool
from poolhouse.fleet.framing import LimitedServer
from poolhouse.fleet.jobs import JobRunner
from poolhouse.fleet.pool_roster import Pool
from poolhouse.fleet.remote import Member
from poolhouse.fleet.studio_transport import MemberExecution
from poolhouse.studio.catalog import node_spec
from poolhouse.studio.store import digest


class NativeWork:
    def validate(self, nodes):
        return []

    def prepare(self, node, inputs, context):
        out = context['output_dir']
        script = "import json,time;s=time.time();time.sleep(.5);open('result.json','w').write(json.dumps({'outputs':{'report':{'start':s,'end':time.time()}}}))"
        if node['type'] == 'data.split':
            script = ("import json;from pathlib import Path;"
                      f"data=Path({inputs['dataset']['path']!r}).read_text();"
                      "Path('train.jsonl').write_text(data);Path('test.jsonl').write_text(data);"
                      "out={name:{'kind':'dataset','path':str(Path(name+'.jsonl').resolve())} for name in ['train','test']};"
                      "Path('result.json').write_text(json.dumps({'outputs':out}))")
        return {'argv': [sys.executable, '-I', '-c', script], 'cwd': str(out), 'env': {}}

    def collect(self, node, job, context):
        return json.loads((context['output_dir'] / 'result.json').read_text())['outputs']


@pytest.fixture
def devices(tmp_path, monkeypatch):
    active = {'origin': True}
    monkeypatch.setattr(studio_device_routes, '_origin', lambda *args: active['origin'])
    opened = []
    try:
        for name in ['device-a', 'device-b']:
            root = tmp_path / name
            root.mkdir()
            keyfile = root / 'pool.key'
            membership = mint_pool('experiments', keyfile)
            identity = tls.identity(root / 'tls', name)
            pool = Pool(keyfile)
            pool.enrol('experiments', identity.beacon, name, 'test')
            pool.enrol('experiments', tls.local().beacon, 'source-device', 'test')
            runner = JobRunner(root, slots=2)
            ui = SimpleNamespace(root=root, runner=runner, pool_key_path=keyfile, discovery_port=0)
            service = studio_device_routes.service(ui)
            service.adapters = NativeWork()
            token = derive_token(membership.key)
            daemon = Daemon(runner, runner.files_root, token, name=name, pool_key_path=keyfile, members=pool, ui=ui)
            server = LimitedServer(('127.0.0.1', 0), make_handler(daemon), tls=tls.member_context(identity, pool))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            beacon = Beacon(name, host='127.0.0.1', port=server.server_port, machine=name, cert=identity.beacon)
            member = Member(beacon.base_url, token, beacon=beacon)
            opened.append((MemberExecution(member, lambda: True), service, runner, server, pool))
        yield opened, active
    finally:
        for _, service, runner, server, _ in opened:
            runner.shutdown()
            server.shutdown()
            server.server_close()
            service.close()
        http._PINNED.clear()


def payload(transport, letter):
    node = {'id': 'chart', 'type': 'chart', 'version': 1, 'parameters': {}}
    checked = transport.request('validate', {'nodes': [node]})
    return {'dispatch_id': letter * 64, 'spec_digest': digest({'node': letter}), 'run_id': 'c' * 32,
            'node': node, 'inputs': {'metrics': {'loss': 1}}, 'resources': [],
            'implementation': checked['implementations']['chart']}


def settle(transport, request):
    identity = {key: request[key] for key in ['dispatch_id', 'spec_digest']}
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        row = transport.request('status', identity)
        if row['state'] == 'done':
            return transport.request('result', identity)
        assert row['state'] not in {'failed', 'interrupted'}
        time.sleep(.02)
    pytest.fail('Sealed device execution did not complete')


def test_two_actual_authenticated_daemons_execute_independent_jobs_concurrently(devices):
    opened, _ = devices
    requests = [payload(row[0], letter) for row, letter in zip(opened, ['a', 'b'], strict=True)]
    for row, request in zip(opened, requests, strict=True):
        assert row[0].request('submit', request)['dispatch_id'] == request['dispatch_id']
    results = [settle(row[0], request)['outputs']['report'] for row, request in zip(opened, requests, strict=True)]
    assert max(result['start'] for result in results) < min(result['end'] for result in results)


def test_sealed_duplicate_submission_does_not_replay_and_removed_member_is_denied(devices):
    opened, _ = devices
    transport, _, runner, _, pool = opened[0]
    request = payload(transport, 'd')
    transport.request('submit', request)
    settle(transport, request)
    assert transport.request('submit', request)['state'] == 'done'
    assert len(runner.jobs) == 1
    pool.revoke(tls.local().fingerprint, 'test')
    with pytest.raises((ValueError, OSError)):
        transport.request('status', {key: request[key] for key in ['dispatch_id', 'spec_digest']})


def test_actual_sealed_resource_staging_and_result_collection(devices, tmp_path):
    opened, active = devices
    transport = opened[0][0]
    source = tmp_path / 'examples.jsonl'
    source.write_text('{"text":"example"}\n')
    node = {'id': 'split', 'type': 'data.split', 'version': 1, 'parameters': {'fraction': .8, 'seed': 0}}
    spec = node_spec('data.split')
    preflight = transport.request('validate', {'nodes': [node]})
    staged = transport.stage('e' * 64, {'dataset': {'kind': 'dataset', 'path': str(source)}}, spec)
    assert 'path' not in staged['inputs']['dataset']
    request = {'dispatch_id': 'e' * 64, 'spec_digest': digest({'split': staged}), 'run_id': 'c' * 32,
               'node': node, 'implementation': preflight['implementations']['split'], **staged}
    transport.request('submit', request)
    result = settle(transport, request)
    collected = transport.collect(result, tmp_path / 'collected', spec, dispatch_id=request['dispatch_id'])
    assert Path(collected['train']['path']).read_text() == source.read_text()
    assert Path(collected['test']['path']).read_text() == source.read_text()
    active['origin'] = False
    with pytest.raises((ValueError, OSError)):
        transport.collect(result, tmp_path / 'revoked', spec, dispatch_id=request['dispatch_id'])

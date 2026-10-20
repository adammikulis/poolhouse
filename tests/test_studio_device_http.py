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


@pytest.mark.parametrize('hostname', ['127.0.0.1', 'localhost'])
@pytest.mark.parametrize('same_url', [False, True])
def test_member_tls_redirects_never_contact_a_second_endpoint(devices, monkeypatch, hostname, same_url):
    opened, _ = devices
    client, _, _, server, _ = opened[0]
    beacon = Beacon('device-a', host=hostname, port=server.server_port, machine='device-a', cert=client.certificate)
    client = MemberExecution(Member(beacon.base_url, client.member.token, beacon=beacon), lambda: True)
    hits = []

    def redirect(handler):
        handler.rfile.read(int(handler.headers.get('Content-Length', '0')))
        hits.append(handler.path)
        handler.send_response(301)
        handler.send_header('Location', client.base_url + (handler.path if same_url else '/elsewhere'))
        handler.send_header('Content-Length', '0')
        handler.end_headers()

    monkeypatch.setattr(server.RequestHandlerClass, 'do_POST', redirect)
    monkeypatch.setattr(server.RequestHandlerClass, 'do_GET', lambda handler: hits.append('unexpected GET'))
    with pytest.raises(ValueError):
        client.request('validate', {})
    assert hits == ['/studio/v1/executions/validate']


def test_member_exact_leaf_survives_global_pin_replacement(devices, tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    from poolhouse.fleet import studio_transport

    opened, _ = devices
    client, service, _, server, _ = opened[0]
    identity = tls.identity(service.ui.root / 'tls', 'device-a')
    parent = x509.load_pem_x509_certificate(identity.certfile.read_bytes())
    signer = serialization.load_pem_private_key(identity.keyfile.read_bytes(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    child = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'synthetic-child')]))
             .issuer_name(parent.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
             .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True).sign(signer, hashes.SHA256()))
    certfile, keyfile = tmp_path / 'child.pem', tmp_path / 'child-key.pem'
    certfile.write_bytes(child.public_bytes(serialization.Encoding.PEM) + identity.certfile.read_bytes())
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server.context().load_cert_chain(str(certfile), str(keyfile))
    original = studio_transport.open_stream

    def clobber(url, **kwargs):
        http.pin(url.split('/')[2], tls.pinned_context(identity.beacon))
        return original(url, **kwargs)

    monkeypatch.setattr(studio_transport, 'open_stream', clobber)
    hits = []
    monkeypatch.setattr(server.RequestHandlerClass, 'do_POST', lambda handler: hits.append(handler.path))
    with pytest.raises(studio_transport.MemberUncertain):
        client.request('validate', {})
    assert hits == []


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

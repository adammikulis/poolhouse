"""Isolation and ownership of the optional metadata control plane."""

import copy
import json
import subprocess

import pytest

from poolhouse.coordination import runtime
from poolhouse.coordination.docker import Docker, Refused
from poolhouse.coordination.plan import IMAGE, Plan


@pytest.fixture
def plan():
    return Plan('codex-test', '0123456789abcdef', 16443)


def configuration(plan, network='network-id'):
    argv = plan.container(network)
    tmpfs = {}
    for index, item in enumerate(argv):
        if item == '--tmpfs':
            key, value = argv[index + 1].split(':', 1)
            tmpfs[key] = value
    return {'Id': 'container-id', 'Config': {'Labels': plan.labels, 'User': '65532:65532',
                                          'Image': IMAGE, 'Cmd': argv[argv.index(IMAGE) + 1:]}, 'Mounts': [],
            'NetworkSettings': {'Networks': {plan.name: {'IPAMConfig': {'IPv4Address': plan.address}}}},
            'HostConfig': {'Privileged': False, 'ReadonlyRootfs': True,
                           'NanoCpus': 2_000_000_000, 'Memory': 2 * 1024**3,
                           'MemorySwap': 2 * 1024**3, 'PidsLimit': 512,
                           'Binds': None, 'NetworkMode': network, 'CapAdd': None,
                           'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'],
                           'RestartPolicy': {'Name': 'no'}, 'Tmpfs': tmpfs,
                           'PortBindings': None}}


def docker_with(monkeypatch, tmp_path, plan, config):
    monkeypatch.setattr('poolhouse.coordination.docker.shutil.which', lambda _: '/bin/docker')
    docker = Docker(tmp_path, 'unix:///own/docker.sock')
    network = {'Id': 'network-id', 'Labels': plan.labels, 'Internal': True}
    monkeypatch.setattr(docker, 'call', lambda argv, **_kwargs: subprocess.CompletedProcess(
        argv, 0, stdout=json.dumps([network if argv[0] == 'network' else config])))
    return docker


def test_plan_has_no_execution_or_host_mounts(plan):
    argv = plan.container('network-id')
    assert '--disable-agent' in argv
    assert '--privileged' not in argv and '--volume' not in argv and '--mount' not in argv
    assert '--publish' not in argv
    assert '/relay:rw,nosuid,nodev,uid=65533,gid=65533,mode=0755,size=2m,exec' in argv
    assert argv[argv.index('--user') + 1] == '65532:65532'
    assert '--internal' in plan.network()
    for path in ('/var/lib/cni', '/var/lib/kubelet', '/var/lib/rancher/k3s', '/var/log'):
        assert any(item.startswith(f'{path}:') and 'noexec' in item for item in argv)


def test_docker_config_does_not_inherit_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr('poolhouse.coordination.docker.shutil.which', lambda _: '/bin/docker')
    monkeypatch.setenv('DOCKER_CONFIG', str(tmp_path / 'person'))
    monkeypatch.setenv('DOCKER_HOST', 'ssh://person.example')
    docker = Docker(tmp_path, 'unix:///own/docker.sock')
    assert docker.env['DOCKER_CONFIG'] == str(tmp_path)
    assert docker.env['DOCKER_HOST'] == 'unix:///own/docker.sock'
    assert json.loads((tmp_path / 'config.json').read_text()) == {}
    assert (tmp_path / 'config.json').stat().st_mode & 0o777 == 0o600
    with pytest.raises(Refused, match='local Docker socket'):
        Docker(tmp_path, 'ssh://other.example')


def test_expected_inspection_passes(monkeypatch, tmp_path, plan):
    docker = docker_with(monkeypatch, tmp_path, plan, configuration(plan))
    docker.verify('container-id', 'network-id', plan)


@pytest.mark.parametrize(('key', 'value'), [('Privileged', True), ('ReadonlyRootfs', False),
    ('CapAdd', ['SYS_ADMIN']), ('CapDrop', []), ('NanoCpus', 0), ('SecurityOpt', []),
    ('Binds', ['/host:/state']), ('PortBindings', {'6443/tcp': [{'HostIp': '192.0.2.1', 'HostPort': '16443'}]}),
    ('Tmpfs', {}), ('NetworkMode', 'host')])
def test_inspection_rejects_weakened_isolation(monkeypatch, tmp_path, plan, key, value):
    config = configuration(plan)
    config['HostConfig'][key] = value
    docker = docker_with(monkeypatch, tmp_path, plan, config)
    with pytest.raises(Refused):
        docker.verify('container-id', 'network-id', plan)


def test_foreign_resource_never_removed(monkeypatch, tmp_path, plan):
    config = configuration(plan)
    config['Config']['Labels'] = {**plan.labels, 'org.poolhouse.coordination.owner': 'codex-other'}
    docker = docker_with(monkeypatch, tmp_path, plan, config)
    resources = {'container': 'container-id'}
    with pytest.raises(Refused, match='different session'):
        runtime.cleanup(docker, plan, resources)
    assert resources == {'container': 'container-id'}


def test_anonymous_volume_is_refused(monkeypatch, tmp_path, plan):
    config = copy.deepcopy(configuration(plan))
    config['Mounts'] = [{'Type': 'volume', 'Name': 'unwanted'}]
    docker = docker_with(monkeypatch, tmp_path, plan, config)
    with pytest.raises(Refused, match='mounts'):
        docker.verify('container-id', 'network-id', plan)


def test_existing_runtime_cli_prepares_own_principal(monkeypatch, capsys):
    from types import SimpleNamespace

    from poolhouse import runtime_cli
    from poolhouse.coordination import cli

    selected = []
    def connect(**kwargs):
        selected.append(kwargs['agent'])
        return SimpleNamespace(whoami=lambda: SimpleNamespace(name='codex-test'))
    monkeypatch.setattr(cli.session, 'connect', connect)
    assert runtime_cli.main(['coordination', 'prepare', '--agent', 'codex-test']) == 0
    evidence = json.loads(capsys.readouterr().out)
    assert evidence['authority'] == 'none' and evidence['control_plane_only'] is True
    assert 'org.poolhouse.coordination.owner=codex-test' in evidence['container']
    assert selected == ['codex-test']


def test_cleanup_removes_only_verified_exact_resources(monkeypatch, tmp_path, plan):
    docker = docker_with(monkeypatch, tmp_path, plan, configuration(plan))
    calls = []
    monkeypatch.setattr(docker, 'inspect', lambda kind, identifier, owner: {'Id': identifier})
    monkeypatch.setattr(docker, 'call', lambda argv, **kwargs: calls.append(argv))
    identifiers = {'container': 'container-id', 'network': 'network-id'}
    runtime.cleanup(docker, plan, identifiers)
    assert calls == [['stop', '--time', '10', 'container-id'],
                     ['rm', '--volumes', 'container-id'], ['network', 'rm', 'network-id']]
    assert identifiers == {}


@pytest.mark.parametrize("status", [401, 403, 500, None, "tls"])
def test_host_tls_checks_owned_ca_anonymous_auth_and_restores_pin(monkeypatch, tmp_path, status):
    import ssl
    from pathlib import Path

    from test_http_trust import _self_signed

    from poolhouse import http
    from poolhouse.coordination import verify

    certificate, _ = _self_signed(tmp_path)
    authority = Path(certificate).read_text()
    expected = "https://127.0.0.1:16443/api/v1/namespaces/poolhouse-coordination/configmaps/status"
    old = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setitem(http._PINNED, "127.0.0.1:16443", old)

    class OwnedDocker:
        def call(self, argv):
            assert argv == ["exec", "container-id", "/bin/cat", "/state/k3s/server/tls/server-ca.crt"]
            return subprocess.CompletedProcess(argv, 0, stdout=authority)

    def credential(_url):
        pytest.fail("the anonymous metadata probe looked up a server credential")
    monkeypatch.setattr(http.serverkeys, "for_url", credential)

    def request(url, *, context, timeout, headers):
        from poolhouse import httpguard
        assert url == expected and timeout == 5 and headers == {"Authorization": ""}
        assert http._PINNED["127.0.0.1:16443"] is old
        assert context is not old and context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        assert context.cert_store_stats()["x509"] == 1
        if status == "tls":
            raise httpguard.Refused("fixture invalid certificate")
        return httpguard.Fetched(url, status or 200, {}, b"{}")
    monkeypatch.setattr(verify.loopback, "fetch", request)
    if status in (401, 403):
        verify.host_tls(OwnedDocker(), "container-id", 16443)
    else:
        with pytest.raises(Refused):
            verify.host_tls(OwnedDocker(), "container-id", 16443)
    assert http._PINNED["127.0.0.1:16443"] is old


def test_docker_delivery_keeps_hostile_argv_literal_and_drops_ambient_credentials(monkeypatch, tmp_path):
    import os
    import sys

    program = tmp_path / 'fixture-docker'
    program.write_text(f'#!{sys.executable}\nimport json, os, sys\nprint(json.dumps({{"argv": sys.argv[1:], "env": dict(os.environ)}}))\n')
    program.chmod(0o700)
    monkeypatch.setattr('poolhouse.coordination.docker.shutil.which', lambda _: str(program))
    monkeypatch.setenv('FIXTURE_TOKEN', 'fixture-private-value')
    docker = Docker(tmp_path, 'unix:///own/docker.sock')
    hostile = '$(touch forbidden); --privileged'
    result = json.loads(docker.call(['inspect', hostile]).stdout)
    assert result['argv'] == ['inspect', hostile]
    assert 'FIXTURE_TOKEN' not in result['env']
    assert result['env']['HOME'] == str(tmp_path)
    assert result['env']['DOCKER_CONFIG'] == str(tmp_path)
    assert result['env']['DOCKER_HOST'] == 'unix:///own/docker.sock'
    assert result['env']['PATH'] == os.defpath
    program.write_text(f'#!{sys.executable}\nimport sys\nprint("fixture-private-value", file=sys.stderr)\nsys.exit(7)\n')
    with pytest.raises(Refused, match=r'^Docker inspect failed \(exit 7\)\.$'):
        docker.call(['inspect', hostile])
    program.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(10)\n')
    with pytest.raises(subprocess.TimeoutExpired):
        docker.call(['inspect', hostile], timeout=0.1)


@pytest.mark.parametrize('phase', ['startup', 'keepalive'])
def test_termination_cleans_owned_resources_and_restores_handler(monkeypatch, tmp_path, phase):
    import os
    import signal
    from types import SimpleNamespace

    calls = []
    releases = []
    actor = SimpleNamespace(name='codex-test')
    session = SimpleNamespace(whoami=lambda: actor, reserve=lambda _: None,
                              release=lambda kind, key: releases.append((kind, key)))
    class OwnedDocker:
        def __init__(self, *_args):
            self.env = {'HOME': str(tmp_path)}
        def image_ready(self):
            pass
        def call(self, argv, **_kwargs):
            calls.append(argv)
            value = 'b' * 64 if argv[0] == 'network' else 'a' * 64
            return subprocess.CompletedProcess(argv, 0, stdout=value)
        def verify(self, *_args):
            pass
        def inspect(self, *_args):
            pass
    def terminate(*_args, **_kwargs):
        os.kill(os.getpid(), signal.SIGTERM)
    monkeypatch.setattr(runtime, 'Docker', OwnedDocker)
    monkeypatch.setattr(runtime, 'network_plan', lambda _d, _n, plan: plan)
    monkeypatch.setattr(runtime.verify, 'readiness', terminate if phase == 'startup' else lambda *_: None)
    monkeypatch.setattr(runtime.verify, 'verify', lambda *_: {})
    monkeypatch.setattr(runtime.verify, 'host_tls', lambda *_: None)
    monkeypatch.setattr(runtime.relay, 'install', lambda *_: None)
    import contextlib
    @contextlib.contextmanager
    def forward(*_args):
        yield SimpleNamespace(check=lambda: None)
    monkeypatch.setattr(runtime.relay, 'forward', forward)
    monkeypatch.setattr(runtime.time, 'sleep', terminate)
    previous = signal.getsignal(signal.SIGTERM)
    with pytest.raises(KeyboardInterrupt):
        runtime.run(session, tmp_path / 'private-state', 'unix:///own/docker.sock', 16443, 650)
    assert signal.getsignal(signal.SIGTERM) == previous
    assert calls[-3:] == [['stop', '--time', '10', 'a' * 64],
                          ['rm', '--volumes', 'a' * 64], ['network', 'rm', 'b' * 64]]
    assert len(releases) == 3 and {kind for kind, _ in releases} == {'install', 'server', 'port'}


@pytest.fixture
def relay_fixture(tmp_path):
    import sys
    from types import SimpleNamespace

    from poolhouse.board.session import Session
    from poolhouse.coordination import relay

    class OwnSession(Session):
        def whoami(self):
            return SimpleNamespace(name='codex-test')
        def claims(self):
            return [SimpleNamespace(owner='codex-test', kind=kind, key=key, expires_in_s=30)
                    for kind, key in [('server', 'poolhouse-coordination-fixture'), ('port', '0')]]

    program = tmp_path / 'fixture-docker'
    program.write_text(f'#!{sys.executable}\nimport json,os,sys\nfrom pathlib import Path\nPath(os.environ["FIXTURE_LOG"]).write_text(json.dumps(sys.argv[1:]))\nwhile data:=os.read(0,65536): os.write(1,data)\n')
    program.chmod(0o700)
    log = tmp_path / 'argv.json'
    class OwnedDocker:
        def __init__(self):
            self.env = {'FIXTURE_LOG': str(log)}
            self.program = str(program)
            self.stat = '65533:65533:755:12:directory\n65533:65533:555:13:regular file\n'
            self.hash = relay.SHA256
            self.verifies = []
        def call(self, argv, **kwargs):
            assert kwargs['timeout'] == 2 and argv[:2] == ['exec', 'container-id']
            output = self.stat if '/bin/stat' in argv else f'{self.hash}  /relay/busybox\n'
            return subprocess.CompletedProcess(argv, 0, stdout=output)
        def inspect(self, kind, identifier, plan, **kwargs):
            assert (kind, identifier, plan.owner, kwargs['timeout']) == ('container', 'container-id', 'codex-test', 2)
            return {'HostConfig': {'NetworkMode': 'network-id'}}
        def verify(self, identifier, network, plan, **kwargs):
            assert (identifier, network, kwargs['timeout']) == ('container-id', 'network-id', 2)
            self.verifies.append(plan.owner)
    session = OwnSession(SimpleNamespace(state=tmp_path), 'fixture-board', 'fixture-token')
    return relay, OwnedDocker(), Plan('codex-test', 'fixture', 0), session, log


def test_relay_delivers_opaque_bytes_with_fixed_destination_and_cleans_up(relay_fixture):
    import socket

    relay, docker, plan, session, log = relay_fixture
    body = b'\x16\x03\x03\x00\x10fixture-opaque\xff\x00'
    with relay.forward(docker, 'container-id', plan, session) as worker:
        assert worker.session.client.timeout == 2 and not worker.session.client.autostart
        with socket.create_connection(worker.listener.getsockname(), timeout=2) as client:
            client.sendall(body)
            assert client.recv(1024) == body
        assert docker.verifies == ['codex-test']
    assert worker.active is None and worker.listener.fileno() == -1
    assert json.loads(log.read_text()) == ['exec', '-i', '--user', '65532:65532', 'container-id',
                                           '/relay/busybox', 'nc', '-w', '30', '127.0.0.1', '6443']


@pytest.mark.parametrize(('field', 'value'), [('stat', '65532:65532:777:13:regular file\n'),
    ('stat', '65533:65533:755:12:directory\n65533:65533:555:13:symbolic link\n'), ('hash', '0' * 64)])
def test_relay_rejects_replaced_or_writable_executable(relay_fixture, field, value):
    relay, docker, plan, session, _ = relay_fixture
    setattr(docker, field, value)
    with pytest.raises(Refused, match=r'custody|digest'), relay.forward(docker, 'container-id', plan, session):
        pytest.fail('unsafe executable was accepted')


def test_relay_refuses_lost_claim_before_starting_executor(relay_fixture, monkeypatch):
    import socket
    import time

    relay, docker, plan, session, log = relay_fixture
    monkeypatch.setattr(type(session), 'claims', lambda _: [])
    with pytest.raises(Refused, match='relay stopped'), relay.forward(docker, 'container-id', plan, session) as worker:
        with socket.create_connection(worker.listener.getsockname(), timeout=2) as client:
            assert client.recv(1) == b''
        deadline = time.monotonic() + 2
        while not worker.failed and time.monotonic() < deadline:
            time.sleep(0.01)
    assert not log.exists() and not docker.verifies


def test_relay_enforces_byte_limit_and_cancels_live_stream(relay_fixture, monkeypatch):
    import socket

    relay, docker, plan, session, _ = relay_fixture
    monkeypatch.setattr(relay, 'LIMIT', 32)
    with (relay.forward(docker, 'container-id', plan, session) as worker,
          socket.create_connection(worker.listener.getsockname(), timeout=2) as client):
        client.sendall(b'x' * 64)
        assert client.recv(1) == b''
    assert worker.active is None
    with relay.forward(docker, 'container-id', plan, session) as worker:
        client = socket.create_connection(worker.listener.getsockname(), timeout=2)
        client.sendall(b'active')
        assert client.recv(32) == b'active'
    client.close()
    assert worker.active is None


def test_relay_rejects_untrusted_binary_files(tmp_path):
    from poolhouse.coordination import relay

    body = tmp_path / 'binary'
    body.write_bytes(b'fixture executable from an untrusted source')
    with pytest.raises(Refused, match='digest'):
        relay.verified_binary(body)
    link = tmp_path / 'link'
    link.symlink_to(body)
    import errno
    with pytest.raises(OSError) as refused:
        relay.verified_binary(link)
    assert refused.value.errno == errno.ELOOP


@pytest.mark.parametrize('phase', ['create', 'start', 'copy', 'install', 'cleanup'])
def test_relay_dependency_cancellation_keeps_ambiguous_evidence_and_cleans_known_ids(monkeypatch, tmp_path, phase):
    import os
    import signal
    from types import SimpleNamespace

    from poolhouse.coordination import relay
    from poolhouse.coordination.creation import Unfinished

    calls = []
    releases = []
    root = tmp_path / 'private-config'
    root.mkdir()
    plan = Plan('codex-test', 'cancel-fixture', 16443)
    session = SimpleNamespace(reserve=lambda _: None,
                              release=lambda kind, key: releases.append((kind, key)))
    stages = {'create': 'create', 'start': 'start', 'cp': 'copy', 'exec': 'install', 'rm': 'cleanup'}
    class OwnedDocker:
        def image_ready(self, **_kwargs):
            pass
        def inspect(self, kind, identifier, owner):
            assert identifier in ('a' * 64, 'main-container') and owner.owner == plan.owner
            return {'Config': {'Image': relay.IMAGE, 'User': '65532:65532'}, 'Mounts': [],
                    'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True,
                                   'Privileged': False, 'CapAdd': None, 'CapDrop': ['ALL']}}
        def call(self, argv, **_kwargs):
            calls.append(argv)
            if stages.get(argv[0]) == phase:
                os.kill(os.getpid(), signal.SIGTERM)
            return subprocess.CompletedProcess(argv, 0, stdout='a' * 64)
    monkeypatch.setattr(relay, 'verified_binary', lambda _: b'fixture executable')
    expected = Unfinished if phase in ('create', 'cleanup') else KeyboardInterrupt
    with pytest.raises(expected), runtime.termination_cleanup():
        relay.install(OwnedDocker(), 'main-container', plan, root, session)
    records = [json.loads(line) for line in next(tmp_path.glob('creation-*.jsonl')).read_text().splitlines()]
    assert records[0]['phase'] == 'prepared' and records[1]['phase'] == 'creating'
    assert records[-1]['owner'] == 'codex-test'
    if phase == 'create':
        assert records[-1]['phase'] == 'cleanup_required'
        assert records[-1]['unconfirmed_kind'] == 'container' and not records[-1]['known_ids']
        assert not releases and not any(argv[0] == 'rm' for argv in calls)
    elif phase == 'cleanup':
        assert records[-1]['known_ids'] == {'container': 'a' * 64} and not releases
    else:
        assert calls[-1] == ['rm', '--volumes', 'a' * 64]
        assert records[-1]['phase'] == 'cleaned' and releases


def test_creation_timeout_retains_durable_pending_record_and_no_fabricated_id(tmp_path):
    from types import SimpleNamespace

    from poolhouse.coordination.creation import Creation

    def timeout(argv):
        raise subprocess.TimeoutExpired(argv, 1)
    receipt = Creation(tmp_path, Plan('codex-test', 'timeout-fixture', 16443))
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            receipt.create(SimpleNamespace(call=timeout), 'container', ['create', 'fixture'])
        assert receipt.pending == 'container' and receipt.identifiers == {}
    finally:
        receipt.close()
    records = [json.loads(line) for line in receipt.path.read_text().splitlines()]
    assert records[-1]['phase'] == 'cleanup_required' and records[-1]['unconfirmed_kind'] == 'container'
    assert receipt.path.stat().st_mode & 0o777 == 0o600


def test_relay_unexpected_thread_failure_is_observable_and_fails_closed(relay_fixture, monkeypatch):
    import socket
    import time

    relay, docker, plan, session, _ = relay_fixture
    def malformed():
        raise ValueError('fixture malformed custody')
    failures = []
    monkeypatch.setattr('threading.excepthook', lambda args: failures.append(args.exc_value))
    with pytest.raises(Refused, match='relay stopped'), relay.forward(docker, 'container-id', plan, session) as worker:
        monkeypatch.setattr(worker, 'authorized', malformed)
        with socket.create_connection(worker.listener.getsockname(), timeout=2) as client:
            assert client.recv(1) == b''
        deadline = time.monotonic() + 2
        while not worker.failed and time.monotonic() < deadline:
            time.sleep(0.01)
    assert len(failures) == 1 and isinstance(failures[0], ValueError)


def test_relay_vanished_child_still_reaps_and_closes_streams(monkeypatch):
    import io
    from types import SimpleNamespace

    from poolhouse.coordination import relay

    calls = []
    process = SimpleNamespace(poll=lambda: None, wait=lambda **kwargs: calls.append(kwargs),
                              stdin=io.BytesIO(), stdout=io.BytesIO())
    def vanished(*_args, **_kwargs):
        raise ProcessLookupError
    monkeypatch.setattr(relay.platform, 'terminate_process_group', vanished)
    relay.reap(process)
    assert calls == [{'timeout': 1}]
    assert process.stdin.closed and process.stdout.closed


def test_relay_incomplete_reaping_retains_container_cleanup_boundary(relay_fixture, monkeypatch):
    import socket

    relay, docker, plan, session, _ = relay_fixture
    from poolhouse.coordination.creation import Unfinished

    with pytest.raises(Unfinished), relay.forward(docker, 'container-id', plan, session) as worker:
        with socket.create_connection(worker.listener.getsockname(), timeout=2) as client:
            client.sendall(b'fixture')
            assert client.recv(32) == b'fixture'
        import time
        deadline = time.monotonic() + 2
        while worker.active is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.active is None
        # All actual fixture children are reaped; emulate the unresolved custody signal.
        monkeypatch.setattr(worker, 'reaped', False)


def test_relay_foreign_owned_binary_is_refused_before_read(monkeypatch, tmp_path):
    import os
    import stat
    from types import SimpleNamespace

    from poolhouse.coordination import relay

    path = tmp_path / 'fixture-binary'
    path.write_bytes(b'fixture')
    with monkeypatch.context() as scoped:
        scoped.setattr(relay.os, 'fstat', lambda _: SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600, st_size=7, st_uid=os.getuid() + 1))
        scoped.setattr(relay.os, 'read', lambda *_: pytest.fail('foreign bytes were read'))
        with pytest.raises(Refused, match='regular file'):
            relay.verified_binary(path)


def test_creation_initial_evidence_failure_closes_its_descriptor(monkeypatch, tmp_path):
    import os

    from poolhouse.coordination.creation import Creation

    closed = []
    original = os.close
    def close(descriptor):
        closed.append(descriptor)
        original(descriptor)
    def unavailable(_self, _phase):
        raise OSError('fixture storage unavailable')
    with monkeypatch.context() as scoped:
        scoped.setattr(Creation, 'record', unavailable)
        scoped.setattr(os, 'close', close)
        with pytest.raises(OSError, match='storage unavailable'):
            Creation(tmp_path, Plan('codex-test', 'storage-fixture', 16443))
    assert len(closed) == 1


def test_relay_connection_deadline_closes_without_waiting_for_client_data(relay_fixture, monkeypatch):
    import socket
    from types import SimpleNamespace

    relay, docker, plan, session, _ = relay_fixture
    clock = iter((0, 31))
    monkeypatch.setattr(relay, 'time', SimpleNamespace(monotonic=lambda: next(clock, 31)))
    with (relay.forward(docker, 'container-id', plan, session) as worker,
          socket.create_connection(worker.listener.getsockname(), timeout=2) as client):
        assert client.recv(1) == b''
    assert worker.reaped


def test_relay_reset_client_does_not_stop_the_next_connection(relay_fixture):
    import socket
    import struct

    relay, docker, plan, session, _ = relay_fixture
    with relay.forward(docker, 'container-id', plan, session) as worker:
        client = socket.create_connection(worker.listener.getsockname(), timeout=2)
        client.sendall(b'fixture reset' * 1024)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
        client.close()
        with socket.create_connection(worker.listener.getsockname(), timeout=2) as following:
            following.sendall(b'next connection')
            assert following.recv(64) == b'next connection'
        assert not worker.failed


def test_relay_broken_pipe_still_reaps_before_the_next_connection(relay_fixture, monkeypatch):
    import socket

    relay, docker, plan, session, _ = relay_fixture
    original = relay.pump
    calls = []
    def broken_once(client, process, stop):
        calls.append(process)
        if len(calls) == 1:
            raise BrokenPipeError('fixture closed stream')
        assert calls[0].poll() is not None
        return original(client, process, stop)
    monkeypatch.setattr(relay, 'pump', broken_once)
    with relay.forward(docker, 'container-id', plan, session) as worker:
        with socket.create_connection(worker.listener.getsockname(), timeout=2) as client:
            assert client.recv(1) == b''
        with socket.create_connection(worker.listener.getsockname(), timeout=2) as following:
            following.sendall(b'after broken pipe')
            assert following.recv(64) == b'after broken pipe'
        assert not worker.failed and len(calls) == 2


@pytest.mark.parametrize('permitted', [None, 'exec', 'token'])
def test_verifier_queries_exact_execution_and_token_subresources(monkeypatch, permitted):
    from poolhouse.coordination import verify

    observed = []

    def kubectl(_docker, _identifier, args, **kwargs):
        if args[:2] == ['auth', 'can-i']:
            observed.append(args)
            allowed = permitted is not None and f'--subresource={permitted}' in args
            return subprocess.CompletedProcess(args, 0 if allowed else 1, 'yes' if allowed else 'no', '')
        if kwargs.get('identity') in ('invalid', 'wrong-audience'):
            return subprocess.CompletedProcess(args, 1, '', 'Unauthorized')
        if args[0].startswith('--tls-server-name'):
            return subprocess.CompletedProcess(args, 1, '', 'certificate')
        value = {'data': {'authority': 'none'}}
        if args[:2] == ['get', 'lease']:
            value = {'spec': {'holderIdentity': 'verification-only'}}
        if args[:2] == ['get', 'nodes']:
            value = {'items': []}
        return subprocess.CompletedProcess(args, 0, json.dumps(value), '')

    monkeypatch.setattr(verify, 'kubectl', kubectl)
    monkeypatch.setattr(verify, 'token_claims', lambda *_: {'aud': ['poolhouse-local'], 'iat': 0, 'exp': 600})
    class Bootstrap:
        def call(self, *_args, **_kwargs):
            return None

    if permitted:
        with pytest.raises(Refused, match='unexpected operation'):
            verify.verify(Bootstrap(), 'owned')
    else:
        assert verify.verify(Bootstrap(), 'owned')['restricted_service_account']
        assert ['auth', 'can-i', 'create', 'pods', '--subresource=exec'] in observed
        assert ['auth', 'can-i', 'create', 'serviceaccounts', '--subresource=token'] in observed
        assert ['auth', 'can-i', 'update', 'configmaps/other'] in observed

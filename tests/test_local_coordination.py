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
                           'PortBindings': {'6443/tcp': [{'HostIp': '127.0.0.1',
                                                       'HostPort': str(plan.port)}]}}}


def docker_with(monkeypatch, tmp_path, plan, config):
    monkeypatch.setattr('poolhouse.coordination.docker.shutil.which', lambda _: '/bin/docker')
    docker = Docker(tmp_path, 'unix:///own/docker.sock')
    network = {'Id': 'network-id', 'Labels': plan.labels, 'Internal': True}
    monkeypatch.setattr(docker, 'call', lambda argv: subprocess.CompletedProcess(
        argv, 0, stdout=json.dumps([network if argv[0] == 'network' else config])))
    return docker


def test_plan_has_no_execution_or_host_mounts(plan):
    argv = plan.container('network-id')
    assert '--disable-agent' in argv
    assert '--privileged' not in argv and '--volume' not in argv and '--mount' not in argv
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


@pytest.mark.parametrize("status", [401, 403, 500, None])
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

    def request(url, *, options):
        assert url == expected and options.timeout == 5 and options.token == ""
        assert dict(options.headers) == {"Authorization": ""}
        assert options.guard(url) == expected
        with pytest.raises(Refused):
            options.guard(url + "/other")
        context = http._PINNED["127.0.0.1:16443"]
        assert context is not old and context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        assert context.cert_store_stats()["x509"] == 1
        wire = http.build_request(url, headers=dict(options.headers))
        assert wire.get_header("Authorization") == ""
        if status is None:
            return {}
        raise http.ServerError("fixture refusal", status=status)
    monkeypatch.setattr(http, "request_json", request)
    if status in (401, 403):
        verify.host_tls(OwnedDocker(), "container-id", 16443)
    else:
        with pytest.raises(Refused):
            verify.host_tls(OwnedDocker(), "container-id", 16443)
    assert http._PINNED["127.0.0.1:16443"] is old

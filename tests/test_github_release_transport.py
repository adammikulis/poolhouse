"""Fixed publication origins and private Git credential transport."""

import base64
import io
import subprocess

import pytest

from poolhouse import httpguard
from poolhouse.http import Server
from poolhouse.net import github_release as net
from poolhouse.workspace import github_release as holder


@pytest.mark.parametrize('path', [
    'https://host.invalid/repos/demo/project/releases', '//host.invalid/a',
    '/repos/demo/project/../../secrets', '/repos/demo/project/actions/workflows',
    '/repos/demo/project/releases?token=secret', '/repos/demo/project/releases\r\nHost: hostile',
])
def test_invalid_api_paths_never_connect(path, monkeypatch):
    monkeypatch.setattr(httpguard, 'fetch', lambda *a, **k: pytest.fail('unexpected connection'))
    with pytest.raises(net.Failed):
        net.request('POST', path, bearer='fixture-secret')


def test_redirect_is_returned_without_following_and_refused(monkeypatch):
    calls = []

    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        assert kwargs['limits'].follow_redirects is False
        with pytest.raises(net.Failed):
            kwargs['limits'].vet('https://host.invalid/escape')
        return httpguard.Fetched(url, 302, {'location': 'https://host.invalid/escape'}, b'', ())

    monkeypatch.setattr(httpguard, 'fetch', fetch)
    with pytest.raises(net.Failed, match='302'):
        net.request('GET', '/repos/demo/project/releases', bearer='fixture-secret')
    assert len(calls) == 1
    assert calls[0][0] == 'https://api.github.com/repos/demo/project/releases'


def test_fixed_upload_origin_and_pinned_body(monkeypatch):
    calls = []

    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        return httpguard.Fetched(url, 201, {}, b'{"id":7}', ())

    monkeypatch.setattr(httpguard, 'fetch', fetch)
    assert net.upload('demo/project', 7, ('artifact.whl', b'wheel'), 'fixture-secret') == {'id': 7}
    url, options = calls[0]
    assert url == 'https://uploads.github.com/repos/demo/project/releases/7/assets?name=artifact.whl'
    assert options['data'] == b'wheel'
    assert options['headers']['Authorization'] == 'Bearer fixture-secret'
    assert options['limits'].context is None
    assert options['limits'].follow_redirects is False


def test_child_credentials_are_absent_from_arguments_parent_and_errors(tmp_path, monkeypatch, capsys):
    original = holder.environment(tmp_path)
    captured = []

    def start(argv, **kwargs):
        captured.append((argv, kwargs))
        return Process(1)

    monkeypatch.setattr(net.os, 'waitid', lambda *a: None)
    monkeypatch.setattr(net.platform, 'start_process', start)
    monkeypatch.setattr(net, '_exchange', lambda process: process.returncode)
    monkeypatch.setattr(net.platform, 'terminate_process_group', lambda *a, **k: None)
    plan = net.GitPush('demo/project', '0.3dev', 'a' * 40, 'fixture-secret')
    assert 'fixture-secret' not in repr(plan)
    with pytest.raises(net.Failed, match='development push failed') as error:
        net.push(plan, tmp_path, original)
    assert 'fixture-secret' not in str(error.value)
    argv, kwargs = captured[0]
    assert argv[-1] == 'a' * 40 + ':refs/heads/0.3dev'
    assert '--force' not in argv and '--tags' not in argv
    assert 'fixture-secret' not in repr(argv)
    assert 'GIT_CONFIG_COUNT' not in original
    config = {kwargs['env'][f'GIT_CONFIG_KEY_{i}']: kwargs['env'][f'GIT_CONFIG_VALUE_{i}']
              for i in range(int(kwargs['env']['GIT_CONFIG_COUNT']))}
    secret = base64.b64encode(b'x-access-token:fixture-secret').decode()
    assert config['http.https://github.com/.extraHeader'] == f'Authorization: Basic {secret}'
    assert config['credential.helper'] == ''
    assert config['http.followRedirects'] == 'false'
    assert config['http.proxy'] == ''
    assert not list((tmp_path / 'no-transport-hooks').iterdir())
    assert 'fixture-secret' not in repr(list(tmp_path.rglob('*')))
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('repo,branch', [('demo/project', 'main'), ('demo/project', 'master'),
                                        ('demo/project', 'x:refs/tags/unsafe'), ('https://host.invalid/a', '0.3dev')])
def test_push_rejects_alternate_targets_before_spawn(tmp_path, monkeypatch, repo, branch):
    monkeypatch.setattr(net.platform, 'start_process', lambda *a, **k: pytest.fail('unexpected spawn'))
    with pytest.raises(net.Failed):
        net.push(net.GitPush(repo, branch, 'a' * 40, 'fixture-secret'), tmp_path, {})


def test_environment_discards_ambient_credential_and_execution_settings(tmp_path, monkeypatch):
    for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'GIT_TRACE', 'GIT_CURL_VERBOSE', 'GIT_CONFIG_COUNT',
                 'GIT_CONFIG_VALUE_0', 'GIT_ASKPASS', 'HTTPS_PROXY', 'GIT_SSH_COMMAND', 'GIT_DIR'):
        monkeypatch.setenv(name, 'hostile-ambient')
    env = holder.environment(tmp_path)
    assert 'hostile-ambient' not in repr(env)
    assert env['GIT_CONFIG_GLOBAL'] == '/dev/null'
    assert env['GIT_CONFIG_NOSYSTEM'] == '1'
    assert env['GIT_ALLOW_PROTOCOL'] == 'https'
    assert env['GIT_TERMINAL_PROMPT'] == '0'


def test_network_errors_never_echo_bearer(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('invalid header Bearer fixture-secret')

    monkeypatch.setattr(httpguard, 'fetch', fail)
    with pytest.raises(net.Failed, match='transport failed') as error:
        net.request('GET', '/repos/demo/project/releases', bearer='fixture-secret')
    assert 'fixture-secret' not in str(error.value)


class Process:
    def __init__(self, code=0):
        self.returncode = code
        self.pid = 12345
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()
        self.waits = []

    def communicate(self, timeout):
        assert timeout == 180
        return b"", b""

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.waits.append(timeout)
        self.returncode = 0
        return 0


@pytest.mark.parametrize('failure', [subprocess.TimeoutExpired('git', 180), KeyboardInterrupt()])
def test_timeout_and_cancel_reap_exact_child_and_close_pipes(tmp_path, monkeypatch, failure):
    process = Process(None)
    monkeypatch.setattr(net.os, 'waitid', lambda *a: None)
    calls = []

    def communicate(timeout):
        raise failure

    monkeypatch.setattr(net, '_exchange', lambda child: communicate(180))
    monkeypatch.setattr(net.platform, 'start_process', lambda *a, **k: process)
    monkeypatch.setattr(net.platform, 'terminate_process_group', lambda child, **k: calls.append(child))
    with pytest.raises((net.Failed, KeyboardInterrupt)):
        net.push(net.GitPush('demo/project', '0.3dev', 'a' * 40, 'fixture-secret'), tmp_path,
                 holder.environment(tmp_path))
    assert calls == [process, process] and process.waits == [5]
    assert process.stdout.closed and process.stderr.closed


def test_vanished_child_group_still_waits_and_closes(monkeypatch):
    process = Process(None)
    monkeypatch.setattr(net.os, 'waitid', lambda *a: None)

    def vanished(child, **kwargs):
        assert child is process
        raise ProcessLookupError

    monkeypatch.setattr(net.platform, 'terminate_process_group', vanished)
    net._reap(process)
    assert process.waits == [5]
    assert process.stdout.closed and process.stderr.closed


def test_start_failure_has_constant_error(tmp_path, monkeypatch):
    def fail(*a, **k):
        raise OSError('fixture-secret')

    monkeypatch.setattr(net.platform, 'start_process', fail)
    with pytest.raises(net.Failed, match='transport failed') as error:
        net.push(net.GitPush('demo/project', '0.3dev', 'a' * 40, 'fixture-secret'), tmp_path, {})
    assert 'fixture-secret' not in str(error.value)


def test_failed_reap_is_explicit_and_closes_pipes(tmp_path, monkeypatch):
    process = Process(None)
    monkeypatch.setattr(net.os, 'waitid', lambda *a: None)
    calls = []

    def wait(timeout):
        raise subprocess.TimeoutExpired('git', timeout)

    process.wait = wait
    monkeypatch.setattr(net, '_exchange', lambda child: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(net.platform, 'start_process', lambda *a, **k: process)
    monkeypatch.setattr(net.platform, 'terminate_process_group', lambda child, **k: calls.append((child, k)))
    with pytest.raises(KeyboardInterrupt) as error:
        net.push(net.GitPush('demo/project', '0.3dev', 'a' * 40, 'fixture-secret'), tmp_path, {})
    assert calls == [(process, {}), (process, {'force': True})]
    assert 'the owned Git transport cleanup is unconfirmed' in error.value.__notes__
    assert process.stdout.closed and process.stderr.closed


def _git_fixture(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    clean = holder.environment(source)

    def git(*args, cwd=source):
        return subprocess.run(['git', *args], cwd=cwd, env=clean, capture_output=True, check=True).stdout.strip()

    git('init', '--quiet', '--initial-branch=0.3dev')
    (source / 'value.txt').write_text('fixed artifact\n')
    git('add', 'value.txt')
    git('-c', 'user.name=Codex', '-c', 'user.email=codex@agents.invalid', 'commit', '-qm', 'chore: fixture')
    commit = git('rev-parse', 'HEAD').decode()
    remote_root = tmp_path / 'remote'
    remote_root.mkdir()
    git('init', '--quiet', '--bare', str(remote_root / 'repository.git'))
    git('--git-dir=' + str(remote_root / 'repository.git'), 'config', 'http.receivepack', 'true')
    return source, clean, commit, remote_root, git


def test_actual_git_smart_https_uses_child_token_and_verified_ca(tmp_path, monkeypatch):
    import http.server
    import ssl
    import threading
    import urllib.parse

    from tests.test_http_trust import _self_signed

    source, clean, commit, remote_root, git = _git_fixture(tmp_path)
    expected = 'Basic ' + base64.b64encode(b'x-access-token:fixture-secret').decode()
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self):
            address = urllib.parse.urlsplit(self.path)
            seen.append(self.headers.get('Authorization'))
            if self.headers.get('Authorization') != expected or not address.path.startswith('/repository.git/'):
                self.send_error(403)
                return
            size = int(self.headers.get('Content-Length', '0'))
            assert 0 <= size <= 1 << 20
            env = holder.environment(remote_root)
            env.update(GIT_PROJECT_ROOT=str(remote_root), GIT_HTTP_EXPORT_ALL='1',
                       PATH_INFO=address.path, QUERY_STRING=address.query, REQUEST_METHOD=self.command,
                       CONTENT_TYPE=self.headers.get('Content-Type', ''), CONTENT_LENGTH=str(size),
                       REMOTE_USER='fixture')
            response = subprocess.run(['git', 'http-backend'], env=env, input=self.rfile.read(size),
                                      capture_output=True, check=True).stdout
            headers, body = response.split(b'\r\n\r\n', 1)
            self.send_response(200)
            for line in headers.decode().splitlines():
                name, value = line.split(':', 1)
                if name.lower() != 'status':
                    self.send_header(name, value.strip())
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = answer
        do_POST = answer

    cert, key = _self_signed(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server = Server(('127.0.0.1', 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    original = net.platform.start_process

    def fixed_fixture_transport(argv, **kwargs):
        assert argv[-2] == 'https://github.com/demo/project.git'
        assert argv[-1] == commit + ':refs/heads/0.3dev'
        env = dict(kwargs['env'])
        count = int(env['GIT_CONFIG_COUNT'])
        for index in range(count):
            if env[f'GIT_CONFIG_KEY_{index}'] == 'http.https://github.com/.extraHeader':
                env[f'GIT_CONFIG_KEY_{index}'] = f'http.https://127.0.0.1:{server.server_port}/.extraHeader'
        env['GIT_CONFIG_KEY_' + str(count)] = 'http.sslCAInfo'
        env['GIT_CONFIG_VALUE_' + str(count)] = cert
        env['GIT_CONFIG_COUNT'] = str(count + 1)
        kwargs['env'] = env
        address = f'https://127.0.0.1:{server.server_port}/repository.git'
        return original([*argv[:-2], address, argv[-1]], **kwargs)

    monkeypatch.setattr(net.platform, 'start_process', fixed_fixture_transport)
    try:
        net.push(net.GitPush('demo/project', '0.3dev', commit, 'fixture-secret'), source, clean)
        assert seen and all(value == expected for value in seen)
        actual = git('--git-dir=' + str(remote_root / 'repository.git'), 'rev-parse', 'refs/heads/0.3dev')
        assert actual.decode() == commit
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    assert not thread.is_alive()


def test_exited_leader_retains_group_custody_until_lingering_helper_is_killed(tmp_path, monkeypatch):
    import os
    import sys

    import psutil

    record = tmp_path / 'helper.pid'
    program = ('import subprocess,sys; from pathlib import Path; '
               'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
               'Path(sys.argv[1]).write_text(str(child.pid))')
    process = net.platform.start_process([sys.executable, '-c', program, str(record)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    original = net.platform.terminate_process_group
    signals = []

    def terminate(child, **kwargs):
        assert child is process
        ended = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        assert ended is not None and ended.si_status == 0
        signals.append(kwargs)
        return original(child, **kwargs)

    monkeypatch.setattr(net.platform, 'terminate_process_group', terminate)
    cleaned = False
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            net._exchange(process, timeout=0.5)
        helper = psutil.Process(int(record.read_text()))
        assert helper.is_running()
        net._reap(process)
        cleaned = True
        assert process.returncode == 0
        assert signals == [{}, {'force': True}]
        assert not helper.is_running() or helper.status() == psutil.STATUS_ZOMBIE
        with pytest.raises(ChildProcessError):
            os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    finally:
        if not cleaned:
            net._reap(process)


def test_output_cap_terminates_owned_child_without_reaping_early():
    import sys

    process = net.platform.start_process([sys.executable, '-c', 'import os,time; os.write(1,b"x"*(2<<20)); time.sleep(60)'],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with pytest.raises(net.Failed, match='output exceeded'):
            net._exchange(process, timeout=5)
    finally:
        net._reap(process)
    assert process.returncode is not None

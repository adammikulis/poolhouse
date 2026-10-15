"""Fixed publication origins and private Git credential transport."""

import base64
import io
import subprocess

import pytest

from poolhouse import httpguard
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

    monkeypatch.setattr(net.platform, 'start_process', start)
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
    calls = []

    def communicate(timeout):
        raise failure

    process.communicate = communicate
    monkeypatch.setattr(net.platform, 'start_process', lambda *a, **k: process)
    monkeypatch.setattr(net.platform, 'terminate_process_group', lambda child, **k: calls.append(child))
    with pytest.raises((net.Failed, KeyboardInterrupt)):
        net.push(net.GitPush('demo/project', '0.3dev', 'a' * 40, 'fixture-secret'), tmp_path,
                 holder.environment(tmp_path))
    assert calls == [process] and process.waits == [5]
    assert process.stdout.closed and process.stderr.closed


def test_vanished_child_group_still_waits_and_closes(monkeypatch):
    process = Process(None)

    def vanished(child):
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
    calls = []

    def wait(timeout):
        raise subprocess.TimeoutExpired('git', timeout)

    process.wait = wait
    process.communicate = lambda timeout: (_ for _ in ()).throw(KeyboardInterrupt())
    monkeypatch.setattr(net.platform, 'start_process', lambda *a, **k: process)
    monkeypatch.setattr(net.platform, 'terminate_process_group', lambda child, **k: calls.append((child, k)))
    with pytest.raises(net.Failed, match='cleanup is unconfirmed'):
        net.push(net.GitPush('demo/project', '0.3dev', 'a' * 40, 'fixture-secret'), tmp_path, {})
    assert calls == [(process, {}), (process, {'force': True})]
    assert process.stdout.closed and process.stderr.closed

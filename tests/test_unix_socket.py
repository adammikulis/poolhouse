"""Kernel Unix endpoints bind exact live processes, never siblings or inherited claims."""
import os
import socket
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import psutil
import pytest

from poolhouse import unix_socket


@pytest.fixture
def tmp_path():
    # Darwin pathname sockets cannot use pytest's long per-test directory names.
    with tempfile.TemporaryDirectory(prefix='unix-endpoint-', dir='/tmp') as directory:
        yield Path(directory)


@contextmanager
def connected(tmp_path):
    path = str(tmp_path / 'endpoint.sock')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(path)
        listener.listen(4)
        listener.settimeout(5)
        script = 'import socket,sys; s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);sys.stdin.buffer.read(1)'
        child = subprocess.Popen([sys.executable, '-c', script, path], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            channel, _ = listener.accept()
            with channel:
                yield channel, child, listener
        finally:
            if child.poll() is None:
                child.communicate(b'x', timeout=5)
            else:
                child.communicate(timeout=5)


@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='Kernel endpoint PID requires Unix')
def test_actual_kernel_endpoint_and_generation_reject_sibling_and_reused_pid(tmp_path):
    with connected(tmp_path) as (channel, child, _listener):
        expected = {'pid': child.pid, 'started': psutil.Process(child.pid).create_time()}
        assert unix_socket.identity(channel) == expected
        assert unix_socket.matches(channel, expected) is True
        assert unix_socket.matches(channel, {**expected, 'pid': os.getpid()}) is False
        assert unix_socket.matches(channel, {**expected, 'started': expected['started'] + 1}) is False
        sibling = subprocess.Popen([sys.executable, '-c', 'import sys;sys.stdin.buffer.read(1)'], stdin=subprocess.PIPE)
        try:
            assert unix_socket.matches(channel, {'pid': sibling.pid, 'started': psutil.Process(sibling.pid).create_time()}) is False
        finally:
            sibling.communicate(b'x', timeout=5)
        child.communicate(b'x', timeout=5)
        assert unix_socket.matches(channel, expected) is False


@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='Kernel endpoint PID requires Unix')
def test_client_verifies_actual_server_before_sending_payload(tmp_path):
    path = str(tmp_path / 'server.sock')
    script = ('import socket,sys; s=socket.socket(socket.AF_UNIX);s.bind(sys.argv[1]);s.listen();'
              'print("ready",flush=True);c,_=s.accept();sys.stdin.buffer.read(1)')
    server = subprocess.Popen([sys.executable, '-c', script, path], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert server.stdout.readline() == b'ready\n'
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(path)
            assert unix_socket.identity(client) == {'pid': server.pid, 'started': psutil.Process(server.pid).create_time()}
            assert not unix_socket.matches(client, {'pid': os.getpid(), 'started': psutil.Process().create_time()})
    finally:
        server.communicate(b'x', timeout=5)


@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='Kernel endpoint PID requires Unix')
def test_fork_uses_actual_kernel_identity_without_ancestry_fallback(tmp_path):
    path = str(tmp_path / 'fork.sock')
    script = ('import os,socket,sys; s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);p=os.fork();'
              '\nif p==0: s.sendall(str(os.getpid()).encode()+b"\\n");sys.stdin.buffer.read(1);os._exit(0)'
              '\nelse: os.waitpid(p,0)')
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(path)
        listener.listen()
        listener.settimeout(5)
        parent = subprocess.Popen([sys.executable, '-c', script, path], stdin=subprocess.PIPE)
        try:
            channel, _ = listener.accept()
            with channel:
                channel.settimeout(5)
                fork_pid = int(channel.recv(128))
                assert fork_pid != parent.pid
                # Darwin reports the fork writer; Linux pins SO_PEERCRED at connect.
                # Neither behavior is an application-level proof of descriptor custody.
                expected_pid = fork_pid if sys.platform == 'darwin' else parent.pid
                assert unix_socket.identity(channel)['pid'] == expected_pid
                other_pid = parent.pid if expected_pid == fork_pid else fork_pid
                assert not unix_socket.matches(channel, {'pid': other_pid, 'started': psutil.Process(other_pid).create_time()})
        finally:
            parent.communicate(b'x', timeout=5)


@pytest.mark.parametrize('kind', ['socketpair', 'inet', 'datagram', 'unconnected'])
@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='Kernel endpoint PID requires Unix')
def test_non_connected_pathname_streams_deny(kind):
    if kind == 'socketpair':
        one, two = socket.socketpair()
        with one, two, pytest.raises(unix_socket.IdentityUnavailable):
            unix_socket.identity(one)
        return
    with socket.socket(socket.AF_INET if kind == 'inet' else socket.AF_UNIX,
                       socket.SOCK_DGRAM if kind == 'datagram' else socket.SOCK_STREAM) as channel:
        assert unix_socket.matches(channel, {'pid': os.getpid(), 'started': psutil.Process().create_time()}) is False


@pytest.mark.parametrize('expected', [{}, {'pid': True, 'started': 1}, {'pid': 1, 'started': float('nan')},
                                      {'pid': 1, 'started': 1, 'extra': 2}, {'pid': -1, 'started': 1}])
@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='Kernel endpoint PID requires Unix')
def test_invalid_pins_fail_closed(expected):
    assert unix_socket.matches(None, expected) is False


@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='Kernel endpoint PID requires Unix')
def test_credentials_and_process_generation_rechecked(tmp_path, monkeypatch):
    with connected(tmp_path) as (channel, _child, _listener):
        original = unix_socket._credentials(channel)
        answers = iter([original, (original[0] + 1, original[1])])
        monkeypatch.setattr(unix_socket, '_credentials', lambda _channel: next(answers))
        with pytest.raises(unix_socket.IdentityUnavailable):
            unix_socket.identity(channel)


@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='Kernel endpoint PID requires Unix')
def test_process_replacement_between_checks_denies(tmp_path, monkeypatch):
    with connected(tmp_path) as (channel, _child, _listener):
        answers = iter([1.0, 2.0])
        monkeypatch.setattr(unix_socket, '_started', lambda _pid: next(answers))
        with pytest.raises(unix_socket.IdentityUnavailable):
            unix_socket.identity(channel)


@pytest.mark.parametrize('credentials', [(0, os.geteuid()), (1, os.geteuid() + 1)])
@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='Kernel endpoint PID requires Unix')
def test_foreign_or_unknown_endpoint_denies(tmp_path, monkeypatch, credentials):
    with connected(tmp_path) as (channel, _child, _listener):
        monkeypatch.setattr(unix_socket, '_credentials', lambda _channel: credentials)
        with pytest.raises(unix_socket.IdentityUnavailable):
            unix_socket.identity(channel)


@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='Kernel endpoint PID requires Unix')
def test_unsupported_platform_denies(tmp_path, monkeypatch):
    with connected(tmp_path) as (channel, _child, _listener):
        monkeypatch.setattr(unix_socket.sys, 'platform', 'unsupported')
        with pytest.raises(unix_socket.IdentityUnavailable):
            unix_socket.identity(channel)


@pytest.mark.parametrize('platform', ['darwin', 'linux'])
@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='Kernel endpoint PID requires Unix')
def test_truncated_kernel_credential_rejected(monkeypatch, platform):
    class Truncated:
        def getsockopt(self, *_args):
            return b'\0'
    monkeypatch.setattr(unix_socket.sys, 'platform', platform)
    if platform == 'linux':
        monkeypatch.setattr(socket, 'SO_PEERCRED', 17, raising=False)
    with pytest.raises(unix_socket.IdentityUnavailable):
        unix_socket._credentials(Truncated())

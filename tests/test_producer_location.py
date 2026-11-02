"""Private locator publication survives crashes without adopting old authority."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from poolhouse.fleet import producer_location as location


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(prefix='loc-', dir='/tmp') as name:
        path = Path(name).resolve()
        path.chmod(0o700)
        secret = path / 'app-identity'
        secret.write_text('ab' * 32)
        secret.chmod(0o600)
        yield path


@pytest.fixture
def endpoint(root):
    with socket.socket(socket.AF_UNIX) as channel:
        path = root / 'bootstrap.sock'
        channel.bind(str(path))
        path.chmod(0o600)
        yield path


def test_publish_idempotent_and_close(root, endpoint):
    secret = (root / 'app-identity').read_bytes()
    owner = location.Location(root)
    try:
        result = owner.publish(endpoint)
        assert owner.publish(endpoint) == result
        assert json.loads(result.read_bytes()) == {'version': 1, 'bootstrap_path': str(endpoint)}
        assert result.stat().st_mode & 0o777 == 0o600
        assert result.stat().st_nlink == 1
        assert owner.check() is True
        with pytest.raises(PermissionError):
            location.Location(root)
    finally:
        owner.close()
        owner.close()
    assert not result.exists()
    assert (root / 'app-identity').read_bytes() == secret


def test_tightens_owned_directory_without_creating_secret(root):
    root.chmod(0o755)
    owner = location.Location(root)
    owner.close()
    assert root.stat().st_mode & 0o777 == 0o700
    (root / 'app-identity').unlink()
    with pytest.raises(OSError):
        location.Location(root)
    assert not (root / 'app-identity').exists()


def test_real_account_gate_before_root_io(root, monkeypatch):
    monkeypatch.setattr(location.home, 'account_roots', lambda: (root, root / 'cache'))
    calls = []
    def denied(_what):
        calls.append('gate')
        raise PermissionError('not installed')
    monkeypatch.setattr(location.real_machine, 'require', denied)
    monkeypatch.setattr(location, 'directory', lambda *_: pytest.fail('root I/O before gate'))
    with pytest.raises(PermissionError):
        location.Location(root / 'traind')
    assert calls == ['gate']


@pytest.mark.parametrize('kind', ['symlink', 'directory', 'hardlink', 'public'])
def test_unsafe_locator_preserved(root, kind):
    target = root / location.LOCATOR
    if kind == 'symlink':
        target.symlink_to('app-identity')
    elif kind == 'directory':
        target.mkdir()
    else:
        target.write_text('inert')
        target.chmod(0o644 if kind == 'public' else 0o600)
        if kind == 'hardlink':
            os.link(target, root / 'second')
            os.link(target, root / 'third')
    with pytest.raises(PermissionError):
        location.Location(root)
    assert target.exists() or target.is_symlink()


def test_stale_private_contents_are_not_parsed(root, endpoint):
    target = root / location.LOCATOR
    target.write_text('not JSON or authority')
    target.chmod(0o600)
    owner = location.Location(root)
    try:
        assert not target.exists()
        owner.publish(endpoint)
    finally:
        owner.close()


def test_existing_after_constructor_never_overwritten(root, endpoint):
    owner = location.Location(root)
    target = root / location.LOCATOR
    target.write_text('replacement')
    target.chmod(0o600)
    try:
        with pytest.raises(FileExistsError):
            owner.publish(endpoint)
    finally:
        owner.close()
    assert target.read_text() == 'replacement'
    assert not list(root.glob('*.part'))


def test_close_preserves_replaced_locator_and_reused_descriptor(root, endpoint):
    owner = location.Location(root)
    target = owner.publish(endpoint)
    replacement = root / 'replacement'
    replacement.write_text('keep')
    replacement.replace(target)
    old_fd = owner._fd
    owner.close()
    other = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.dup2(other, old_fd)
        owner.close()
        assert os.fstat(old_fd)
        assert target.read_text() == 'keep'
    finally:
        if other != old_fd:
            os.close(old_fd)
        os.close(other)


def test_concurrent_close(root, endpoint):
    owner = location.Location(root)
    owner.publish(endpoint)
    threads = [threading.Thread(target=owner.close) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    assert not (root / location.LOCATOR).exists()


@pytest.mark.parametrize('value', ['relative.sock', '/tmp/../outside.sock', '/tmp/outside.sock', 'bad\0sock'])
def test_socket_path_restrictions(root, value):
    owner = location.Location(root)
    try:
        with pytest.raises((ValueError, OSError)):
            owner.publish(value)
    finally:
        owner.close()


def test_lock_replacement_denied_and_preserved(root):
    owner = location.Location(root)
    target = root / location.LOCK
    target.unlink()
    target.write_text('replacement')
    target.chmod(0o600)
    try:
        with pytest.raises(PermissionError):
            owner.check()
    finally:
        owner.close()
    assert target.read_text() == 'replacement'


def test_socket_symlink_and_public_directory_denied(root, endpoint):
    owner = location.Location(root)
    linked = root / 'link.sock'
    linked.symlink_to(endpoint)
    try:
        with pytest.raises(PermissionError):
            owner.publish(linked)
    finally:
        owner.close()


def test_actual_crash_between_link_and_unlink_recovers(root, endpoint):
    program = '''
import os, sys
from pathlib import Path
from poolhouse.fleet import producer_location as module
owner = module.Location(Path(sys.argv[1]))
original = os.link
def stop(*args, **kwargs):
    original(*args, **kwargs)
    os._exit(23)
module.os.link = stop
owner.publish(Path(sys.argv[2]))
'''
    result = subprocess.run([sys.executable, '-c', program, str(root), str(endpoint)],
                            env={**os.environ, 'PYTHONPATH': str(Path(location.__file__).parents[2])},
                            timeout=10, capture_output=True)
    assert result.returncode == 23, result.stderr.decode()
    assert (root / location.LOCATOR).stat().st_nlink == 2
    owner = location.Location(root)
    try:
        owner.publish(endpoint)
        assert (root / location.LOCATOR).stat().st_nlink == 1
    finally:
        owner.close()


def test_named_root_replacement_refused(root):
    owner = location.Location(root)
    previous = root.with_name(root.name + '-old')
    root.rename(previous)
    root.mkdir(mode=0o700)
    try:
        with pytest.raises(PermissionError):
            owner.check()
    finally:
        owner.close()
        root.rmdir()
        previous.rename(root)


def test_publish_different_socket_and_modified_locator_refused(root, endpoint):
    owner = location.Location(root)
    target = owner.publish(endpoint)
    with socket.socket(socket.AF_UNIX) as other:
        path = root / 'other.sock'
        other.bind(str(path))
        path.chmod(0o600)
        with pytest.raises(PermissionError):
            owner.publish(path)
    target.write_text('modified')
    try:
        with pytest.raises(PermissionError):
            owner.publish(endpoint)
    finally:
        owner.close()


def test_private_socket_ancestors_required(root):
    folder = root / 'public'
    folder.mkdir(mode=0o755)
    owner = location.Location(root)
    with socket.socket(socket.AF_UNIX) as other:
        path = folder / 'socket'
        other.bind(str(path))
        path.chmod(0o600)
        try:
            with pytest.raises(PermissionError):
                owner.publish(path)
        finally:
            owner.close()


def test_partial_failure_removes_only_own_part(root, endpoint, monkeypatch):
    owner = location.Location(root)
    def failed(*_args, **_kwargs):
        raise OSError('injected link failure')
    monkeypatch.setattr(location.os, 'link', failed)
    try:
        with pytest.raises(OSError):
            owner.publish(endpoint)
        assert not list(root.glob('*.part'))
        assert not (root / location.LOCATOR).exists()
    finally:
        owner.close()


def test_target_replacement_during_promotion_preserved(root, endpoint, monkeypatch):
    owner = location.Location(root)
    original = os.link
    def replaced(*args, **kwargs):
        original(*args, **kwargs)
        target = root / location.LOCATOR
        target.unlink()
        target.write_text('keep replacement')
    monkeypatch.setattr(location.os, 'link', replaced)
    try:
        with pytest.raises(PermissionError):
            owner.publish(endpoint)
    finally:
        owner.close()
    assert (root / location.LOCATOR).read_text() == 'keep replacement'


def test_idempotent_publish_never_adopts_replaced_socket(root, endpoint):
    owner = location.Location(root)
    owner.publish(endpoint)
    endpoint.unlink()
    with socket.socket(socket.AF_UNIX) as replacement:
        replacement.bind(str(endpoint))
        endpoint.chmod(0o600)
        try:
            with pytest.raises(PermissionError):
                owner.publish(endpoint)
        finally:
            owner.close()

"""Audit attribution for descriptor-relative file opens."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
import testreuse_plugin as plugin


@contextmanager
def recording(monkeypatch, checkout):
    with monkeypatch.context() as patch:
        patch.setattr(plugin, 'ROOT', checkout)
        plugin.FILES.pop('descriptor-probe', None)
        plugin.CURRENT.append('descriptor-probe')
        try:
            yield plugin._entry('descriptor-probe')['violations']
        finally:
            plugin.CURRENT.pop()
            plugin.FILES.pop('descriptor-probe', None)


@pytest.fixture
def directories(tmp_path):
    checkout, allowed = tmp_path / 'checkout', tmp_path / 'allowed'
    checkout.mkdir()
    allowed.mkdir()
    first = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    second = os.open(allowed, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield checkout, allowed, first, second
    finally:
        os.close(first)
        os.close(second)


def test_relative_temporary_write_is_not_attributed_to_checkout(directories, monkeypatch):
    checkout, allowed, forbidden_fd, allowed_fd = directories
    with recording(monkeypatch, checkout) as violations:
        wrapped = plugin._probe_open(os.open)
        descriptor = wrapped('manifest.part', os.O_WRONLY | os.O_CREAT, dir_fd=allowed_fd)
        os.write(descriptor, b'temporary')
        os.close(descriptor)
        assert not violations
        descriptor = wrapped('forbidden.txt', os.O_WRONLY | os.O_CREAT, dir_fd=forbidden_fd)
        os.close(descriptor)
        assert violations == {f'wrote {checkout / "forbidden.txt"}'}
    assert (allowed / 'manifest.part').read_bytes() == b'temporary'
    assert not (checkout / 'manifest.part').exists()


def test_nested_calls_and_errors_restore_the_open_context(directories, monkeypatch):
    checkout, _, _, allowed_fd = directories
    monkeypatch.chdir(checkout)
    flags = os.O_WRONLY | os.O_CREAT
    def inner(path, actual_flags, mode, *, dir_fd=None):
        plugin._audit('open', (path, None, actual_flags))
        raise OSError('injected open failure')
    def outer(path, actual_flags, mode, *, dir_fd=None):
        with pytest.raises(OSError):
            plugin._probe_open(inner)('nested.txt', actual_flags)
        plugin._audit('open', (path, None, actual_flags))
        plugin._audit('open', ('builtin.txt', 'w', actual_flags))
        return 123
    with recording(monkeypatch, checkout) as violations:
        assert plugin._probe_open(outer)('allowed.txt', flags, dir_fd=allowed_fd) == 123
        assert violations == {f'wrote {checkout / "nested.txt"}', f'wrote {checkout / "builtin.txt"}'}
        assert getattr(plugin.OPEN_CALLS, 'stack', []) == []


def test_concurrent_open_contexts_do_not_mask_a_forbidden_write(directories, monkeypatch):
    checkout, _, forbidden_fd, allowed_fd = directories
    barrier, lock = threading.Barrier(2), threading.Lock()
    flags = os.O_WRONLY | os.O_CREAT
    def original(path, actual_flags, mode, *, dir_fd=None):
        barrier.wait()
        with lock:
            plugin._audit('open', (path, None, actual_flags | getattr(os, 'O_CLOEXEC', 0)))
        return 123
    def call(descriptor):
        return plugin._probe_open(original)('data.jsonl', flags, dir_fd=descriptor)
    with recording(monkeypatch, checkout) as violations, ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(call, (allowed_fd, forbidden_fd))) == [123, 123]
        assert violations == {f'wrote {checkout / "data.jsonl"}'}

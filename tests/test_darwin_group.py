"""Darwin process inventory rejects hostile kernel replies."""

import ctypes
import os
import types

import pytest

from poolhouse.net import darwin_group as group


class Library:
    def __init__(self, case='zombie'):
        self.case = case
        self.calls = 0

    def proc_listpids(self, kind, leader, buffer, size):
        assert kind == 2 and leader == 12345 and size == 64
        buffer[0] = leader
        if self.case == 'duplicate':
            buffer[1] = leader
            return 8
        if self.case == 'negative-pid':
            buffer[1] = -1
            return 8
        return {'error': -1, 'empty': 0, 'full': 64, 'overflow': 68, 'unaligned': 5}.get(self.case, 4)

    def proc_pidinfo(self, pid, flavor, arg, pointer, size):
        assert (pid, flavor, arg, size) == (12345, 3, 1, 136)
        self.calls += 1
        row = ctypes.cast(pointer, ctypes.POINTER(group._BSDInfo)).contents
        row.pid, row.pgid, row.ppid = pid, pid, os.getpid()
        row.uid, row.status, row.start_sec = os.getuid(), 5, 100
        if self.case == 'short-info':
            return 0
        if self.case in {'pid', 'pgid', 'ppid', 'uid', 'status', 'start_usec'}:
            setattr(row, self.case, {'pid': 2, 'pgid': 2, 'ppid': 2, 'uid': os.getuid() + 1,
                                     'status': 99, 'start_usec': 1_000_000}[self.case])
        if self.case == 'live':
            row.status = 2
        if self.case == 'churn':
            row.start_sec += self.calls
        return size


@pytest.mark.parametrize('case', [
    'error', 'empty', 'full', 'overflow', 'unaligned', 'duplicate', 'negative-pid',
    'short-info', 'pid', 'pgid', 'ppid', 'uid', 'status', 'start_usec', 'churn',
])
def test_incomplete_or_unverified_inventory_never_confirms_cleanup(monkeypatch, case):
    monkeypatch.setattr(group, '_library', lambda: Library(case))
    monkeypatch.setattr(group.os, 'waitid', lambda *args: types.SimpleNamespace(si_pid=12345))
    with pytest.raises(OSError):
        group.quiescent(types.SimpleNamespace(pid=12345))


@pytest.mark.parametrize('case,expected', [('live', False), ('zombie', True)])
def test_only_stable_positive_zombie_inventory_confirms_cleanup(monkeypatch, case, expected):
    library = Library(case)
    monkeypatch.setattr(group, '_library', lambda: library)
    monkeypatch.setattr(group.os, 'waitid', lambda *args: types.SimpleNamespace(si_pid=12345))
    assert group.quiescent(types.SimpleNamespace(pid=12345)) is expected
    assert library.calls == 2


def test_live_leader_does_not_confirm_cleanup_or_query_inventory(monkeypatch):
    monkeypatch.setattr(group.os, 'waitid', lambda *args: None)
    monkeypatch.setattr(group, '_library', lambda: pytest.fail('unexpected inventory'))
    assert not group.quiescent(types.SimpleNamespace(pid=12345))


def test_lost_child_custody_never_confirms_cleanup(monkeypatch):
    monkeypatch.setattr(group, '_library', Library)
    answers = iter([types.SimpleNamespace(si_pid=12345), None])
    monkeypatch.setattr(group.os, 'waitid', lambda *args: next(answers))
    with pytest.raises(OSError):
        group.quiescent(types.SimpleNamespace(pid=12345))


def test_missing_native_symbol_is_an_explicit_cleanup_error(monkeypatch):
    monkeypatch.setattr(group.ctypes, 'CDLL', lambda *a, **k: object())
    with pytest.raises(OSError, match='inventory ABI is unavailable'):
        group._library()

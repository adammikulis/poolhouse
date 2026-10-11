"""Bounded advisory hooks reuse authentication and preserve unread alerts."""

import os
import subprocess
import sys
import time

import psutil
import pytest
from harness_kit import SRC

from poolhouse import harnesshook
from poolhouse.workspace import notification_reader as reader, worktree_lifecycle


@pytest.mark.parametrize('emit', [True, False])
def test_stalled_worker_is_reaped_before_hook_deadline(tmp_path, monkeypatch, emit):
    pid_file = tmp_path / 'pid'
    actual = harnesshook.platform.start_process

    def stalled(command, **kwargs):
        code = ("import os,time; from pathlib import Path; "
                f"Path({str(pid_file)!r}).write_text(str(os.getpid())); "
                + ("print('A direct question is waiting; run inbox',flush=True); " if emit else '')
                + 'time.sleep(30)')
        return actual([sys.executable, '-c', code], **kwargs)
    monkeypatch.setattr(harnesshook.platform, 'start_process', stalled)
    monkeypatch.setattr(harnesshook, 'NUDGE_S', 0.3)
    began = time.monotonic()
    context = harnesshook.post('worker')['hookSpecificOutput']['additionalContext']
    assert time.monotonic() - began < 2
    assert 'notification unavailable' in context
    assert ('direct question' in context) is emit
    assert not psutil.pid_exists(int(pid_file.read_text()))


def test_reader_notifies_before_checkpoint(monkeypatch, capsys, tmp_path):
    calls = []

    def read(agent, cwd):
        calls.append('read')
        return 'one unread'

    def checkpoint(base, actor):
        assert capsys.readouterr().out == 'one unread\n'
        assert actor == 'bound-worker'
        calls.append('checkpoint')
    monkeypatch.setattr(reader, 'read', read)
    monkeypatch.setattr(reader, 'checkpoint', checkpoint)
    reader.notify('bound-worker', tmp_path)
    assert calls == ['read', 'checkpoint']


def test_unchanged_checkpoint_avoids_a_rewrite(monkeypatch, tmp_path):
    scope = {'path': str(tmp_path), 'commits': ['commit'], 'branches': ['branch']}
    monkeypatch.setattr(reader.worktree_lifecycle, 'scopes', lambda *args: [scope])
    monkeypatch.setattr(reader.repo, 'git', lambda path, *args: 'commit' if args[0] == 'rev-parse' else 'branch')
    monkeypatch.setattr(reader.worktree_lifecycle, 'remember', lambda *args: pytest.fail('rewrote unchanged lifecycle'))
    reader.checkpoint(tmp_path, 'bound-worker')


def test_changed_checkpoint_preserves_bound_owner(monkeypatch, tmp_path):
    scope = {'path': str(tmp_path), 'commits': [], 'branches': []}
    seen = []
    monkeypatch.setattr(reader.worktree_lifecycle, 'scopes', lambda *args: [scope])
    monkeypatch.setattr(reader.repo, 'git', lambda *args: 'changed')
    monkeypatch.setattr(reader.worktree_lifecycle, 'remember', lambda *args: seen.append(args))
    reader.checkpoint(tmp_path, 'bound-worker')
    assert seen == [(tmp_path, 'bound-worker', str(tmp_path))]


def test_urgent_action_survives_host_context_limit():
    text = reader.compact('workspace: 15 waiting for you (from ' + 'sender' * 100
                          + '). A direct question is waiting on you: run poolhouse-workspace inbox now and answer it')
    shown = ('workspace (data from other agents): ' + text)[:250]
    assert 'direct question' in shown and 'inbox now and answer it' in shown


def test_cancelled_checkpoint_keeps_committed_scopes_and_releases_lock(tmp_path):
    base = tmp_path / 'state'
    base.mkdir()
    worktree_lifecycle._edit(base, lambda rows: rows.__setitem__('original', {'owner': 'worker'}))
    code = ("import sys,time; from pathlib import Path; "
            "from poolhouse.workspace import worktree_lifecycle as life\n"
            "def change(rows):\n"
            "    rows['pending'] = {'owner': 'worker'}\n"
            "    print('checkpoint staged', flush=True)\n"
            "    time.sleep(30)\n"
            f"life._edit(Path({str(base)!r}), change)\n")
    child = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True,
                             env={**os.environ, 'PYTHONPATH': SRC})
    try:
        assert child.stdout.readline().strip() == 'checkpoint staged'
    finally:
        child.terminate()
        child.wait(timeout=5)
        child.stdout.close()
    assert not psutil.pid_exists(child.pid)
    assert len(worktree_lifecycle.scopes(base, 'worker')) == 1
    worktree_lifecycle._edit(base, lambda rows: rows.__setitem__('next', {'owner': 'worker'}))
    assert len(worktree_lifecycle.scopes(base, 'worker')) == 2


def test_timeout_reaps_worker_and_its_spawned_descendant(tmp_path, monkeypatch):
    pid_file = tmp_path / 'child'
    actual = harnesshook.platform.start_process

    def descendants(command, **kwargs):
        code = ("import subprocess,sys,time; from pathlib import Path; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                f"Path({str(pid_file)!r}).write_text(str(child.pid)); "
                "print('A direct question is waiting; run inbox',flush=True); time.sleep(30)")
        return actual([sys.executable, '-c', code], **kwargs)
    monkeypatch.setattr(harnesshook.platform, 'start_process', descendants)
    monkeypatch.setattr(harnesshook, 'NUDGE_S', 0.3)
    began = time.monotonic()
    context = harnesshook.post('worker')['hookSpecificOutput']['additionalContext']
    assert time.monotonic() - began < 2
    assert 'direct question' in context and 'notification unavailable' in context
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 1
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not psutil.pid_exists(pid)

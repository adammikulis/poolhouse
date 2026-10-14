"""Landing retains the worktree of an actual or uncertain background test job."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import land_check
import land_finish
import land_recover
import land_run
import testjobs
import testreuse_store


@pytest.fixture
def tracked(tmp_path, monkeypatch):
    worktree = tmp_path / 'tree'
    worktree.mkdir()
    store = tmp_path / 'store'
    monkeypatch.setattr(land_finish.reuse, 'reuse_base', lambda: store)
    monkeypatch.setattr(land_finish.testruns, 'scope', lambda root: 'fixture')
    jobs = testjobs.Jobs(store / 'fixture')
    state = {'worktree': str(worktree), 'full': {'status': 'started', 'job': 'fixture-full', 'worktree': str(worktree)}}
    jobs.write('fixture-full', 'spec.json', {'id': 'fixture-full', 'root': str(worktree), 'argv': ['full']})
    return jobs, state, tmp_path


def test_detached_job_alive_without_submission_launcher(tracked):
    jobs, state, root = tracked
    jobs.write('fixture-full', 'status.json', {'state': 'running', 'pid': os.getpid(),
              'started': testreuse_store.process_start(os.getpid()), 'at': time.time() - 10})
    assert land_finish.full_running(root, state)
    jobs.write('fixture-full', 'status.json', {'state': 'done', 'exit': 0})
    assert not land_finish.full_running(root, state)


def test_reused_pid_is_not_a_live_job(tracked):
    jobs, state, root = tracked
    jobs.write('fixture-full', 'status.json', {'state': 'running', 'pid': os.getpid(),
              'started': testreuse_store.process_start(os.getpid()) - 1000, 'at': time.time() - 10})
    assert not land_finish.full_running(root, state)


@pytest.mark.parametrize('full', [None, [], 'malformed', {'status': 'pending'}, {'status': 'unknown'}])
def test_uncertain_full_record_is_retained(tmp_path, full):
    assert land_finish.full_running(tmp_path, {'full': full})


@pytest.mark.parametrize('changed', [{'id': 'other'}, {'root': '/other'}, {'argv': ['gate']}])
def test_substituted_job_binding_is_retained(tracked, changed):
    jobs, state, root = tracked
    spec = jobs.read('fixture-full', 'spec.json')
    jobs.write('fixture-full', 'spec.json', {**spec, **changed})
    jobs.write('fixture-full', 'status.json', {'state': 'done'})
    assert land_finish.full_running(root, state)


@pytest.mark.parametrize('outcome', [SimpleNamespace(returncode=1, stdout=''), SimpleNamespace(returncode=0, stdout=''), subprocess.TimeoutExpired('submit', 30)])
def test_uncertain_submission_never_claims_no_job(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(land_check, 'other_full_run', lambda root: '')
    def submit(*args, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(land_check.subprocess, 'run', submit)
    full = land_check.start_full(tmp_path, tmp_path)
    assert full['status'] == 'unknown'
    assert land_finish.full_running(tmp_path, {'full': full})


def test_pending_record_survives_latest_batch_overwrite(tmp_path, monkeypatch):
    common = tmp_path / 'common'
    common.mkdir()
    monkeypatch.setattr(land_run.lg, 'common_dir', lambda root: common)
    monkeypatch.setattr(land_run, 'tree_hash', lambda root: 'fixture-tree')
    first = land_run.Batch(tmp_path, 'dev', 'base', tmp_path / 'first', 'land/first', [])
    second = land_run.Batch(tmp_path, 'dev', 'base', tmp_path / 'second', 'land/second', [])
    land_run.save(first, 'full-pending', {'full': {'status': 'pending'}})
    land_run.save(second, 'verified', {'full': {}})
    records = [json.loads(path.read_text()) for path in (common / 'land' / 'batches').glob('*.json')]
    assert any(state['integration'] == 'land/first' and land_finish.full_running(tmp_path, state) for state in records)
    assert land_recover.sweep(tmp_path) == []


def test_submission_starts_only_after_pending_record(tmp_path, monkeypatch):
    from land_support import DEV, Project
    project = Project(tmp_path)
    project.branch('leaf', {'docs/leaf.md': 'fixture\n'})
    monkeypatch.setattr(land_run, 'connect', lambda **kwargs: SimpleNamespace(whoami=lambda: SimpleNamespace(name='fixture-agent')))
    monkeypatch.setattr(land_run, 'verify', lambda *args: ([], 'docs', 'fixture-full'))
    def crash(worktree, common):
        state = land_finish.load(project.root)
        assert state['status'] == 'full-pending'
        assert state['full']['status'] == 'pending'
        assert list((common / 'land' / 'batches').glob('*.json'))
        raise RuntimeError('submission interrupted')
    monkeypatch.setattr(land_check, 'start_full', crash)
    with pytest.raises(RuntimeError, match='submission interrupted'):
        land_run.run(project.root, DEV, ['leaf'], {'dry_run': False, 'conflicts': 'eject', 'workers': 1})

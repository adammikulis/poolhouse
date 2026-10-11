"""Interrupted lifecycle writes preserve committed ownership records."""

import os
import subprocess
import sys

import pytest
from harness_kit import SRC

from poolhouse.workspace import worktree_lifecycle as lifecycle

STORE = 'worktree-lifecycle.json'


def seed(base):
    lifecycle._edit(base, lambda rows: rows.__setitem__('scope', {'owner': 'worker', 'label': 'worker', 'commits': ['retained']}))


def test_killed_writer_preserves_active_records(tmp_path):
    seed(tmp_path)
    before = (tmp_path / STORE).read_bytes()
    script = """from pathlib import Path
import os, sys
from poolhouse.workspace.worktree_lifecycle import _edit
def change(rows):
    rows['unpublished'] = {'owner': 'worker'}
    os._exit(23)
_edit(Path(sys.argv[1]), change)
"""
    done = subprocess.run([sys.executable, '-c', script, str(tmp_path)], env={**os.environ, 'PYTHONPATH': SRC}, check=False)
    assert done.returncode == 23
    assert (tmp_path / STORE).read_bytes() == before
    assert lifecycle.scopes(tmp_path, 'worker')[0]['commits'] == ['retained']
    lifecycle._edit(tmp_path, lambda rows: rows.__setitem__('next', {'owner': 'worker', 'label': ''}))
    assert len(lifecycle.scopes(tmp_path, 'worker')) == 2


def test_failed_writer_preserves_committed_records(tmp_path):
    seed(tmp_path)
    before = (tmp_path / STORE).read_bytes()

    def interrupted(rows):
        rows['unpublished'] = {'owner': 'worker'}
        raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError, match='interrupted'):
        lifecycle._edit(tmp_path, interrupted)
    assert (tmp_path / STORE).read_bytes() == before
    assert len(lifecycle.scopes(tmp_path, 'worker')) == 1


def test_read_does_not_rewrite_or_add_files(tmp_path):
    seed(tmp_path)
    before = (tmp_path / STORE).stat().st_mtime_ns
    names = sorted(path.name for path in tmp_path.iterdir())
    assert lifecycle.scopes(tmp_path, 'worker')
    assert (tmp_path / STORE).stat().st_mtime_ns == before
    assert sorted(path.name for path in tmp_path.iterdir()) == names

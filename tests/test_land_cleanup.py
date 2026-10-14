"""Integration ownership metadata uses the authenticated creating session."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import land_run
from land_support import DEV, Project

from poolhouse import trees
from poolhouse.board.client import Denied


def test_integration_records_authenticated_owner(tmp_path, monkeypatch):
    project = Project(tmp_path)
    monkeypatch.setattr(land_run, 'connect', lambda **kwargs: SimpleNamespace(name='cached-name', whoami=lambda: SimpleNamespace(name='agent-fixture')))
    batch = land_run.create(project.root, DEV, [])
    recorded = trees.recorded(project.root)['trees'][str(batch.wt)]
    assert recorded['owner'] == 'agent-fixture'
    assert recorded['pid'] == 0
    assert recorded['purpose'] == 'integration batch'


def test_missing_session_does_not_assign_generic_identity(tmp_path, monkeypatch):
    project = Project(tmp_path)

    def refuse(**kwargs):
        raise Denied('denied', 'no token')

    monkeypatch.setattr(land_run, 'connect', refuse)
    batch = land_run.create(project.root, DEV, [])
    assert batch.wt.exists()
    assert str(batch.wt) not in trees.recorded(project.root)['trees']


def test_revoked_session_does_not_register_cached_owner(tmp_path, monkeypatch):
    project = Project(tmp_path)

    def refuse():
        raise Denied('denied', 'revoked token')

    monkeypatch.setattr(land_run, 'connect', lambda **kwargs: SimpleNamespace(name='cached-name', whoami=refuse))
    batch = land_run.create(project.root, DEV, [])
    assert str(batch.wt) not in trees.recorded(project.root)['trees']

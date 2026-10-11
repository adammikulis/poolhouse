"""A board on a real node with a lead, its coding worker on a git checkout that holds its model, an independent session, and the
task flows between them: the node's tasks, the scheduler's allocations and the worker's worktrees."""

from __future__ import annotations

import pytest
from git_template import copy_checkout
from local_kit import build

from poolhouse.board.tasks import Tasks
from poolhouse.net import git
from poolhouse.workspace import localagent as la, task_scheduler

MODEL = "qwen"


def _baseline(root):
    repository, source = root / 'repository', root / 'source'
    repository.mkdir()
    git.run(['init', str(repository)])
    (repository / 'code.py').write_text('BASELINE = True\n')
    git.run(['add', 'code.py'], cwd=repository)
    git.run(['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
             'commit', '-m', 'baseline'], cwd=repository)
    git.run(['worktree', 'add', '-b', 'source', str(source)], cwd=repository)


def worker_on(kit, source, name='native-worker', model=MODEL, parent=None, **fields):
    """A coding worker on ``source``: a subagent of the lead that holds the slot of its model, saved as a local agent;
    ``(member, record)``."""
    member = kit.node.member(name, parent=parent or kit.lead)
    kit.session(member).call('lease_acquire', resources=[{'type': 'model_slot', 'model': model}], ttl_s=5000)
    saved = la.Agent(name, model, identity=member.name, board=kit.node.board, profile='coding', project=str(source),
                     pid=555, process_started=42, **{'harness': 'pi', **fields})
    la.save(kit.base, saved)
    return member, saved


@pytest.fixture
def board(workspace_node, monkeypatch, tmp_path):
    """The team with a queued task, assigned to the worker on its model slot with its checkout reserved."""
    kit = build(workspace_node, monkeypatch)
    copy_checkout('taskboard', _baseline, tmp_path, 'repository', 'source')
    kit.source = tmp_path / 'source'
    kit.parent = kit.session(kit.lead)
    kit.member, kit.runner = worker_on(kit, kit.source)
    kit.child = kit.session(kit.member)
    kit.worker_id = kit.member.name
    kit.reviewer = kit.session(kit.stranger)
    kit.board = Tasks(kit.parent)
    kit.work = Tasks(kit.child)
    kit.spec = {'title': 'Inspect native simulation', 'description': 'Collect a reproducible replay.',
                'acceptance': ['Replay passes'], 'source_key': 'repo:demo/sim:issue:42'}
    kit.task = kit.board.create(kit.spec)
    kit.allocation = task_scheduler.assign_next(kit.parent, kit.base, kit.worker_id, task=kit.task['id'])
    return kit


def proposed(kit, allocation=None, ident=None):
    """The worker claims the task and submits a proposal; the proposal."""
    ident = ident or kit.task['id']
    kit.work.claim(ident, (allocation or kit.allocation)['allocation_id'])
    return kit.work.submit(ident, {'artifacts': {'replay.json': 'a' * 64},
                                   'checks': [{'name': 'Worker claims tests', 'passed': True}],
                                   'summary': 'Native replay proposed',
                                   'provenance': {'commit': 'abc123', 'environment': 'native-python',
                                                  'model': 'qwen', 'runtime': 'Agents SDK'}})


def accepted():
    return {'accepted': True, 'reason': 'Independent native replay passed',
            'checks': [{'name': 'Replay passes', 'passed': True}]}

"""Task branches exclude prior uncommitted worker changes."""

from pathlib import Path

import pytest
from local_kit import team  # noqa: F401
from node_kit import node_binary, workspace_node  # noqa: F401
from taskboard_kit import worker_on

from poolhouse.board.tasks import Tasks
from poolhouse.errors import Denied
from poolhouse.net import git
from poolhouse.workspace import task_authority, task_scheduler, task_worktrees


def repository(root, name='main'):
    primary = root / name
    primary.mkdir()
    git.run(['init', str(primary)])
    (primary / 'code.py').write_text('ORIGINAL = True\n')
    git.run(['add', 'code.py'], cwd=primary)
    git.run(['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
             'commit', '-m', 'baseline'], cwd=primary)
    return primary


def reserved(kit, project, title='Task'):
    """A worker on ``project`` under the lead, a person's task assigned to it with its checkout reserved; ``(task, scope, worker)``."""
    member, _ = worker_on(kit, project)
    task = Tasks(kit.person).create({'title': title, 'description': 'Current code', 'acceptance': ['Checks pass'],
                                     'source_key': 'person-' + title})['id']
    task_scheduler.assign_next(kit.session(kit.lead), kit.base, member.name, task=task)
    return task, Tasks(kit.session(kit.lead)).get(task)['scope'], member


def test_clean_task_baseline_claims_and_parent_authority(team, tmp_path):  # noqa: F811
    primary, source = repository(tmp_path), tmp_path / 'source'
    git.run(['worktree', 'add', '-b', 'source', str(source)], cwd=primary)
    (source / 'code.py').write_text('CONTAMINATED = True\n')
    member, _ = worker_on(team, source)
    parent = team.session(team.lead)
    task = Tasks(team.person).create({'title': 'Person task', 'description': 'Fix current code',
                                      'acceptance': ['Checks pass']})['id']
    with pytest.raises(Denied, match="creator of a task that names the worker"):
        task_authority.authorize(team.session(team.stranger), team.base, member.name, task)
    with pytest.raises(Denied, match="creator of a task that names the worker"):
        task_authority.authorize(parent, team.base, member.name, task)
    got = task_scheduler.assign_next(parent, team.base, member.name, task=task)
    prepared = Tasks(parent).get(task)['scope']
    target = tmp_path / ('main-' + task)
    assert prepared['project'] == str(target) == got['project']
    assert not target.exists()
    assert prepared['state'] == 'reserved'
    worker = team.session(member)
    task_worktrees.activate(worker, Tasks(worker).get(task))
    assert (target / 'code.py').read_text() == 'ORIGINAL = True\n'
    assert (source / 'code.py').read_text() == 'CONTAMINATED = True\n'
    assert prepared['baseline_commit'] == git.head(source)
    held = Tasks(worker).get(task)['scope']
    assert held == {**prepared, 'state': 'active'}
    runner, spec = task_authority.authorize(parent, team.base, member.name, task, parent_may=True)
    assert task_worktrees.plan(parent.name, runner, spec) == held
    assert {c.owner for c in worker.claims('worktree') if c.key == str(target)} == {member.name}
    assert {c.key for c in worker.claims('branch')} == {prepared['branch']}


def test_task_target_inside_a_checkout_is_refused_before_creation(tmp_path):
    from poolhouse import worktreerules
    primary = tmp_path / 'repo'
    primary.mkdir()
    git.run(['init', str(primary)])
    assert 'inside checkout' in worktreerules.worktree_refusal(primary / '.worktrees' / 'task', primary)
    assert not worktreerules.worktree_refusal(tmp_path / 'task', primary)


def test_shell_guard_refuses_nested_creation_in_primary_and_worktree(tmp_path):
    from poolhouse import worktreerules
    primary = tmp_path / 'repo'
    (primary / 'scripts' / 'hooks').mkdir(parents=True)
    (primary / 'scripts' / 'hooks' / 'primary-only').write_text('')
    (primary / 'code.py').write_text('ready = True\n')
    git.run(['init', str(primary)])
    git.run(['add', '.'], cwd=primary)
    git.run(['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'baseline'], cwd=primary)
    work = tmp_path / 'work'
    git.run(['worktree', 'add', '-b', 'work', str(work)], cwd=primary)
    assert 'inside checkout' in worktreerules.bash_refusal('git worktree add -b task .worktrees/task HEAD', str(primary))
    assert 'inside checkout' in worktreerules.bash_refusal('git worktree add -b task nested/task HEAD', str(work))
    assert not worktreerules.bash_refusal('git worktree add -b task ../task HEAD', str(primary))


def test_primary_baseline_is_read_only_and_execution_materializes_beside_it(team, tmp_path):  # noqa: F811
    primary = repository(tmp_path, 'repo')
    task, scope, member = reserved(team, primary)
    target = tmp_path / ('repo-' + task)
    assert scope['project'] == str(target) and not target.exists()
    worker = team.session(member)
    task_worktrees.activate(worker, Tasks(worker).get(task))
    assert (target / '.git').is_file()
    (target / 'code.py').write_text('CHANGED = True\n')
    assert (primary / 'code.py').read_text() == 'ORIGINAL = True\n'
    assert not git.run(['status', '--porcelain'], cwd=primary).stdout.strip()


def test_crash_created_checkout_is_validated_and_promoted_without_losing_changes(team, tmp_path):  # noqa: F811
    primary = repository(tmp_path, 'repo')
    task, scope, member = reserved(team, primary)
    target = Path(scope['project'])
    git.run(['worktree', 'add', '-b', scope['branch'], str(target), scope['baseline_commit']], cwd=primary)
    (target / 'pending.txt').write_text('Unique pending work')
    worker = team.session(member)
    active = task_worktrees.activate(worker, Tasks(worker).get(task))
    assert active['state'] == 'active'
    assert task_worktrees.binding(Tasks(worker).get(task), member.name)['state'] == 'active'
    assert (target / 'pending.txt').read_text() == 'Unique pending work'


def test_a_checkout_another_worker_was_reserved_for_cannot_be_taken(team, tmp_path):  # noqa: F811
    primary = repository(tmp_path, 'repo')
    task, _, _ = reserved(team, primary)
    other = team.node.member('other-worker', parent=team.lead)
    with pytest.raises(Denied, match='no prepared isolated worktree'):
        task_worktrees.binding(Tasks(team.session(team.lead)).get(task), other.name)

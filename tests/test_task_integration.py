"""Real Git worktrees and a local bare remote enforce reviewed development integration."""

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from node_kit import node_binary, workspace_node  # noqa: F401
from taskboard_kit import accepted, board as _board_fixture

from poolhouse import harness_claims
from poolhouse.board.tasks import Tasks
from poolhouse.errors import Denied
from poolhouse.workspace import (
    integration_git as repo,
    integration_staging,
    localagent,
    task_cli,
    task_integration,
    task_scheduler,
    task_worktrees,
)

board = _board_fixture

GATES = '''import json, os, sys
with open(os.environ['INTEGRATION_GATE_LOG'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if os.environ.get('FAIL_INTEGRATION_GATE') == sys.argv[1]:
    sys.exit(1)
'''


def install_push_hook(primary):
    hook = Path(__file__).resolve().parents[1] / 'scripts' / 'hooks' / 'pre-push'
    interpreter = Path(sys.executable).as_posix()
    checker = (hook.parent / 'pushed').as_posix()
    installed = primary / '.git' / 'hooks' / 'pre-push'
    text = hook.read_text().replace('"${PYTHON:-python3}" "$here/pushed"', f'\"{interpreter}\" \"{checker}\"')
    text = text.replace('here=$(cd "$(dirname "$self")" && pwd)', f'here="{hook.parent.as_posix()}"')
    installed.write_text(text.replace('python="${PYTHON:-python3}"', f'python="{interpreter}"'))
    installed.chmod(0o755)


def integrate(kit, *, publish=False, who=None):
    return task_integration.integrate(who or kit.parent, kit.base, kit.task['id'], publish=publish)


def detail(kit, who=None):
    return Tasks(who or kit.parent).get(kit.task['id'])


@pytest.fixture
def project(board, tmp_path, monkeypatch, request):
    param = getattr(request, 'param', '')
    project_root = tmp_path / 'integration-project'
    project_root.mkdir()
    primary, origin = project_root / 'repo', project_root / 'remote.git'
    primary.mkdir()
    repo.git(primary, 'init', '--initial-branch=0.2dev')
    repo.git(primary, 'config', 'user.name', 'Isolated reviewer')
    repo.git(primary, 'config', 'user.email', 'reviewer@example.test')
    (primary / 'scripts').mkdir()
    (primary / 'scripts' / 'test').write_text(GATES)
    (primary / '.gitignore').write_text('.claude/worktrees/\n')
    (primary / 'sim.py').write_text('speed = 1\n')
    repo.git(primary, 'add', '--', '.gitignore', 'scripts/test', 'sim.py')
    repo.git(primary, 'commit', '-m', 'chore: isolated baseline')
    repo.git(tmp_path, 'init', '--bare', str(origin))
    repo.git(primary, 'remote', 'add', 'origin', str(origin))
    repo.git(primary, 'push', '-u', 'origin', '0.2dev')
    template = project_root / 'source-template'
    repo.git(primary, 'worktree', 'add', '-b', 'source-template', str(template))
    localagent.save(board.base, replace(localagent.load(board.base, 'native-worker'), project=str(template)))
    creator = board.person if param == 'human_creator' else board.parent
    board.task = Tasks(creator).create({**board.spec, 'source_key': 'native-integration'})
    allocation = task_scheduler.assign_next(board.parent, board.base, board.worker_id, task=board.task['id'])
    worktree = board.board.get(board.task['id'])['scope']
    source = Path(worktree['project'])
    board.work.claim(board.task['id'], allocation['allocation_id'])
    task_worktrees.activate(board.child, board.work.get(board.task['id']))
    if param == 'native_claims':
        harness_claims.reserve('Write', {'file_path': str(source / 'sim.py')},
                               str(source), board.worker_id, [str(source)])
        harness_claims.reserve('Bash', {'command': 'git add sim.py'},
                               str(source), board.worker_id, [str(source)])
    (source / 'sim.py').write_text('speed = 2\n')
    repo.git(source, 'add', '--', 'sim.py')
    repo.git(source, 'commit', '-m', 'fix: reviewed simulation speed')
    patch = repo.git(source, 'diff', '--binary', '--full-index', worktree['baseline_commit'], 'HEAD', binary=True)
    if param == 'bad_patch':
        patch += b'not the reviewed full diff\n'
    (source / '.task.patch').write_bytes(patch)
    (source / '.task-report.md').write_text('ready\n')
    repo.git(source, 'add', '--', '.task.patch', '.task-report.md')
    repo.git(source, 'commit', '-m', 'chore: record task artifacts')
    artifacts = {name: hashlib.sha256(repo.git(source, 'show', f'HEAD:{name}', binary=True)).hexdigest()
                 for name in ('sim.py', '.task.patch', '.task-report.md')}
    if param == 'wrong_artifact':
        artifacts['sim.py'] = 'b' * 64
    proposal = board.work.submit(board.task['id'],
                                 {'summary': 'Native source is committed.', 'artifacts': artifacts,
                                  'checks': [{'name': 'Worker claim', 'passed': True}],
                                  'provenance': {'commit': repo.git(source, 'rev-parse', 'HEAD')}})
    install_push_hook(primary)
    log = tmp_path / 'gate-commands.jsonl'
    monkeypatch.setenv('INTEGRATION_GATE_LOG', str(log))
    return {'primary': primary, 'source': source, 'origin': origin, 'log': log,
            'proposal': proposal, 'baseline': worktree['baseline_commit'], 'worktree': worktree}


def claims_of(kit, who=None):
    """What the board shows as claimed, by kind and key."""
    return {(c.kind, c.key): c.owner for c in (who or kit.parent).claims()}


def test_reviewed_native_patch_runs_maintained_gates_then_fast_forwards_development(board, project):
    review = board.board.review(board.task['id'], accepted())
    result = integrate(board, publish=True)
    assert result['state'] == 'published' and result['review_hash'] == review['review_hash']
    commands = [json.loads(line) for line in project['log'].read_text().splitlines()]
    assert commands == [['quick', '--base', project['baseline']], ['gate'],
                        ['all', 'tests/test_serve_no_bypass.py', '--redteam']]
    assert repo.git(project['primary'], 'branch', '--show-current') == '0.2dev'
    assert repo.git(project['primary'], 'rev-parse', 'HEAD') == result['commit']
    assert repo.git(project['origin'], 'rev-parse', 'refs/heads/0.2dev') == result['commit']
    assert repo.ancestor(project['primary'], project['baseline'], result['commit'])
    assert repo.git(project['origin'], 'for-each-ref', '--format=%(refname)', 'refs/heads') == 'refs/heads/0.2dev'
    assert integrate(board, publish=True) == result
    task = detail(board)
    assert len(task['integrations']) == 1
    assert {event['state'] for event in task['integration_events']} == {'scope_returned', 'candidate', 'integrated', 'publication_confirmed', 'cleanup_required', 'published'}
    assert len(task['integrations'][0]['checks']) == 3 and task['integrations'][0]['commit'] == result['commit']
    assert not project['source'].exists()
    assert (('worktree', str(project['source']))) not in claims_of(board)
    assert task['state'] == 'completed' and task['landed_commit'] == result['commit']


@pytest.mark.redteam
def test_unaccepted_and_unrelated_requests_make_no_git_changes(board, project):
    for who in (board.parent, board.reviewer):
        with pytest.raises(Denied):
            integrate(board, who=who)
    assert repo.git(project['primary'], 'rev-parse', 'HEAD') == project['baseline']
    assert not project['log'].exists()
    assert claims_of(board)[('worktree', str(project['source']))] == board.worker_id


@pytest.mark.redteam
def test_an_unrelated_session_cannot_integrate_an_accepted_task(board, project):
    board.board.review(board.task['id'], accepted())
    with pytest.raises(Denied):
        integrate(board, who=board.reviewer)
    assert repo.git(project['primary'], 'rev-parse', 'HEAD') == project['baseline']
    assert not project['log'].exists()
    assert claims_of(board)[('worktree', str(project['source']))] == board.worker_id


@pytest.mark.redteam
@pytest.mark.parametrize('change', ['new_commit', 'dirty', 'main'])
def test_stale_sources_and_release_target_are_refused_before_gates(board, project, change):
    board.board.review(board.task['id'], accepted())
    if change == 'main':
        repo.git(project['primary'], 'branch', '-m', 'main')
    else:
        (project['source'] / 'sim.py').write_text('speed = 99\n')
        if change == 'new_commit':
            repo.git(project['source'], 'add', '--', 'sim.py')
            repo.git(project['source'], 'commit', '-m', 'fix: unreviewed source')
    with pytest.raises((Denied, ValueError)):
        integrate(board, publish=True)
    assert not project['log'].exists()


@pytest.mark.redteam
@pytest.mark.parametrize('project', ['wrong_artifact'], indirect=True)
def test_a_proposal_naming_another_artifact_hash_is_refused_before_gates(board, project):
    board.board.review(board.task['id'], accepted())
    with pytest.raises(Denied, match='reviewed artifact hash changed'):
        integrate(board, publish=True)
    assert not project['log'].exists()


def test_parallel_reviewed_branch_merges_in_owned_candidate_without_rebasing(board, project):
    board.board.review(board.task['id'], accepted())
    (project['primary'] / 'parallel.py').write_text('independent = True\n')
    repo.git(project['primary'], 'add', '--', 'parallel.py')
    repo.git(project['primary'], 'commit', '-m', 'feat: independent parallel development')
    before = repo.git(project['primary'], 'rev-parse', 'HEAD')
    result = integrate(board, publish=True)
    assert result['state'] == 'published'
    assert repo.ancestor(project['primary'], before, result['commit'])
    assert repo.ancestor(project['primary'], project['proposal']['provenance']['commit'], result['commit'])
    assert not project['source'].exists()
    assert (project['primary'] / 'parallel.py').exists()


@pytest.mark.redteam
@pytest.mark.parametrize('failure', ['conflict', 'gate'])
def test_failed_merge_or_gate_preserves_candidate_and_never_publishes(board, project, monkeypatch, failure):
    board.board.review(board.task['id'], accepted())
    if failure == 'conflict':
        (project['primary'] / 'sim.py').write_text('speed = 3\n')
        repo.git(project['primary'], 'add', '--', 'sim.py')
        repo.git(project['primary'], 'commit', '-m', 'fix: conflicting independent change')
    else:
        monkeypatch.setenv('FAIL_INTEGRATION_GATE', 'gate')
    before = repo.git(project['primary'], 'rev-parse', 'HEAD')
    result = integrate(board, publish=True)
    assert result['state'] == 'blocked'
    assert repo.git(project['primary'], 'rev-parse', 'HEAD') == before
    assert repo.git(project['origin'], 'rev-parse', 'refs/heads/0.2dev') == project['baseline']
    assert repo.git(project['source'], 'rev-parse', 'HEAD') == project['proposal']['provenance']['commit']


@pytest.mark.redteam
def test_maintained_push_hook_warns_of_foreign_merged_worktrees_and_preserves_them(board, project, tmp_path):
    board.board.review(board.task['id'], accepted())
    foreign = tmp_path / 'foreign-worktree'
    repo.git(project['primary'], 'worktree', 'add', '-b', 'other-agent/active', str(foreign))
    (foreign / 'foreign.py').write_text('independent = True\n')
    repo.git(foreign, 'add', '--', 'foreign.py')
    repo.git(foreign, 'commit', '-m', 'feat: other agent change')
    repo.git(project['primary'], 'merge', '--ff-only', 'other-agent/active')
    other = board.node.member('other-agent', parent=board.lead)
    board.session(other).claim('worktree', str(foreign))
    result = integrate(board, publish=True)
    assert result['state'] == 'published' and result['checks']
    assert foreign.exists() and not Path(repo.git(foreign, 'rev-parse', '--git-path', 'locked')).exists()
    assert claims_of(board)[('worktree', str(foreign))] == other.name
    assert repo.git(project['origin'], 'rev-parse', 'refs/heads/0.2dev') == result['commit']


@pytest.mark.redteam
@pytest.mark.parametrize('project', ['bad_patch'], indirect=True)
def test_accepted_artifact_hashes_cannot_substitute_an_unrelated_native_patch(board, project):
    board.board.review(board.task['id'], accepted())
    with pytest.raises(Denied, match='exact reviewed source changes'):
        integrate(board, publish=True)
    assert not project['log'].exists()


@pytest.mark.redteam
def test_worker_or_unrelated_agent_cannot_return_the_reviewed_child_scope(board, project):
    unrelated = board.node.member('unrelated', parent=board.lead)
    for who in (board.session(unrelated), board.session(board.stranger)):
        with pytest.raises(Exception, match=r'subagent|only a parent|denied'):
            who.release('worktree', str(project['source']), held_by=board.worker_id)
    assert claims_of(board)[('worktree', str(project['source']))] == board.worker_id


@pytest.mark.redteam
def test_reviewed_artifact_names_stay_literal_git_arguments_and_cannot_escape(tmp_path):
    repo.git(tmp_path, 'init', '--initial-branch=0.2dev')
    repo.git(tmp_path, 'config', 'user.name', 'Isolated reviewer')
    repo.git(tmp_path, 'config', 'user.email', 'reviewer@example.test')
    name = '$(touch ESCAPED);--upload-pack.py'
    content = b'authoritative tracked content\n'
    (tmp_path / name).write_bytes(content)
    repo.git(tmp_path, 'add', '--', name)
    repo.git(tmp_path, 'commit', '-m', 'test: literal hostile artifact path')
    commit = repo.git(tmp_path, 'rev-parse', 'HEAD')
    digest = hashlib.sha256(content).hexdigest()
    repo.reviewed_files(tmp_path, commit, {name: digest})
    for unsafe in ('../outside.py', '/outside.py', 'nested\\outside.py'):
        with pytest.raises(Denied, match='relative repository paths'):
            repo.reviewed_files(tmp_path, commit, {unsafe: digest})
    assert not (tmp_path / 'ESCAPED').exists()


@pytest.mark.redteam
def test_foreign_development_branch_owner_blocks_integration_before_gates(board, project):
    board.board.review(board.task['id'], accepted())
    other = board.node.member('development-owner', parent=board.lead)
    board.session(other).claim('branch', '0.2dev')
    result = integrate(board, publish=True)
    assert result['state'] == 'blocked' and result['blocking_owner'] == other.name
    assert claims_of(board)[('branch', '0.2dev')] == other.name
    assert not project['log'].exists()
    assert repo.git(project['primary'], 'rev-parse', 'HEAD') == project['baseline']


def test_standard_nested_harness_worktrees_do_not_dirty_or_delete_primary(board, project):
    foreign = project['primary'] / '.claude' / 'worktrees' / 'foreign-agent'
    foreign.mkdir(parents=True)
    marker = foreign / 'work.txt'
    marker.write_text('active foreign work\n')
    repo.clean(project['primary'])
    assert marker.read_text() == 'active foreign work\n'
    unrelated = project['primary'] / '.claude' / 'unreviewed.py'
    unrelated.write_text('unreviewed = True\n')
    with pytest.raises(Denied, match='uncommitted changes'):
        repo.clean(project['primary'])
    assert marker.exists() and unrelated.exists()


def test_task_integrate_command_uses_the_callers_session_and_exact_task_id(board, project, monkeypatch):
    board.board.review(board.task['id'], accepted())
    result = integrate(board)
    assert result['state'] == 'completed'
    command = next(item for item in task_cli.TABLE if item[0] == 'task-integrate')
    monkeypatch.setattr(task_cli.limits, 'root', lambda: board.base)
    assert command[3](SimpleNamespace(id=board.task['id']), board.parent) == result


def test_the_mcp_integrate_tool_acts_as_the_callers_session_on_the_exact_task(board, project, monkeypatch):
    from poolhouse.workspace import tools
    board.board.review(board.task['id'], accepted())
    monkeypatch.setattr(tools.limits, 'root', lambda: board.base)
    monkeypatch.setattr(tools, '_session', lambda: board.reviewer)
    with pytest.raises(Denied):
        tools.workspace_task_integrate(board.task['id'])
    assert board.board.get(board.task['id'])['state'] == 'accepted'
    monkeypatch.setattr(tools, '_session', lambda: board.parent)
    assert tools.workspace_task_integrate(board.task['id'])['state'] == 'completed'
    assert board.board.get(board.task['id'])['state'] == 'completed'


@pytest.mark.parametrize('project', ['human_creator'], indirect=True)
def test_registered_parent_integrates_human_created_independently_reviewed_task(board, project):
    task = Tasks(board.person).get(board.task['id'])
    assert task['created_by'] != board.lead.name
    review = Tasks(board.person).review(board.task['id'], accepted())
    assert review['verifier'] != board.lead.name
    result = integrate(board, publish=True)
    assert result['state'] == 'published'
    assert repo.git(project['origin'], 'rev-parse', 'refs/heads/0.2dev') == result['commit']


@pytest.mark.redteam
@pytest.mark.parametrize('project', ['native_claims'], indirect=True)
def test_integration_returns_the_workers_checkout_and_branch_and_leaves_its_other_reservations(board, project, tmp_path):
    source, scope = project['source'], project['worktree']
    target = str(source / 'sim.py')
    before = claims_of(board)
    assert before[('worktree', str(source))] == board.worker_id and before[('branch', scope['branch'])] == board.worker_id
    assert before[('area', target)] == board.worker_id
    unrelated = str(tmp_path / 'unrelated-worker-file')
    board.child.claim('area', unrelated)
    board.board.review(board.task['id'], accepted())
    result = integrate(board, publish=True)
    assert result['state'] == 'published'
    after = claims_of(board)
    assert ('worktree', str(source)) not in after and ('branch', scope['branch']) not in after
    assert after[('area', unrelated)] == board.worker_id
    task = detail(board)
    returned = next(row for row in task['integration_events'] if row['state'] == 'scope_returned')
    assert returned['worker'] == board.worker_id and task['integrations'][0]['owner'] == board.lead.name


@pytest.mark.parametrize('project', ['native_claims'], indirect=True)
def test_integration_frees_the_workers_areas_even_when_its_checkout_and_branch_claims_are_already_gone(board, project, tmp_path):
    source, scope = project['source'], project['worktree']
    target = str(source / 'sim.py')
    shared = harness_claims.normal('area', target)
    before = claims_of(board)
    assert before[('area', target)] == board.worker_id and before[('area', shared)] == board.worker_id
    unrelated = str(tmp_path / 'unrelated-worker-file')
    board.child.claim('area', unrelated)
    board.child.release('worktree', str(source))
    board.child.release('branch', scope['branch'])
    board.board.review(board.task['id'], accepted())
    assert integrate(board, publish=True)['state'] == 'published'
    after = claims_of(board)
    assert ('area', target) not in after and ('area', shared) not in after
    assert after[('area', unrelated)] == board.worker_id


def test_the_board_shows_the_exact_published_review_and_a_once_only_attempt(board, project):
    review = board.board.review(board.task['id'], accepted())
    outcome = task_scheduler.integrate_completed(board.parent, board.base, board.worker_id)[0]
    assert outcome['state'] == 'completed'
    assert task_scheduler.integrate_completed(board.parent, board.base, board.worker_id) == []
    task = detail(board, board.person)
    integration = task['integrations'][0]
    assert integration['review_id'] == review['id'] and integration['review_hash'] == review['review_hash']
    assert integration['proposal_hash'] == task['proposal']['proposal_hash']
    assert integration['source_commit'] == project['proposal']['provenance']['commit']
    assert integration['commit'] == outcome['commit'] and len(integration['checks']) == 3
    assert all(check['passed'] and len(check['output_hash']) == 64 for check in integration['checks'])
    assert all(check['command'][0] == 'scripts/test' for check in integration['checks'])
    assert len(task['integration_attempts']) == 1
    assert task['integration_attempts'][0]['outcome']['state'] == 'completed'
    assert {event['state'] for event in task['integration_events']} == {
        'scope_returned', 'candidate', 'integrated', 'cleanup_required', 'completed'}
    assert 'candidate' not in integration and 'primary' not in json.dumps(task['integration_events'])


@pytest.mark.redteam
def test_a_blocked_integration_reaches_the_board_without_secrets_or_private_paths(board, project):
    board.board.review(board.task['id'], accepted())
    other = board.node.member('publication-owner', parent=board.lead)
    board.session(other).claim('branch', '0.2dev')
    outcome = task_scheduler.integrate_completed(board.parent, board.base, board.worker_id)[0]
    assert outcome['state'] == 'blocked'
    private = task_integration._load(board.base, 'integration:' + task_integration._digest(board.parent, board.board.get(board.task['id'])))
    shown = task_integration.public({**private, 'reason': f"token=very-private-value {board.base}/tokens/private.token unavailable"}, board.base)
    task = detail(board, board.person)
    assert task['integrations'][0]['blocking_owner'] == other.name
    exposed = json.dumps({key: task[key] for key in ('integrations', 'integration_attempts', 'integration_events')})
    assert 'candidate' not in exposed and 'interpreter' not in exposed and str(board.base) not in exposed
    assert 'very-private-value' not in json.dumps(shown) and 'private.token' not in json.dumps(shown)
    assert task_scheduler.integrate_completed(board.parent, board.base, board.worker_id) == []


def test_accepted_task_is_not_done_until_local_landing_and_cleanup(board, project):
    board.board.review(board.task['id'], accepted())
    assert board.board.get(board.task['id'])['state'] == 'accepted'
    result = integrate(board)
    assert result['state'] == 'completed' and result['cleanup_verified']
    assert not project['source'].exists()
    assert not Path(result['candidate']).exists()
    assert repo.git(project['origin'], 'rev-parse', 'refs/heads/0.2dev') == project['baseline']
    assert integrate(board) == result


def test_unique_ignored_files_keep_task_pending_until_preserved(board, project, tmp_path):
    board.board.review(board.task['id'], accepted())
    excluded = Path(repo.git(project['source'], 'rev-parse', '--git-path', 'info/exclude'))
    excluded.write_text('notes.private\n')
    notes = project['source'] / 'notes.private'
    notes.write_text('unique work')
    result = integrate(board)
    assert result['state'] == 'blocked' and 'requiring preservation' in result['reason']
    assert notes.read_text() == 'unique work'
    assert board.board.get(board.task['id'])['state'] == 'accepted'
    notes.rename(tmp_path / 'preserved.private')
    result = integrate(board)
    assert result['state'] == 'completed'
    assert not project['source'].exists()
    assert (tmp_path / 'preserved.private').read_text() == 'unique work'


def test_completed_task_refuses_recreated_branch_without_deleting_it(board, project):
    board.board.review(board.task['id'], accepted())
    result = integrate(board)
    assert result['state'] == 'completed'
    repo.git(project['primary'], 'branch', project['worktree']['branch'])
    with pytest.raises(Denied, match='branch reappeared'):
        integrate(board)
    assert repo.git(project['primary'], 'rev-parse', project['worktree']['branch']) == result['commit']


@pytest.mark.redteam
@pytest.mark.parametrize('staged', [False, True])
def test_lead_preserves_exact_reviewed_primary_bytes_before_fast_forward(board, project, staged, monkeypatch):
    board.board.review(board.task['id'], accepted())
    primary = project['primary']
    content = repo.git(project['source'], 'show', 'HEAD:sim.py', binary=True)
    (primary / 'sim.py').write_bytes(content)
    if staged:
        repo.git(primary, 'add', '--', 'sim.py')
    git = repo.git

    def read_only_primary(root, *arguments, **kwargs):
        assert root != primary or arguments[0] not in ('add', 'commit', 'restore', 'reset', 'checkout')
        return git(root, *arguments, **kwargs)

    monkeypatch.setattr(repo, 'git', read_only_primary)
    if not staged:
        index = primary / git(primary, 'rev-parse', '--git-path', 'index')
        before = index.read_bytes()
        with pytest.raises(Denied, match='already staged'):
            integrate(board)
        assert index.read_bytes() == before and (primary / 'sim.py').read_bytes() == content
        assert not project['log'].exists()
        return
    result = integrate(board)
    assert result['state'] == 'completed'
    assert (primary / 'sim.py').read_bytes() == content
    assert repo.git(primary, 'status', '--porcelain') == ''
    assert ('area', str(primary / 'sim.py')) not in claims_of(board)


@pytest.mark.redteam
@pytest.mark.parametrize('change', ['working', 'staged', 'untracked', 'child'])
def test_unreviewed_primary_bytes_and_child_staging_are_refused(board, project, change):
    board.board.review(board.task['id'], accepted())
    primary = project['primary']
    (primary / 'sim.py').write_text('speed = 2\n')
    who = board.parent
    if change == 'working':
        (primary / 'sim.py').write_text('speed = 99\n')
    elif change == 'staged':
        (primary / 'sim.py').write_text('speed = 99\n')
        repo.git(primary, 'add', '--', 'sim.py')
        (primary / 'sim.py').write_text('speed = 2\n')
    elif change == 'untracked':
        (primary / 'unknown.py').write_text('unknown = True\n')
    else:
        who = board.child
    before = repo.git(primary, 'diff', '--cached', binary=True)
    with pytest.raises(Denied):
        integrate(board, who=who)
    assert repo.git(primary, 'diff', '--cached', binary=True) == before
    assert repo.git(primary, 'rev-parse', 'HEAD') == project['baseline']
    assert not project['log'].exists()


@pytest.mark.redteam
def test_primary_preservation_verification_keeps_index_bytes(board, project):
    primary = project['primary']
    content = repo.git(project['source'], 'show', 'HEAD:sim.py', binary=True)
    (primary / 'sim.py').write_bytes(content)
    repo.git(primary, 'add', '--', 'sim.py')
    index = primary / repo.git(primary, 'rev-parse', '--git-path', 'index')
    before = index.read_bytes()
    names = integration_staging.files(primary, project['proposal']['provenance']['commit'])
    integration_staging.verify(primary, project['proposal']['provenance']['commit'], names)
    assert index.read_bytes() == before


@pytest.mark.redteam
def test_primary_preservation_refuses_symlink_bytes(board, project, monkeypatch):
    primary = project['primary']
    target = primary / 'sim.py'
    target.write_bytes(repo.git(project['source'], 'show', 'HEAD:sim.py', binary=True))
    repo.git(primary, 'add', '--', 'sim.py')
    is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == target or is_symlink(path))
    with pytest.raises(Denied, match='symlinks'):
        integration_staging.files(primary, project['proposal']['provenance']['commit'])


@pytest.mark.redteam
@pytest.mark.parametrize('contained', [False, True])
def test_unfinished_merge_metadata_requires_reviewed_commit_containment(board, project, contained):
    primary = project['primary']
    commit = project['proposal']['provenance']['commit']
    marker = primary / repo.git(primary, 'rev-parse', '--git-path', 'MERGE_HEAD')
    marker.write_text((commit if contained else '0' * 40) + '\n')
    (primary / 'sim.py').write_bytes(repo.git(project['source'], 'show', 'HEAD:sim.py', binary=True))
    repo.git(primary, 'add', '--', 'sim.py')
    if not contained:
        with pytest.raises((Denied, RuntimeError)):
            integration_staging.files(primary, commit)
        assert marker.exists()
        return
    names = integration_staging.files(primary, commit)
    index = primary / repo.git(primary, 'rev-parse', '--git-path', 'index')
    before = index.read_bytes()
    integration_staging.verify(primary, commit, names)
    assert marker.exists() and index.read_bytes() == before
    board.board.review(board.task['id'], accepted())
    result = integrate(board)
    assert result['state'] == 'completed' and not marker.exists()


@pytest.mark.redteam
def test_cleanup_refuses_a_foreign_worktree_lock(board, project):
    source = project['source']
    locked = source / repo.git(source, 'rev-parse', '--git-path', 'locked')
    repo.git(project['primary'], 'worktree', 'lock', '--reason', 'foreign live operation', str(source))
    board.board.review(board.task['id'], accepted())
    result = integrate(board)
    assert result['state'] == 'blocked' and 'lock owned by another operation' in result['reason']
    assert source.exists() and locked.read_text().strip() == 'foreign live operation'

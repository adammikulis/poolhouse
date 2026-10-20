"""Native hook ownership rejects competing mutations before their filesystem effects."""

import concurrent.futures
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from harness_kit import held, hook, verdict
from node_kit import node_binary, workspace_node  # noqa: F401

from poolhouse import coding_policy, harness_claims, harnesshook
from poolhouse.board import place, session as board_session
from poolhouse.errors import Conflict, Denied
from poolhouse.net import git
from poolhouse.workspace import harness_session, limits, worktree_lifecycle

pytestmark = pytest.mark.redteam


@pytest.fixture
def kit(workspace_node, tmp_path):  # noqa: F811
    lead = workspace_node.member('lead')
    kit = SimpleNamespace(node=workspace_node, project=tmp_path / 'project', tmp=tmp_path)
    kit.project.mkdir()
    kit.alpha, kit.beta = (workspace_node.member(name, parent=lead) for name in ('alpha', 'beta'))
    kit.a, kit.b = workspace_node.session(kit.alpha), workspace_node.session(kit.beta)
    return kit


def reserve(kit, who, name, args, roots=None):
    return harness_claims.reserve(name, args, str(kit.project), who.name, roots or [str(kit.project)])


def test_actual_hook_blocks_other_owner_and_symlink_alias_before_write(kit):
    target = kit.project / 'result.txt'
    alias = kit.project / 'alias'
    alias.symlink_to(kit.project, target_is_directory=True)
    reserve(kit, kit.beta, 'Write', {'file_path': str(target), 'content': 'mine'})
    payload = {'tool_name': 'Write', 'tool_input': {'file_path': str(alias / target.name), 'content': 'hostile'},
               'cwd': str(kit.project)}
    done = hook(kit.node, 'pre', payload, '--permissions', 'ask-dangerous', '--agent', kit.alpha.name, '--root', str(kit.project))
    assert done.returncode == 0, done.stderr
    decision = verdict(done)
    if decision['permissionDecision'] == 'allow':
        target.write_text('hostile')
    assert decision['permissionDecision'] == 'deny' and kit.beta.name in decision['permissionDecisionReason']
    assert not target.exists()


def test_atomic_multi_resource_conflict_does_not_leave_partial_claims(kit):
    kit.b.claim('port', '8080')
    with pytest.raises(Conflict):
        kit.a.reserve([('area', str(kit.project / 'untouched')), ('port', '8080')])
    assert held(kit.a, kit.alpha.name) == []

    def take(session):
        try:
            session.reserve([('area', str(kit.project / 'shared'))])
            return session.name
        except Conflict:
            return None
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        assert len([value for value in pool.map(take, [kit.a, kit.b]) if value]) == 1


def test_launcher_identity_and_root_cannot_be_overridden_by_call(kit):
    target = kit.project / 'ok.txt'
    args = {'file_path': str(target), 'actor': kit.beta.name, 'roots': ['/']}
    reserve(kit, kit.alpha, 'Write', args)
    assert held(kit.a, kit.alpha.name) == [('area', str(target))]
    with pytest.raises(Denied, match='outside the launcher-approved'):
        reserve(kit, kit.alpha, 'Write', {'file_path': str(kit.project.parent / 'outside')})
    assert held(kit.a, kit.beta.name) == []


def test_a_token_file_of_another_session_is_not_the_launcher_identity(kit):
    folder = kit.node.state / 'client' / kit.node.board
    (folder / f'{kit.alpha.name}.token').write_text(kit.beta.token + '\n')
    with pytest.raises(Denied, match='launcher-bound identity'):
        reserve(kit, kit.alpha, 'Write', {'file_path': str(kit.project / 'forged.txt')})
    assert held(kit.b, kit.beta.name) == [] and held(kit.b, kit.alpha.name) == []


def test_a_session_the_board_refuses_is_denied_and_never_waved_through(kit):
    kit.a.retire()
    payload = {'tool_name': 'Write', 'tool_input': {'file_path': str(kit.project / 'retired.txt')}, 'cwd': str(kit.project)}
    answer = harnesshook.pre(payload, harnesshook.Rail(coding_policy.Policy('ask-dangerous'), kit.alpha.name, roots=[str(kit.project)], wait_s=0))
    decision = answer['hookSpecificOutput']
    assert decision['permissionDecision'] == 'deny' and 'refused the token' in decision['permissionDecisionReason']


def test_a_claim_taken_between_the_check_and_the_reservation_denies_the_call(kit, monkeypatch):
    target = kit.project / 'raced.txt'
    monkeypatch.setattr(harness_claims, 'conflict', lambda *args, **kwargs: '')
    reserve(kit, kit.beta, 'Write', {'file_path': str(target)})
    payload = {'tool_name': 'Write', 'tool_input': {'file_path': str(target), 'content': 'the second session writes this file\n'},
               'cwd': str(kit.project)}
    answer = harnesshook.pre(payload, harnesshook.Rail(coding_policy.Policy('ask-dangerous'), kit.alpha.name, roots=[str(kit.project)], wait_s=0))
    decision = answer['hookSpecificOutput']
    assert decision['permissionDecision'] == 'deny' and kit.beta.name in decision['permissionDecisionReason']
    assert held(kit.a, kit.alpha.name) == []


def test_a_refused_reservation_keeps_what_the_session_held_and_takes_nothing_new(kit):
    old, fresh, last = (str(kit.project / name) for name in ('a-old.py', 'b-fresh.py', 'c-last.py'))
    kit.a.reserve([('area', old)])
    kit.b.claim('area', last)
    with pytest.raises(Conflict):
        kit.a.reserve([('area', old), ('area', fresh), ('area', last)])
    assert held(kit.a, kit.alpha.name) == [('area', old)]
    assert held(kit.a, kit.beta.name) == [('area', last)]


def test_physical_claims_conflict_across_boards(kit, monkeypatch):
    other = board_session.register(kit.node.client, 'second', board_session.Native('claude-sonnet-5-5', 'claude-code', 'elsewhere'))
    target = kit.project / 'source.py'
    reserve(kit, kit.alpha, 'Write', {'file_path': str(target)})
    monkeypatch.setenv('POOLHOUSE_BOARD', 'second')
    with pytest.raises(Conflict):
        harness_claims.reserve('Write', {'file_path': str(target)}, str(kit.project), other.name, [str(kit.project)])
    assert held(board_session.Session(kit.node.client, 'second', other.token, other.name), other.name) == []


def test_an_area_held_by_another_session_blocks_a_file_under_it(kit):
    kit.b.claim('area', str(kit.project))
    with pytest.raises(Conflict):
        reserve(kit, kit.alpha, 'Write', {'file_path': str(kit.project / 'source.py')})
    assert harness_claims.conflict('Write', {'file_path': str(kit.project / 'source.py')}, str(kit.project),
                                   kit.alpha.name, [str(kit.project)]) == f'file {kit.project.resolve() / "source.py"} belongs to {kit.beta.name}'


def test_a_session_without_a_token_of_its_own_reserves_nothing(kit):
    (kit.node.state / 'client' / kit.node.board / f'{kit.alpha.name}.token').unlink()
    with pytest.raises(Denied, match=kit.alpha.name) as refused:
        reserve(kit, kit.alpha, 'Write', {'file_path': str(kit.project / 'source.py')})
    assert not isinstance(refused.value, harness_session.BoardUnavailable), 'a missing token is a refusal, not a board outage'
    assert held(kit.b, kit.beta.name) == [] and held(kit.b, kit.alpha.name) == []


def test_the_hook_denies_a_session_with_no_token_file_and_does_not_wave_it_through_with_a_note(kit):
    (kit.node.state / 'client' / kit.node.board / f'{kit.alpha.name}.token').unlink()
    target = kit.project / 'source.py'
    payload = {'tool_name': 'Write', 'tool_input': {'file_path': str(target), 'content': 'x\n'}, 'cwd': str(kit.project)}
    answer = harnesshook.pre(payload, harnesshook.Rail(coding_policy.Policy('ask-dangerous'), kit.alpha.name, roots=[str(kit.project)], wait_s=0))['hookSpecificOutput']
    assert answer['permissionDecision'] == 'deny' and 'no usable token' in answer['permissionDecisionReason']
    assert 'ownership not' not in answer['permissionDecisionReason']
    assert held(kit.b, kit.beta.name) == [] and held(kit.b, kit.alpha.name) == []


def test_the_board_of_the_launcher_approved_project_is_the_boards_of_the_session(kit, monkeypatch, tmp_path):
    monkeypatch.delenv('POOLHOUSE_BOARD')
    mine, theirs = (tmp_path / name for name in ('mine', 'theirs'))
    for folder in (mine, theirs):
        git.run(['init', '-b', 'development', str(folder)])
    board = place.resolve(kit.node.client, mine)
    assert place.resolve(kit.node.client, theirs) != board
    member = board_session.register(kit.node.client, board, board_session.Native('claude-sonnet-5-5', 'claude-code', 'mine'))
    harness_claims.reserve('Write', {'file_path': str(mine / 'a.py')}, str(mine), member.name, [str(mine)])
    with pytest.raises(Denied, match='board of the launcher-approved project'):
        harness_claims.reserve('Write', {'file_path': str(theirs / 'a.py')}, str(mine), member.name, [str(mine), str(theirs)])
    assert held(board_session.Session(kit.node.client, board, member.token, member.name), member.name) == sorted([
        ('area', str(mine.resolve() / 'a.py')), ('area', harness_claims.normal('area', str(mine / 'a.py')))])


@pytest.mark.parametrize('command', ['echo x > "$TARGET"', 'touch *.py', 'touch $(pwd)/x'])
def test_shell_targets_that_are_not_fixed_claim_the_whole_checkout(kit, command):
    assert ('worktree', str(kit.project)) in harness_claims.resources('Bash', {'command': command}, str(kit.project))


def test_explicit_port_worktree_and_install_mutations_reserve_native_resources(kit, monkeypatch):
    python = kit.project / 'venv' / 'bin' / 'python'
    which = harness_claims.shutil.which
    monkeypatch.setattr(harness_claims.shutil, 'which', lambda name: str(python) if name == 'python' else which(name))
    command = f'git -C {kit.project} add result.txt; python -m pip install wheel; poolhouse-serve down --port 51548'
    required = harness_claims.resources('Bash', {'command': command}, str(kit.project))
    assert ('worktree', str(kit.project)) in required
    assert ('install', str(python.parent.parent)) in required
    assert ('port', '51548') in required
    reserve(kit, kit.alpha, 'Bash', {'command': command})
    decision = harnesshook.pre({'tool_name': 'Bash', 'tool_input': {'command': command}, 'cwd': str(kit.project)},
                               harnesshook.Rail(coding_policy.Policy('ask-dangerous'), kit.beta.name, roots=[str(kit.project)], wait_s=0))
    assert decision['hookSpecificOutput']['permissionDecision'] == 'deny'
    owners = {(c.kind, c.key): c.owner for c in kit.b.claims()}
    assert owners[('install', str(python.parent.parent))] == kit.alpha.name
    assert owners[('port', '51548')] == kit.alpha.name and owners[('worktree', str(kit.project))] == kit.alpha.name


def initialised(project):
    git.run(['init', '-b', 'development', str(project)])
    (project / 'shared.py').write_text('original\n')
    git.run(['add', 'shared.py'], cwd=project)
    git.run(['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-m', 'fixture'], cwd=project)


def test_separate_worktrees_share_source_area_but_allow_independent_files(kit, tmp_path):
    initialised(kit.project)
    other = tmp_path / 'other-worktree'
    git.run(['worktree', 'add', '-b', 'independent', str(other)], cwd=kit.project)
    reserve(kit, kit.alpha, 'Write', {'file_path': str(kit.project / 'shared.py')})
    with pytest.raises(Conflict):
        harness_claims.reserve('Write', {'file_path': str(other / 'shared.py')}, str(other), kit.beta.name, [str(other)])
    harness_claims.reserve('Write', {'file_path': str(other / 'independent.py')}, str(other), kit.beta.name, [str(other)])
    shared = harness_claims.normal('area', str(other / 'shared.py'))
    assert shared == harness_claims.normal('area', str(kit.project / 'shared.py'))
    assert not Path(shared).is_relative_to(kit.project.resolve()) and not Path(shared).is_relative_to(other.resolve())
    owner = next(c for c in kit.b.claims() if (c.kind, c.key) == ('area', shared))
    assert owner.owner == kit.alpha.name and owner.pid == os.getppid() and owner.expires_in_s > 0
    assert held(kit.b, kit.beta.name) == sorted([('area', str(other.resolve() / 'independent.py')),
                                                 ('area', harness_claims.normal('area', str(other / 'independent.py')))])


def test_a_workers_edit_in_a_linked_worktree_does_not_refuse_the_lead_working_in_the_primary(kit, tmp_path):
    initialised(kit.project)
    other = tmp_path / 'other-worktree'
    git.run(['worktree', 'add', '-b', 'worker/elsewhere', str(other)], cwd=kit.project)
    harness_claims.reserve('Write', {'file_path': str(other / 'shared.py')}, str(other), kit.beta.name, [str(other)])
    whole_checkout = {'command': 'touch *.py'}
    assert ('worktree', str(kit.project)) in harness_claims.resources('Bash', whole_checkout, str(kit.project))
    assert harness_claims.conflict('Bash', whole_checkout, str(kit.project), kit.alpha.name, [str(kit.project)]) == ''
    harness_claims.reserve('Write', {'file_path': str(kit.project / 'mine.py')}, str(kit.project), kit.beta.name, [str(kit.project)])
    assert kit.beta.name in harness_claims.conflict('Bash', whole_checkout, str(kit.project), kit.alpha.name, [str(kit.project)])


def test_a_native_mutation_records_the_checkout_it_changes_for_the_session(kit, tmp_path):
    initialised(kit.project)
    other = tmp_path / 'other-worktree'
    git.run(['worktree', 'add', '-b', 'worker/other', str(other)], cwd=kit.project)
    reserve(kit, kit.alpha, 'Write', {'file_path': str(other / 'new.py')}, [str(kit.project), str(other)])
    assert [row['path'] for row in worktree_lifecycle.scopes(limits.root(), kit.alpha.name)] == [str(other.resolve())]


@pytest.mark.parametrize('command', [
    "ls src/poolhouse/models* 2>/dev/null; find src -name '*model*' -maxdepth 3 | head",
    "cat HANDOFF.md 2>/dev/null | head",
    "ls 1>/dev/null 2>>/dev/null",
    "ls &>/dev/null",
])
def test_native_read_inventory_null_sink_needs_no_mutation_claim(kit, command):
    assert harness_claims.resources('Bash', {'command': command}, str(kit.project)) == []
    decision = harnesshook.pre({'tool_name': 'Bash', 'tool_input': {'command': command},
                               'cwd': str(kit.project)},
                              harnesshook.Rail(coding_policy.Policy('ask-dangerous'), kit.alpha.name, roots=[str(kit.project)], wait_s=0))
    assert decision['hookSpecificOutput']['permissionDecision'] == 'allow'
    assert held(kit.a, kit.alpha.name) == []


@pytest.mark.parametrize('target', ['/tmp/outside-report', '/dev/zero', '/dev/tty',
                                   '/dev/stdout', '/dev/fd/1', '/proc/self/fd/1'])
def test_other_outside_redirect_targets_remain_denied(tmp_path, target):
    command = f'ls 2>{target}'
    with pytest.raises(Denied, match='outside the launcher-approved'):
        harness_claims.reserve('Bash', {'command': command}, str(tmp_path), 'alpha', [str(tmp_path)])


def test_aliases_and_replaced_null_sink_are_not_exempt(tmp_path, monkeypatch):
    alias = tmp_path / 'null-alias'
    alias.symlink_to(os.devnull)
    assert not harness_claims._null_sink(str(alias))
    replacement = tmp_path / 'null-file'
    replacement.write_text('preserve')
    with monkeypatch.context() as patch:
        patch.setattr(os, 'devnull', str(replacement))
        assert not harness_claims._null_sink(str(replacement))
        patch.setattr(os, 'devnull', str(alias))
        assert not harness_claims._null_sink(str(alias))
    assert replacement.read_text() == 'preserve'


def test_windows_null_sink_is_the_exact_reserved_os_device(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(os, 'name', 'nt')
        patch.setattr(os, 'devnull', 'nul')
        assert harness_claims._null_sink('nul')
        assert not harness_claims._null_sink('NUL.txt')
        assert not harness_claims._null_sink('C:/nul')
        assert not harness_claims._null_sink('/dev/null')

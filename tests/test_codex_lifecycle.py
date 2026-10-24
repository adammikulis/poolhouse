"""Codex lifecycle hooks use native identities on a real scratch node."""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from node_kit import node_binary, workspace_node  # noqa: F401

from poolhouse import node_health
from poolhouse.board import session
from poolhouse.workspace import codex_hooks as lifecycle, codex_native

IDS = {name: str(uuid.uuid4()) for name in ("native-root", "native-child", "native-nested", "child", "new-child", "unregistered-child")}


def event(stage, identifier="native-root", **fields):
    fields = {key: IDS.get(value, value) for key, value in fields.items()}
    return dict(hook_event_name=stage, session_id=IDS.get(identifier, identifier), model="gpt-6.1-sol", **fields)


def header(home, identifier, parent, **extra):
    today = datetime.datetime.now(datetime.UTC).date()
    folder = home / "sessions" / f"{today.year:04d}" / f"{today.month:02d}" / f"{today.day:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-test-{identifier}.jsonl"
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": identifier,
                    "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent}}}, **extra}}) + "\n")
    return path


@pytest.fixture
def node(workspace_node, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("CODEX_THREAD_ID", IDS["native-root"])
    monkeypatch.setenv("POOLHOUSE_BOARD", workspace_node.board)
    monkeypatch.setenv("POOLHOUSE_HOME", str(workspace_node.state.parent))
    native_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(native_home))
    header(native_home, IDS["native-child"], IDS["native-root"])
    header(native_home, IDS["native-nested"], IDS["native-child"])
    header(native_home, IDS["child"], IDS["native-root"])
    return workspace_node


def test_root_child_nested_are_separate_authenticated_sessions(node):
    root_context = lifecycle.handle(event("SessionStart"), node=node.client)
    root_name = session.find("codex", IDS["native-root"], client=node.client)
    child_context = lifecycle.handle(event("SubagentStart", agent_id="native-child", parent_agent_id="native-root"), node=node.client)
    child_name = session.find("codex", IDS["native-child"], client=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-nested", parent_agent_id="native-child"), node=node.client)
    nested_name = session.find("codex", IDS["native-nested"], client=node.client)
    root = node.session(node.adopt(root_name))
    child = node.session(node.adopt(child_name))
    nested = node.session(node.adopt(nested_name))
    assert root.name != child.name != nested.name
    assert child.whoami().parent == root.name
    assert nested.whoami().parent == child.name
    assert child.name in child_context["hookSpecificOutput"]["additionalContext"]
    assert "inherited" in child_context["hookSpecificOutput"]["additionalContext"]
    assert root.name in root_context["hookSpecificOutput"]["additionalContext"]
    assert "token" not in json.dumps(child_context)


@pytest.mark.parametrize("fields", [{}, {"agent_id": "native-root"}, {"agent_id": "new-child"},
                                     {"agent_id": "new-child", "parent_agent_id": "new-child"}])
def test_missing_ambiguous_or_self_parentage_never_falls_back(node, fields):
    lifecycle.handle(event("SessionStart"), node=node.client)
    with pytest.raises(ValueError):
        lifecycle.handle(event("SubagentStart", **fields), node=node.client)
    assert session.find("codex", IDS["new-child"], client=node.client) == ""


def test_child_turn_stop_keeps_authenticated_session_for_followup(node):
    lifecycle.handle(event("SessionStart"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="child", parent_agent_id="native-root"), node=node.client)
    child_name = session.find("codex", IDS["child"], client=node.client)
    child = node.session(node.adopt(child_name))
    before = child.whoami()
    for _ in range(2):
        lifecycle.handle(event("SubagentStop", agent_id="child", turn_id="turn-one"), node=node.client)
    assert child.whoami() == before
    assert lifecycle.handle(event("PostToolUse", agent_id="child"), node=node.client) == {}
    child.post("#general", "note", "followup uses the same authenticated session")


def test_explicit_retirement_still_refuses_ended_child_identity(node):
    lifecycle.handle(event("SessionStart"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="child", parent_agent_id="native-root"), node=node.client)
    child_name = session.find("codex", IDS["child"], client=node.client)
    node.session(node.adopt(child_name)).retire()
    with pytest.raises(Exception, match=r"ended|retired|session"):
        lifecycle.handle(event("SubagentStart", agent_id="child", parent_agent_id="native-root"), node=node.client)
    name = session.find("codex", IDS["native-root"], client=node.client)
    assert not node.session(node.adopt(name)).whoami().retired


def test_root_session_end_retires_root_and_stopped_children(node):
    lifecycle.handle(event("SessionStart"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="child", parent_agent_id="native-root"), node=node.client)
    names = [session.find("codex", IDS[key], client=node.client) for key in ("native-root", "child")]
    lifecycle.handle(event("SubagentStop", agent_id="child"), node=node.client)
    lifecycle.handle(event("SessionEnd"), node=node.client)
    for name in names:
        with pytest.raises(Exception, match=r"token|ended|retired|session"):
            node.session(node.adopt(name)).whoami()


def test_assigned_launcher_identity_is_not_replaced_and_child_not_parent(node):
    assigned = node.member(IDS["native-root"], harness="codex", model="gpt-6.1-sol")
    result = lifecycle.handle(event("SessionStart"), assigned.name, node=node.client)
    assert assigned.name in result["hookSpecificOutput"]["additionalContext"]
    assert session.find("codex", IDS["native-root"], client=node.client) == assigned.name
    lifecycle.handle(event("SubagentStart", agent_id="child", parent_agent_id="native-root"), assigned.name, node=node.client)
    lifecycle.handle(event("SubagentStop", agent_id="child"), assigned.name, node=node.client)
    assert not node.session(assigned).whoami().retired


def test_assigned_identity_must_match_the_registered_native_thread(node):
    lifecycle.handle(event("SessionStart"), node=node.client)
    unrelated = node.member("unrelated-launcher", harness="codex", model="gpt-6.1-sol")
    with pytest.raises(ValueError, match="does not own"):
        lifecycle.handle(event("SessionStart"), unrelated.name, node=node.client)
    assert not node.session(unrelated).whoami().retired


def test_unregistered_child_does_not_use_assigned_or_inherited_parent(node, monkeypatch):
    assigned = node.member("assigned", harness="codex")
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", assigned.name)
    with pytest.raises(Exception, match=r"native|session"):
        lifecycle.handle(event("PostToolUse", agent_id="unregistered-child"), assigned.name, node=node.client)


@pytest.mark.parametrize("completion", [False, True])
def test_stopped_daemon_warns_quickly_and_does_not_block_or_restart(node, completion):
    lifecycle.handle(event("SessionStart"), node=node.client)
    node.stop()
    before = time.monotonic()
    done = subprocess.run([sys.executable, "-m", "poolhouse.workspace.cli", "codex-stop" if completion else "codex-hook"],
                          input=json.dumps(event("Stop" if completion else "PostToolUse")), text=True, capture_output=True,
                          env=node.env(extra={"CODEX_THREAD_ID": IDS["native-root"]}), timeout=5, check=False)
    assert done.returncode == 0 and time.monotonic() - before < 5
    assert ("workspace lifecycle:" in done.stderr) != completion
    assert not done.stdout
    assert node_health.node_health(node.state) is None


def test_warning_redacts_credentials_and_malformed_event_is_advisory(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key-for-test")
    lifecycle.warning("failed secret-key-for-test")
    assert "secret-key-for-test" not in capsys.readouterr().err
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("[]"))
    assert lifecycle.run(argparse.Namespace(assigned="", completion=False, worker=False)) == 0
    assert "must be an object" in capsys.readouterr().err


def test_documented_child_event_uses_actual_header_parent(node):
    lifecycle.handle(event("SessionStart"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-child"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-nested"), node=node.client)
    parent_name = session.find("codex", IDS["native-child"], client=node.client)
    nested_name = session.find("codex", IDS["native-nested"], client=node.client)
    assert node.session(node.adopt(nested_name)).whoami().parent == parent_name


def test_spoofed_parent_field_and_native_header_id_are_refused(node):
    lifecycle.handle(event("SessionStart"), node=node.client)
    with pytest.raises(ValueError, match="conflicts"):
        lifecycle.handle(event("SubagentStart", agent_id="native-child", parent_agent_id="native-nested"), node=node.client)
    home = Path(__import__("os").environ["CODEX_HOME"])
    header(home, IDS["native-child"], IDS["native-root"], id=IDS["native-nested"])
    with pytest.raises(ValueError, match="does not match"):
        codex_native.parent(event("SubagentStart", agent_id="native-child"))


def test_native_header_symlink_or_other_writable_file_is_refused(node, tmp_path):
    home = Path(__import__("os").environ["CODEX_HOME"])
    path = header(home, IDS["native-child"], IDS["native-root"])
    path.chmod(0o666)
    with pytest.raises(ValueError, match="owned regular"):
        codex_native.parent(event("SubagentStart", agent_id="native-child"))
    path.unlink()
    target = tmp_path / "outside-header"
    target.write_text("{}")
    path.symlink_to(target)
    with pytest.raises(ValueError, match="owned regular"):
        codex_native.parent(event("SubagentStart", agent_id="native-child"))


def test_child_notification_without_agent_id_uses_actual_child(node, monkeypatch):
    lifecycle.handle(event("SessionStart"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-child"), node=node.client)
    child_name = session.find("codex", IDS["native-child"], client=node.client)
    monkeypatch.setenv("CODEX_THREAD_ID", IDS["native-child"])
    root_name = session.find("codex", IDS["native-root"], client=node.client)
    result = lifecycle.handle(event("PostToolUse"), root_name, node=node.client)
    assert root_name not in json.dumps(result)
    if result:
        assert child_name in result["hookSpecificOutput"]["additionalContext"]
    lifecycle.handle(event("SessionEnd"), root_name, node=node.client)
    assert not node.session(node.adopt(root_name)).whoami().retired
    retired = {actor.name: actor.retired for actor in node.session(node.adopt(root_name)).agents(retired=True)}
    assert retired[child_name]


def test_ambiguous_notification_and_assigned_thread_disagreement_are_refused(node, monkeypatch):
    assigned = node.member("assigned", harness="codex")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    with pytest.raises(ValueError, match="ambiguous"):
        lifecycle.handle(event("PostToolUse"), assigned.name, node=node.client)
    with pytest.raises(ValueError, match="actual native root"):
        lifecycle.handle(event("SessionStart"), assigned.name, node=node.client)


def test_nested_ambient_notification_proves_native_parent_chain(node, monkeypatch):
    lifecycle.handle(event("SessionStart"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-child"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-nested"), node=node.client)
    monkeypatch.setenv("CODEX_THREAD_ID", IDS["native-nested"])
    lifecycle.handle(event("PostToolUse"), node=node.client)


def test_cached_stop_does_not_run_cleanup_admission_for_own_child(node, monkeypatch):
    lifecycle.handle(event("SessionStart"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-child"), node=node.client)
    root_name = session.find("codex", IDS["native-root"], client=node.client)
    child_name = session.find("codex", IDS["native-child"], client=node.client)
    monkeypatch.setenv("CODEX_THREAD_ID", IDS["native-child"])
    checked = []
    monkeypatch.setattr(lifecycle.worktree_lifecycle, "require_clean", lambda base, owner, **kw: checked.append(owner))
    assert lifecycle.completion(event("Stop"), root_name, node=node.client) == {}
    assert checked == []
    assert child_name != root_name


def test_malformed_completion_payload_is_advisory(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("[]"))
    assert lifecycle.run(argparse.Namespace(assigned="", completion=True, worker=True)) == 0
    output = capsys.readouterr()
    assert not output.out and 'must be an object' in output.err


@pytest.mark.parametrize("completion,stage", [(False, 'PostToolUse'), (True, 'Stop'), (False, 'SessionEnd')])
def test_hostile_worker_arguments_are_single_argv_values_with_deadline(monkeypatch, capsys, completion, stage):
    hostile = 'own;$(touch attacker-marker) --worker'
    payload = json.dumps(event(stage, cwd='/tmp/hostile;$(touch other-marker)'))
    captured = {}

    def timed_out(command, **kwargs):
        captured.update(command=command, **kwargs)
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])

    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(payload))
    monkeypatch.setattr(lifecycle.subprocess, "run", timed_out)
    assert lifecycle.run(argparse.Namespace(assigned=hostile, completion=completion, worker=False)) == 0
    assert captured['command'] == [sys.executable, '-m', 'poolhouse.workspace.cli',
                                   'codex-stop' if completion else 'codex-hook', '--worker', '--assigned', hostile]
    assert captured['input'] == payload
    assert captured['timeout'] == (2.0 if stage == 'SessionEnd' else lifecycle.WAIT)
    assert not captured.get('shell', False)
    output = capsys.readouterr()
    assert not output.out and 'workspace lifecycle:' in output.err


def test_session_end_timeout_reaps_its_owned_worker(monkeypatch, capsys):
    actual_run = subprocess.run
    stopped = []

    def sleeping_worker(command, **kwargs):
        try:
            return actual_run([sys.executable, '-c',
                               'import os,time;print(os.getpid(),flush=True);time.sleep(60)'], **kwargs)
        except subprocess.TimeoutExpired as error:
            stopped.append(int(error.stdout.strip()))
            raise

    monkeypatch.setattr(sys, 'stdin', __import__('io').StringIO(json.dumps(event('SessionEnd'))))
    monkeypatch.setattr(lifecycle.subprocess, 'run', sleeping_worker)
    assert lifecycle.run(argparse.Namespace(assigned='', completion=False, worker=False)) == 0
    assert len(stopped) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(stopped[0], 0)
    output = capsys.readouterr()
    assert not output.out and 'workspace lifecycle:' in output.err


@pytest.mark.parametrize('active', [False, True])
def test_ambiguous_stop_warns_without_a_parent_or_completion_decision(node, monkeypatch, capsys, active):
    lifecycle.handle(event('SessionStart'), node=node.client)
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    checked = []
    monkeypatch.setattr(lifecycle.worktree_lifecycle, 'require_clean', lambda *a, **k: checked.append(1))
    monkeypatch.setattr(sys, 'stdin', __import__('io').StringIO(json.dumps(event('Stop', stop_hook_active=active))))
    assert lifecycle.run(argparse.Namespace(assigned='', completion=True, worker=True)) == 0
    output = capsys.readouterr()
    assert not output.out and not output.err
    assert checked == []


@pytest.mark.parametrize('active', [False, True])
@pytest.mark.parametrize('child', [False, True])
def test_cached_stop_does_not_check_pending_cleanup_even_an_active_stop(node, monkeypatch, capsys, active, child):
    lifecycle.handle(event('SessionStart'), node=node.client)
    if child:
        lifecycle.handle(event('SubagentStart', agent_id='native-child'), node=node.client)
        monkeypatch.setenv('CODEX_THREAD_ID', IDS['native-child'])
    expected = session.find('codex', IDS['native-child' if child else 'native-root'], client=node.client)
    checked = []
    def pending(base, owner):
        checked.append(owner)
        return [{'path': str(Path.cwd()), 'branch': 'worker/change', 'reasons': ['checkout remains']}]
    monkeypatch.setattr(lifecycle.worktree_lifecycle, 'pending', pending)
    monkeypatch.setattr(sys, 'stdin', __import__('io').StringIO(json.dumps(event('Stop', cwd=str(Path.cwd()), stop_hook_active=active))))
    assert lifecycle.run(argparse.Namespace(assigned='', completion=True, worker=True)) == 0
    output = capsys.readouterr()
    assert not output.out and not output.err and checked == []
    assert expected


def test_untyped_cleanup_transport_denial_does_not_invent_pending_work(node, monkeypatch, capsys):
    from poolhouse.errors import Denied
    lifecycle.handle(event('SessionStart'), node=node.client)
    def unavailable(*args, **kwargs):
        raise Denied('unavailable')
    monkeypatch.setattr(lifecycle.worktree_lifecycle, 'pending', unavailable)
    monkeypatch.setattr(sys, 'stdin', __import__('io').StringIO(json.dumps(event('Stop'))))
    assert lifecycle.run(argparse.Namespace(assigned='', completion=True, worker=True)) == 0
    output = capsys.readouterr()
    assert not output.out and not output.err


def test_completion_worker_exit_warns_without_block(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'stdin', __import__('io').StringIO(json.dumps(event('Stop'))))
    monkeypatch.setattr(lifecycle.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a[0], 3, '', ''))
    assert lifecycle.run(argparse.Namespace(assigned='', completion=True, worker=False)) == 0
    output = capsys.readouterr()
    assert not output.out and 'completion worker exited 3' in output.err


def test_cached_stop_needs_no_project_or_native_identity(monkeypatch, capsys):
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    monkeypatch.setattr(lifecycle, 'context', lambda *a, **k: pytest.fail('automatic Stop requested workspace admission'))
    monkeypatch.setattr(sys, 'stdin', __import__('io').StringIO(json.dumps({'hook_event_name': 'Stop', 'cwd': '/no-project'})))
    assert lifecycle.run(argparse.Namespace(assigned='', completion=True, worker=True)) == 0
    assert capsys.readouterr() == ('', '')

"""What an agent's other tools (read, search, MCP, task control) and a subagent's shell may not reach."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse import protectedwrites, worktreerules

GUARD = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "claude-tool-guard"
OWN = "claude-aaaaaa"


def other(command: str) -> str:
    return protectedwrites.other_agent(command, OWN)


@pytest.mark.parametrize("command", [
    "poolhouse-workspace send bob note hi --agent claude-aaaaaa",
    "poolhouse-workspace --agent=claude-aaaaaa inbox",
    "POOLHOUSE_WORKSPACE_AGENT=claude-aaaaaa poolhouse runtime ensure",
    "poolhouse-workspace inbox",
    "poolhouse runtime status",
    "git status",
])
def test_a_subagent_may_act_as_itself(command):
    assert not other(command), command


@pytest.mark.parametrize("command", [
    "poolhouse-workspace inbox --agent agent-lead01",
    "poolhouse-workspace --agent=agent-lead01 inbox",
    "poolhouse-workspace inbox --agent $LEAD",
    "poolhouse-workspace inbox --token-file /tmp/lead.token",
    "POOLHOUSE_WORKSPACE_AGENT=agent-lead01 poolhouse runtime ensure",
    "export POOLHOUSE_WORKSPACE_AGENT=agent-lead01; poolhouse runtime ensure",
    "env POOLHOUSE_WORKSPACE_TOKEN=abc poolhouse-workspace inbox",
    "poolhouse runtime ensure --agent agent-lead01",
    "bash -c 'poolhouse-workspace inbox --agent agent-lead01'",
    "cd /tmp && poolhouse-workspace inbox --agent agent-lead01",
    "echo $(poolhouse-workspace inbox --agent agent-lead01)",
])
def test_a_subagent_may_not_act_as_another_session(command):
    assert other(command) and OWN in other(command), command


def tool(name: str, given: dict, **more) -> str:
    return protectedwrites.tool_refusal(name, given, **{"cwd": "/work", "environ": {"HOME": "/home/p"}, **more})


@pytest.mark.parametrize("name, given", [
    ("Read", {"file_path": "/home/p/.poolhouse/person/statements.log.key"}),
    ("Read", {"file_path": "~/.poolhouse/node/client/board/agent-aaaaaa.token"}),
    ("Read", {"file_path": "/home/p/.poolhouse/node/person/key"}),
    ("Grep", {"pattern": "token", "path": "/home/p/.poolhouse/node/client"}),
    ("Grep", {"pattern": "key", "path": "/home/p/.poolhouse/person"}),
    ("Glob", {"pattern": "**/*", "path": "/home/p/.poolhouse/person"}),
    ("Glob", {"pattern": "/home/p/.poolhouse/node/client/**"}),
    ("mcp__claude-in-chrome__navigate", {"url": "https://example.org"}),
    ("mcp__claude-in-chrome__computer", {"action": "screenshot"}),
    ("mcp__ide__executeCode", {"code": "print(1)"}),
    ("mcp__files__write_file", {"path": "/work/.git/hooks/pre-push", "content": "exit 0"}),
    ("mcp__files__write_file", {"path": "/work/.git/config", "content": "{}"}),
    ("mcp__files__read_file", {"path": "/home/p/.poolhouse/person/statements.log"}),
    ("mcp__files__move", {"source": "/tmp/x", "destination": "/work/.git/hooks/pre-push"}),
    ("mcp__files__batch", {"edits": [{"path": "/work/src/ok.py"}, {"path": "/work/.git/config"}]}),
])
def test_a_tool_that_reaches_the_person_record_a_guard_or_the_owners_browser_is_refused(name, given):
    assert tool(name, given), (name, given)


@pytest.mark.parametrize("name, given", [
    ("Read", {"file_path": "/work/scripts/hooks/pre-push"}),
    ("Read", {"file_path": "/work/src/poolhouse/x.py"}),
    ("Grep", {"pattern": "token", "path": "/work/src"}),
    ("Grep", {"pattern": "\\.poolhouse/person"}),
    ("Glob", {"pattern": "**/*.py"}),
    ("Glob", {"pattern": "src/**", "path": "/work"}),
    ("mcp__files__read_file", {"path": "/work/scripts/hooks/pre-push"}),
    ("mcp__files__write_file", {"path": "/work/src/ok.py", "content": "x"}),
    ("mcp__files__write_file", {"path": "/work/scripts/hooks/pre-push", "content": "exit 0"}),
    ("mcp__files__write_file", {"path": "/work/.claude/settings.local.json", "content": "{}"}),
    ("mcp__files__batch", {"edits": [{"path": "/work/src/ok.py"}, {"path": "/work/scripts/hooks/claude-bash-guard"}]}),
    ("mcp__github__list_issues", {"owner": "o", "repo": "r"}),
    ("TaskStop", {"task_id": "t1"}),
    ("Agent", {"prompt": "edit scripts/hooks/pre-push", "description": "x"}),
])
def test_ordinary_use_of_those_tools_is_not_refused(name, given):
    assert not tool(name, given), (name, given)


def test_only_the_main_session_stops_a_task():
    assert tool("TaskStop", {"task_id": "t1"}, subagent=True)
    assert not tool("TaskStop", {"task_id": "t1"}, subagent=False)


def test_the_active_state_root_is_protected_where_it_is():
    environ = {"HOME": "/home/p", "POOLHOUSE_HOME": "/srv/state"}
    assert tool("Read", {"file_path": "/srv/state/node/client/b/x.token"}, environ=environ)
    assert tool("Grep", {"pattern": "x", "path": "/srv/state/person"}, environ=environ)
    assert not tool("Read", {"file_path": "/srv/state/sentinel/x"}, environ=environ)


def test_a_search_rooted_above_the_person_record_is_refused(tmp_path):
    (tmp_path / ".poolhouse" / "person").mkdir(parents=True)
    assert tool("Grep", {"pattern": "key", "path": str(tmp_path)}, environ={"HOME": str(tmp_path)})
    assert tool("Glob", {"pattern": "**/*", "path": str(tmp_path / ".poolhouse")}, environ={"HOME": str(tmp_path)})
    assert not tool("Grep", {"pattern": "key", "path": str(tmp_path / "elsewhere")}, environ={"HOME": str(tmp_path)})


def test_the_worktree_rules_expose_the_hard_forms_whatever_the_guard_switch_says(monkeypatch, tmp_path):
    monkeypatch.setenv("POOLHOUSE_GUARD", "off")
    assert not worktreerules.bash_refusal("sed -i s/a/b/ .git/hooks/pre-push", str(tmp_path))
    assert worktreerules.hard_bash_refusal("sed -i s/a/b/ .git/hooks/pre-push", str(tmp_path))
    assert worktreerules.hard_bash_refusal("git -c core.hooksPath=/dev/null push origin dev", str(tmp_path))
    assert not worktreerules.hard_bash_refusal("git status", str(tmp_path))
    assert not worktreerules.edit_refusal([".git/hooks/pre-push"], str(tmp_path))
    assert worktreerules.hard_edit_refusal([".git/hooks/pre-push"], str(tmp_path))
    assert worktreerules.hard_edit_refusal(["/home/p/.poolhouse/authority.json"], str(tmp_path))
    assert not worktreerules.hard_edit_refusal(["src/poolhouse/x.py", "AGENTS.md"], str(tmp_path))


def test_heredoc_bodies_are_data_for_cat_and_code_for_an_interpreter(tmp_path):
    written = "cat > notes.md <<'EOF'\nsed -i s/a/b/ .git/hooks/pre-push\nEOF"
    assert not worktreerules.bash_refusal(written, str(tmp_path)) and not worktreerules.hard_bash_refusal(written, str(tmp_path))
    run_it = "python3 - <<'EOF'\nopen('.git/hooks/pre-push','w')\nEOF"
    assert worktreerules.bash_refusal(run_it, str(tmp_path)) and worktreerules.hard_bash_refusal(run_it, str(tmp_path))


def run(event: dict, **env: str) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if k != "POOLHOUSE_GUARD"}
    return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(event), text=True, capture_output=True, env={**clean, **env})


def test_the_tool_guard_script_refuses_and_says_what_to_do():
    done = run({"tool_name": "mcp__claude-in-chrome__navigate", "cwd": "/work", "tool_input": {"url": "https://example.org"}})
    assert done.returncode == 2 and "blocked:" in done.stderr
    done = run({"tool_name": "Read", "cwd": "/work", "tool_input": {"file_path": "~/.poolhouse/person/statements.log"}})
    assert done.returncode == 2 and "person record" in done.stderr
    assert run({"tool_name": "Read", "cwd": "/work", "tool_input": {"file_path": "/work/src/x.py"}}).returncode == 0


def test_the_tool_guard_script_treats_an_event_with_an_agent_id_as_a_subagent():
    assert run({"tool_name": "TaskStop", "cwd": "/work", "agent_id": "a1", "tool_input": {"task_id": "t"}}).returncode == 2
    assert run({"tool_name": "TaskStop", "cwd": "/work", "tool_input": {"task_id": "t"}}).returncode == 0


def test_the_tool_guard_script_keeps_the_hard_rules_when_the_guard_is_off():
    event = {"tool_name": "Read", "cwd": "/work", "tool_input": {"file_path": "~/.poolhouse/person/statements.log"}}
    assert run(event, POOLHOUSE_GUARD="off").returncode == 2


def test_a_broken_event_does_not_block_every_tool_call():
    done = subprocess.run([sys.executable, str(GUARD)], input="not json", text=True, capture_output=True)
    assert done.returncode == 0

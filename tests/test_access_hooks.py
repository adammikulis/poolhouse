"""The hooks the settings wire: the tool guard is wired, the hard forms hold
when POOLHOUSE_GUARD=off, a subagent acts only as itself and the release question names the guards a release changes."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from person_support import commit, proposal, repository

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "scripts" / "hooks"
BASH, EDIT = HOOKS / "claude-bash-guard", HOOKS / "claude-edit-guard"
CLEAN = {k: v for k, v in os.environ.items() if k not in ("POOLHOUSE_GUARD", "POOLHOUSE_HOME")}


def hook(script: Path, event: dict, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script)], input=json.dumps(event), text=True, capture_output=True,
                          env={**CLEAN, **env}, check=False)


def bash(command: str, **env: str) -> subprocess.CompletedProcess:
    return hook(BASH, {"tool_name": "Bash", "cwd": str(ROOT), "tool_input": {"command": command}}, **env)


def guards_of(tool: str) -> set[str]:
    """The guard scripts the settings run before ``tool``."""
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    return {Path(h["command"]).name for entry in settings["hooks"]["PreToolUse"] if re.fullmatch(entry["matcher"], tool)
            for h in entry["hooks"]}


SHELLS = ("Bash", "Monitor")
EDITORS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
OTHERS = ("Read", "Glob", "Grep", "Agent", "Task", "TaskStop", "LSP", "WebFetch", "WebSearch", "SendMessage", "EnterWorktree",
          "ExitWorktree", "Skill", "BashOutput", "mcp__files__write_file", "mcp__claude-in-chrome__navigate",
          "mcp__ide__executeCode", "A_Tool_Added_Next_Year")


def test_every_tool_reaches_a_guard_and_a_tool_that_runs_a_command_reaches_the_shell_guard():
    for tool in SHELLS:
        assert "claude-bash-guard" in guards_of(tool), tool
    for tool in EDITORS:
        assert "claude-edit-guard" in guards_of(tool), tool
    for tool in (*SHELLS, *EDITORS, *OTHERS):
        assert guards_of(tool), f"no guard runs before {tool}"
    for tool in OTHERS:
        assert "claude-tool-guard" in guards_of(tool), tool


HARD = (
    "sed -i s/a/b/ .git/hooks/pre-push",
    "echo x > .git/config",
    "git -c core.hooksPath=/dev/null push origin dev",
    "gh pr merge 12",
    "cat ~/.poolhouse/person/statements.log",
    "python3 - <<'EOF'\nopen('.git/hooks/pre-push', 'w')\nEOF",
)


def test_the_hard_forms_hold_when_the_guard_is_off():
    for command in HARD:
        assert bash(command, POOLHOUSE_GUARD="off").returncode == 2, command
        assert bash(command).returncode == 2, command


def test_an_ordinary_command_passes_with_the_guard_on_or_off():
    assert bash("git status --short").returncode == 0
    assert bash("git status --short", POOLHOUSE_GUARD="off").returncode == 0


def test_the_edit_guard_keeps_the_hard_paths_when_the_guard_is_off():
    for path in (".git/config", ".git/hooks/pre-push", "scripts/hooks/person-consume"):
        event = {"tool_name": "Write", "cwd": str(ROOT), "tool_input": {"file_path": str(ROOT / path), "content": "x\n"}}
        assert hook(EDIT, event, POOLHOUSE_GUARD="off").returncode == 2, path
    event = {"tool_name": "Write", "cwd": str(ROOT), "tool_input": {"file_path": str(ROOT / "docs" / "x.md"), "content": "x\n"}}
    assert hook(EDIT, event, POOLHOUSE_GUARD="off").returncode == 0


def load_bash_guard():
    loader = importlib.machinery.SourceFileLoader("claude_bash_guard_under_test", str(BASH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


AS_ANOTHER = ("poolhouse-workspace inbox --agent agent-lead01", "POOLHOUSE_WORKSPACE_AGENT=agent-lead01 poolhouse runtime ensure",
              "poolhouse-workspace inbox --token-file /tmp/lead.token")
AS_ITSELF = ("poolhouse-workspace inbox --agent claude-aaaaaa", "poolhouse runtime status")


def test_a_subagent_acts_only_as_itself(monkeypatch, capsys):
    guard = load_bash_guard()
    import subagent_records
    monkeypatch.setattr(subagent_records, "load", lambda *_args, **_kwargs: {"name": "claude-aaaaaa"})
    for command in (*AS_ANOTHER, *AS_ITSELF):
        event = {"tool_name": "Bash", "cwd": str(ROOT), "agent_id": "a1", "tool_input": {"command": command}}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
        blocked = command in AS_ANOTHER
        assert guard.main() == (2 if blocked else 0), command
        assert ("claude-aaaaaa" in capsys.readouterr().err) is blocked, command


def test_the_release_question_names_the_guards_the_release_changes(tmp_path):
    repo = repository(tmp_path / "repo")
    state = tmp_path / "state"
    assert "Guards changed: none" in proposal(repo, state)[0]
    (repo / "scripts" / "hooks").mkdir(parents=True)
    commit(repo, "scripts/hooks/person-consume")
    commit(repo, "AGENTS.md")
    question = proposal(repo, state)[0]
    assert "Guards changed: AGENTS.md, scripts/hooks/person-consume" in question
    assert "Commits ahead of origin/main: 3" in question


TOOLS = HOOKS / "claude-tool-guard"
UNKNOWN = "A_Tool_Added_Next_Year"


def test_a_command_run_by_the_monitor_tool_meets_the_shell_rules():
    for command in HARD:
        asked = {"tool_name": "Monitor", "cwd": str(ROOT), "tool_input": {"command": command, "description": "x", "timeout_ms": 1000}}
        assert hook(BASH, asked).returncode == 2, command
        assert hook(BASH, asked, POOLHOUSE_GUARD="off").returncode == 2, command
    ok = {"tool_name": "Monitor", "cwd": str(ROOT), "tool_input": {"command": "git status --short", "description": "x", "timeout_ms": 1000}}
    assert hook(BASH, ok).returncode == 0
    over_a_socket = {"tool_name": "Monitor", "cwd": str(ROOT), "tool_input": {"ws": {"url": "wss://example.invalid/s"}, "description": "x"}}
    assert hook(BASH, over_a_socket).returncode == 0


def test_a_command_in_a_tool_no_guard_knows_meets_the_shell_rules():
    for command in HARD:
        asked = {"tool_name": UNKNOWN, "cwd": str(ROOT), "tool_input": {"command": command}}
        assert hook(TOOLS, asked).returncode == 2, command
    assert hook(TOOLS, {"tool_name": UNKNOWN, "cwd": str(ROOT), "tool_input": {"command": "git status"}}).returncode == 0
    subagent = {"tool_name": UNKNOWN, "agent_id": "a1", "cwd": str(ROOT), "tool_input": {"command": "git status"}}
    assert hook(TOOLS, subagent).returncode == 2, "a subagent's command in an unknown tool cannot be stamped as the subagent"


def test_a_path_in_a_tool_no_guard_knows_is_checked_like_the_read_tool():
    for key in ("filePath", "file_path", "path", "notebook_path"):
        asked = {"tool_name": "LSP", "cwd": str(ROOT), "tool_input": {key: "~/.poolhouse/person/statements.log", "line": 1}}
        assert hook(TOOLS, asked).returncode == 2, key
    ordinary = {"tool_name": "LSP", "cwd": str(ROOT), "tool_input": {"filePath": str(ROOT / "src" / "poolhouse" / "authority.py")}}
    assert hook(TOOLS, ordinary).returncode == 0


def test_a_subagent_command_in_the_monitor_tool_is_stamped_as_the_subagent(monkeypatch, capsys):
    guard = load_bash_guard()
    import subagent_records
    monkeypatch.setattr(subagent_records, "load", lambda *_args, **_kwargs: {"name": "claude-aaaaaa", "board": "demo"})
    event = {"tool_name": "Monitor", "cwd": str(ROOT), "agent_id": "a1",
             "tool_input": {"command": "tail -f x", "description": "x", "timeout_ms": 1000}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    assert guard.main() == 0
    stamped = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["updatedInput"]
    assert stamped["command"].endswith("\ntail -f x") and "POOLHOUSE_WORKSPACE_AGENT=claude-aaaaaa" in stamped["command"]
    assert stamped["timeout_ms"] == 1000


def test_the_stamp_takes_the_leads_native_session_out_of_a_subagent_s_shell(monkeypatch, capsys):
    guard = load_bash_guard()
    import subagent_records
    monkeypatch.setattr(subagent_records, "load", lambda *_args, **_kwargs: {"name": "claude-aaaaaa"})
    event = {"tool_name": "Bash", "cwd": str(ROOT), "agent_id": "a1", "tool_input": {"command": "env"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    assert guard.main() == 0
    stamp = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["updatedInput"]["command"].splitlines()
    for name in ("CLAUDE_CODE_SESSION_ID", "POOLHOUSE_SESSION_ID", "POOLHOUSE_SESSION_HARNESS", "CODEX_THREAD_ID"):
        assert f"unset {name}" in stamp, name

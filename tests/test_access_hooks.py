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


def test_the_settings_wire_the_tool_guard_to_every_tool_the_other_guards_do_not_see():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    wired = {entry["matcher"]: [h["command"] for h in entry["hooks"]] for entry in settings["hooks"]["PreToolUse"]}
    tools = [m for m, commands in wired.items() if any(c.endswith("scripts/hooks/claude-tool-guard") for c in commands)]
    assert len(tools) == 1
    for name in ("Read", "Glob", "Grep", "Agent", "TaskStop", "mcp__files__write_file", "mcp__claude-in-chrome__navigate",
                 "mcp__ide__executeCode"):
        assert re.fullmatch(tools[0], name), name
    for name in ("Bash", "Write", "Edit"):
        assert not re.fullmatch(tools[0], name), name
    assert any(c.endswith("claude-bash-guard") for c in wired["Bash"])


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

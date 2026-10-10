"""Hooks of the old project name, or of a program that is gone, are removed from Claude Code's and
Codex's settings by `install-hooks` and by `migrate run`, and `poolhouse-doctor`'s check names them."""

from __future__ import annotations

import argparse
import json
import tomllib

import pytest

from poolhouse import agent_hooks, agent_hooks_stale, authority, migrate
from poolhouse.workspace import hooks_cli

GONE = "/private/tmp/poolside-hook-runtime-1a2b/bin/python"
OLD_CLAUDE = {"hooks": {
    "PostToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": "ml-stack-workspace nudge --agent claude --hook post"},
        {"type": "command", "command": "other-tool"}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "ml-stack-workspace nudge --agent claude --hook stop"}]}],
    "SessionStart": [{"hooks": [{"type": "command", "command": f"{GONE} -m x"}]}]}}
OLD_CODEX = f'''model = "m"

[features]
hooks = true

[[hooks.PostToolUse]]
matcher = ".*"

[[hooks.PostToolUse.hooks]]
type = "command"
command = "{GONE} -m ml_stack.harnesshook post --agent codex"

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = "ml-stack-workspace nudge --agent codex --hook stop"

[[hooks.Stop.hooks]]
type = "command"
command = "keep-me"

[agents]
max_threads = 4
'''


@pytest.fixture(autouse=True)
def machine(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(agent_hooks.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(authority, "require_person", lambda *a, **k: None)
    return tmp_path


def commands(data, event):
    return [h["command"] for g in data["hooks"].get(event, []) for h in g["hooks"]]


@pytest.mark.parametrize("line", ["ml-stack-workspace nudge", "/usr/bin/ml_stack-x", "python -m ml_stack.harnesshook post",
                                  "poolside-node run", f"{GONE} -m anything"])
def test_an_old_name_or_a_missing_program_is_stale(line):
    assert agent_hooks_stale.is_stale(line)


@pytest.mark.parametrize("line", ["poolhouse-workspace nudge --hook post", "sh /Users/x/repos/ml-stack/scripts/hook",
                                  "python -m poolhouse.harnesshook post", "/bin/sh -c true", ""])
def test_a_current_command_is_not_stale_even_inside_a_checkout_named_ml_stack(line):
    assert not agent_hooks_stale.is_stale(line)


def test_install_hooks_removes_the_stale_claude_entries_and_keeps_the_rest(machine, capsys):
    path = machine / "s.json"
    path.write_text(json.dumps(OLD_CLAUDE))
    args = argparse.Namespace(settings=str(path), codex_config="", only=["claude-code"], agent="")
    assert hooks_cli.install(args, None) == 0
    data = json.loads(path.read_text())
    assert commands(data, "PostToolUse") == ["other-tool", "poolhouse-workspace nudge --agent claude --hook post"]
    assert commands(data, "Stop") == ["poolhouse-workspace nudge --agent claude --hook stop"]
    assert "SessionStart" not in data["hooks"]
    out = capsys.readouterr().out
    assert out.count("removed the hook") == 3 and "ml-stack-workspace nudge" in out
    backups = list(machine.glob("s.json.bak-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == OLD_CLAUDE


def test_install_hooks_removes_the_stale_codex_entries_and_keeps_the_rest(machine):
    path = machine / "c.toml"
    path.write_text(OLD_CODEX)
    args = argparse.Namespace(settings="", codex_config=str(path), only=["codex"], agent="")
    assert hooks_cli.install(args, None) == 0
    data = tomllib.loads(path.read_text())
    assert commands(data, "Stop") == ["keep-me", "poolhouse-workspace nudge --agent codex --hook stop"]
    assert commands(data, "PostToolUse") == ["poolhouse-workspace nudge --agent codex --hook post"]
    assert data["agents"] == {"max_threads": 4} and "ml_stack" not in path.read_text()
    assert len(list(machine.glob("c.toml.bak-*"))) == 1


def test_a_file_with_nothing_stale_is_not_rewritten_or_backed_up(machine):
    path = machine / "s.json"
    agent_hooks.install_claude(path)
    before = path.read_text()
    assert agent_hooks_stale.remove("claude-code", path) == []
    assert path.read_text() == before and not list(machine.glob("s.json.bak-*"))


def test_the_doctor_check_flags_a_stale_entry_with_the_fix(machine):
    claude, codex = machine / "s.json", machine / "c.toml"
    claude.write_text(json.dumps(OLD_CLAUDE))
    codex.write_text(OLD_CODEX)
    found = agent_hooks.findings({"claude-code": claude, "codex": codex})
    assert [f.good for f in found] == [False, False]
    assert all("old-name or missing program" in f.said and f.fix == ["poolhouse-workspace", "install-hooks"]
               for f in found)


def test_migrate_run_removes_the_old_hooks_from_the_account_settings(machine, capsys):
    account = machine / "acct"
    (account / ".claude").mkdir(parents=True)
    (account / ".codex").mkdir(parents=True)
    (account / ".claude" / "settings.json").write_text(json.dumps(OLD_CLAUDE))
    (account / ".codex" / "config.toml").write_text(OLD_CODEX)
    lines = migrate._hooks_step(account)
    assert len(lines) == 5 and all(line.startswith("hooks: ") for line in lines)
    assert "ml-stack-workspace" not in (account / ".claude" / "settings.json").read_text()
    assert "ml_stack" not in (account / ".codex" / "config.toml").read_text()
    assert migrate._hooks_step(account) == []

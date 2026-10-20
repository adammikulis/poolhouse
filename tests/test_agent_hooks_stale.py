"""Missing absolute hook executables are removed atomically and reported by the doctor."""

from __future__ import annotations

import argparse
import json
import tomllib

import pytest

from poolhouse import agent_hooks, agent_hooks_stale, authority
from poolhouse.workspace import hooks_cli

GONE = "/private/tmp/retired-hook-runtime-1a2b/bin/python"
STALE_CLAUDE = {"hooks": {
    "PostToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": f"{GONE} --hook post"},
        {"type": "command", "command": "other-tool"}]}],
    "Stop": [{"hooks": [{"type": "command", "command": f"{GONE} --hook stop"}]}],
    "SessionStart": [{"hooks": [{"type": "command", "command": f"{GONE} -m x"}]}]}}
STALE_CODEX = f'''model = "m"

[features]
hooks = true

[[hooks.PostToolUse]]
matcher = ".*"

[[hooks.PostToolUse.hooks]]
type = "command"
command = "{GONE} -m retired_module.hook post --agent codex"

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = "{GONE} --hook stop"

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


@pytest.mark.parametrize("line", [f"{GONE} --hook post", f"{GONE} -m anything"])
def test_a_missing_absolute_program_is_stale(line):
    assert agent_hooks_stale.is_stale(line)


@pytest.mark.parametrize("line", ["poolhouse-workspace nudge --hook post", "sh /workspace/example-project/scripts/hook",
                                  "python -m poolhouse.harnesshook post", "/bin/sh -c true", ""])
def test_a_current_command_is_not_stale_based_on_its_arguments(line):
    assert not agent_hooks_stale.is_stale(line)


def test_install_hooks_removes_the_stale_claude_entries_and_keeps_the_rest(machine, capsys):
    path = machine / "s.json"
    path.write_text(json.dumps(STALE_CLAUDE))
    args = argparse.Namespace(settings=str(path), codex_config="", only=["claude-code"], agent="")
    assert hooks_cli.install(args) == 0
    data = json.loads(path.read_text())
    assert commands(data, "PostToolUse") == ["other-tool", "poolhouse-workspace nudge --agent claude --hook post"]
    assert commands(data, "Stop") == ["poolhouse-workspace nudge --agent claude --hook stop"]
    assert "SessionStart" not in data["hooks"]
    out = capsys.readouterr().out
    assert out.count("removed the hook") == 3 and GONE in out
    backups = list(machine.glob("s.json.bak-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == STALE_CLAUDE


def test_install_hooks_removes_the_stale_codex_entries_and_keeps_the_rest(machine):
    path = machine / "c.toml"
    path.write_text(STALE_CODEX)
    args = argparse.Namespace(settings="", codex_config=str(path), only=["codex"], agent="")
    assert hooks_cli.install(args) == 0
    data = tomllib.loads(path.read_text())
    assert commands(data, "Stop") == ["keep-me", "poolhouse-workspace codex-hook", "poolhouse-workspace codex-stop"]
    assert commands(data, "PostToolUse") == ["poolhouse-workspace codex-hook"]
    assert data["agents"] == {"max_threads": 4} and GONE not in path.read_text()
    assert len(list(machine.glob("c.toml.bak-*"))) == 1


def test_a_file_with_nothing_stale_is_not_rewritten_or_backed_up(machine):
    path = machine / "s.json"
    agent_hooks.install_claude(path)
    before = path.read_text()
    assert agent_hooks_stale.remove("claude-code", path) == []
    assert path.read_text() == before and not list(machine.glob("s.json.bak-*"))


def test_the_doctor_check_flags_a_stale_entry_with_the_fix(machine):
    claude, codex = machine / "s.json", machine / "c.toml"
    claude.write_text(json.dumps(STALE_CLAUDE))
    codex.write_text(STALE_CODEX)
    found = agent_hooks.findings({"claude-code": claude, "codex": codex})
    assert [f.good for f in found] == [False, False]
    assert all("missing program" in f.said and f.fix == ["poolhouse-workspace", "install-hooks"]
               for f in found)

"""The nudge hooks: written only when asked, only into one project's own settings, and never required."""

from __future__ import annotations

import argparse
import json
import tomllib

import authority_kit
import pytest
from node_kit import node_binary, workspace_node  # noqa: F401

from poolhouse import agent_hooks, authority
from poolhouse.caller import AgentRefused
from poolhouse.workspace import cli, hooks_cli, onboard

CODEX_BEFORE = '''model = "gpt-6.1-sol"

[features]
hooks = true
multi_agent = true

[[hooks.PostToolUse]]
matcher = ".*"

[[hooks.PostToolUse.hooks]]
type = "command"
command = "other-notice"
timeout = 30

[hooks.state."fixture-existing-trust"]
trusted_hash = "fixture-preserved-record"

[agents]
max_threads = 32
'''
USERS_OWN = {".claude/settings.json": '{"model": "mine"}\n', ".claude.json": "{}\n", ".codex/config.toml": 'model = "mine"\n',
             ".gitconfig": "[user]\n", ".zshrc": "# mine\n"}


@pytest.fixture(autouse=True)
def machine(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    for name in (*authority.DELEGATING, "POOLHOUSE_NONINTERACTIVE"):
        monkeypatch.delenv(name, raising=False)
    for name, text in USERS_OWN.items():
        path = tmp_path / "home" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (tmp_path / "project").mkdir()
    return tmp_path


def user_files(machine):
    home = machine / "home"
    return {str(path.relative_to(home)): path.read_text() for path in home.rglob("*") if path.is_file()}


def commands(data, event):
    return [h["command"] for g in data["hooks"][event] for h in g["hooks"]]


def asked(project, *only):
    return argparse.Namespace(project=str(project), only=list(only), agent="")


def test_install_writes_the_projects_own_local_settings_and_nothing_of_the_users(machine):
    project = machine / "project"
    done = agent_hooks.install(project)
    real = project.resolve()
    assert {agent: path for agent, (path, _) in done.items()} == {"claude-code": real / ".claude" / "settings.local.json",
                                                                   "codex": real / ".codex" / "config.toml"}
    assert sorted(str(p.relative_to(project)) for p in project.rglob("*") if p.is_file()) == [".claude/settings.local.json", ".codex/config.toml"]
    assert user_files(machine) == USERS_OWN
    claude = json.loads((real / ".claude" / "settings.local.json").read_text())
    assert commands(claude, "Stop") == ["poolhouse-workspace nudge --agent claude --hook stop"]
    codex = tomllib.loads((real / ".codex" / "config.toml").read_text())
    assert codex["features"] == {"hooks": True} and commands(codex, "PostToolUse") == ["poolhouse-workspace codex-hook"]


@pytest.mark.parametrize("where", ["home", "above-home", "global-folder", "missing"])
def test_a_folder_that_is_not_a_project_is_refused_and_nothing_is_written(machine, where):
    project = {"home": machine / "home", "above-home": machine, "global-folder": machine / "home" / ".claude",
               "missing": machine / "absent"}[where]
    with pytest.raises(ValueError):
        agent_hooks.install(project)
    assert user_files(machine) == USERS_OWN and not (machine / "absent").exists()


@pytest.mark.parametrize("link", [".claude", ".codex", ".claude/settings.local.json", ".codex/config.toml"])
def test_a_project_link_into_the_users_settings_is_refused_before_either_file_is_written(machine, link):
    project = machine / "project"
    target = machine / "home" / link.replace("settings.local.json", "settings.json")
    (project / link).parent.mkdir(parents=True, exist_ok=True)
    (project / link).symlink_to(target)
    with pytest.raises(ValueError, match="is a link"):
        agent_hooks.install(project)
    assert user_files(machine) == USERS_OWN
    assert not [p for p in project.rglob("*") if p.is_file() and not p.is_symlink()]


@pytest.mark.parametrize("link, target", [(".claude/settings.local.json", "made-by-install.json"),
                                          (".codex/config.toml", "made-by-install.toml"),
                                          (".claude", "other-folder"), (".claude/settings.local.json", "kept.json")])
def test_a_committed_link_inside_the_project_is_refused_and_its_target_is_neither_made_nor_changed(machine, link, target):
    project = machine / "project"
    (project / "kept.json").write_text("{}")
    (project / link).parent.mkdir(parents=True, exist_ok=True)
    (project / link).symlink_to(project / target)
    with pytest.raises(ValueError, match="is a link"):
        agent_hooks.install(project)
    assert sorted(p.name for p in project.rglob("*") if p.is_file() and not p.is_symlink()) == ["kept.json"]
    assert (project / "kept.json").read_text() == "{}" and not (project / "other-folder").exists()


@pytest.mark.parametrize("variable, agent, folder", [("CLAUDE_CONFIG_DIR", "claude-code", ".claude"), ("CODEX_HOME", "codex", ".codex")])
def test_a_relocated_user_settings_folder_is_never_written_even_inside_a_project(machine, monkeypatch, variable, agent, folder):
    monkeypatch.setenv(variable, str(machine / "project" / folder))
    with pytest.raises(ValueError, match="user's own"):
        agent_hooks.install(machine / "project", (agent,))
    assert not (machine / "project" / folder).exists()


def test_claude_hooks_are_idempotent_keep_other_hooks_and_replace_earlier_nudges(machine):
    path = machine / "project" / ".claude" / "settings.local.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"model": "x", "hooks": {
        "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "other-tool"}]},
                        {"matcher": "*", "hooks": [{"type": "command", "command": "poolhouse-workspace nudge --agent old --hook post"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "keep-me"},
                            {"type": "command", "command": "poolhouse-workspace nudge --agent old --hook stop"}]}]}}))
    agent_hooks.install(machine / "project", ("claude-code",))
    first = path.read_text()
    agent_hooks.install(machine / "project", ("claude-code",))
    assert path.read_text() == first
    data = json.loads(first)
    assert data["model"] == "x"
    assert commands(data, "PostToolUse") == ["other-tool", "poolhouse-workspace nudge --agent claude --hook post"]
    assert commands(data, "Stop") == ["keep-me", "poolhouse-workspace nudge --agent claude --hook stop"]
    assert commands(data, "UserPromptSubmit") == ["poolhouse-workspace nudge --agent claude --hook prompt"]
    assert data["hooks"]["PostToolUse"][-1]["matcher"] == "*"
    assert not (machine / "project" / ".codex").exists()


def test_codex_hooks_keep_other_settings_and_a_second_run_changes_nothing(machine):
    path = machine / "project" / ".codex" / "config.toml"
    path.parent.mkdir()
    path.write_text(CODEX_BEFORE)
    done = agent_hooks.install(machine / "project", ("codex",))
    assert done["codex"][1] == [event for event, _, _ in agent_hooks.CODEX_EVENTS]
    text = path.read_text()
    data = tomllib.loads(text)
    before = tomllib.loads(CODEX_BEFORE)
    assert data["model"] == "gpt-6.1-sol" and data["agents"] == {"max_threads": 32}
    assert data["hooks"]["state"] == before["hooks"]["state"] and data["features"] == before["features"]
    assert commands(data, "PostToolUse") == ["other-notice", "poolhouse-workspace codex-hook"]
    for event, _, _ in agent_hooks.CODEX_EVENTS:
        assert data['hooks'][event][-1]['hooks'][0]['timeout'] == (3 if event == 'SessionEnd' else 5)
    agent_hooks.install(machine / "project", ("codex",))
    assert path.read_text() == text


def test_codex_turns_the_hooks_feature_on(machine):
    path = machine / "project" / ".codex" / "config.toml"
    path.parent.mkdir()
    path.write_text('[features]\nhooks = false\nshell_snapshot = true\n')
    agent_hooks.install(machine / "project", ("codex",))
    assert tomllib.loads(path.read_text())["features"] == {"hooks": True, "shell_snapshot": True}


def test_a_file_that_cannot_be_read_as_settings_leaves_both_files_untouched(machine):
    path = machine / "project" / ".codex" / "config.toml"
    path.parent.mkdir()
    path.write_text("model = [unclosed\n")
    with pytest.raises(ValueError):
        agent_hooks.install(machine / "project")
    assert path.read_text() == "model = [unclosed\n" and not (machine / "project" / ".claude").exists()


@pytest.mark.parametrize("assigned", ["", "codex-fixture"])
@pytest.mark.parametrize("with_post", [False, True])
def test_codex_context_limits_only_apply_to_events_the_native_host_supports(assigned, with_post):
    data = tomllib.loads(agent_hooks.codex_hooks(with_post=with_post, assigned=assigned))
    context_events = {"SessionStart", "SubagentStart", "PostToolUse", "UserPromptSubmit"}
    suffix = f" --assigned {assigned}" if assigned else ""
    for event, _, _ in agent_hooks.CODEX_EVENTS:
        if event == "PostToolUse" and not with_post:
            assert event not in data["hooks"]
            continue
        handlers = data["hooks"][event][0]["hooks"]
        notification = handlers[0]
        assert notification["command"] == "poolhouse-workspace codex-hook" + suffix
        assert notification["timeout"] == (3 if event == "SessionEnd" else 5)
        if event in context_events:
            assert notification["additionalContextLimit"] == 4000
        else:
            assert "additionalContextLimit" not in notification
        assert len(handlers) == 1


def test_the_command_needs_a_project_and_writes_nothing_without_one(machine, monkeypatch):
    monkeypatch.chdir(machine / "project")
    with pytest.raises(SystemExit) as stopped:
        cli.main(["install-hooks"])
    assert stopped.value.code == 2
    assert user_files(machine) == USERS_OWN and not list((machine / "project").iterdir())


def test_the_command_says_what_it_wrote_and_why_it_refused(machine, capsys, monkeypatch):
    monkeypatch.setattr(authority, "refuse_agent", lambda *a, **k: None)
    assert hooks_cli.install(asked(machine / "project")) == 0
    out = capsys.readouterr().out
    assert "claude-code: PostToolUse, Stop, UserPromptSubmit" in out and "codex: SessionStart, SubagentStart" in out
    assert "settings.local.json" in out and "trusts" in out
    assert hooks_cli.install(asked(machine / "home")) == 1
    assert "not written" in capsys.readouterr().err and user_files(machine) == USERS_OWN


def test_the_install_command_follows_the_registry(machine, monkeypatch, workspace_node):  # noqa: F811
    args = asked(machine / "project", "claude-code")
    written = machine / "project" / ".claude" / "settings.local.json"
    monkeypatch.setenv("POOLHOUSE_HOME", str(workspace_node.state.parent))
    monkeypatch.setenv("POOLHOUSE_BOARD", workspace_node.board)
    lead = workspace_node.member("hooks-lead")
    authority.set_state(["workspace.setup"], authority.PERSON, by="lead")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", lead.name)
    with pytest.raises(AgentRefused, match="held back from agents"):
        hooks_cli.install(args)
    assert not written.exists()
    assert [r["action"] for r in authority.audit_rows() if r["event"] == "refused"] == ["workspace install-hooks"]
    authority_kit.delegate(["workspace.setup"])
    assert hooks_cli.install(args) == 0
    assert written.is_file()
    assert [r["agent"] for r in authority.audit_rows() if r["event"] == "authority.use"] == [lead.name]


def test_the_install_command_runs_directly_for_a_process_with_no_agent_marker_even_when_the_gate_is_held_back(
        machine, monkeypatch, workspace_node):  # noqa: F811
    monkeypatch.setenv("POOLHOUSE_HOME", str(workspace_node.state.parent))
    authority.set_state(["workspace.setup"], authority.PERSON, by="lead")
    assert hooks_cli.install(asked(machine / "project", "claude-code")) == 0
    assert (machine / "project" / ".claude" / "settings.local.json").is_file()
    (row,) = [r for r in authority.audit_rows() if r["event"] == "ran"]
    assert row["who"].startswith("user ")


def test_the_workspace_doctor_does_not_look_for_hooks(machine, workspace_node):  # noqa: F811
    session = workspace_node.session(workspace_node.member("doctor"))
    found = onboard.doctor(session)
    assert found and all(f.ok for f in found), [f.what for f in found if not f.ok]
    assert not [f for f in found if "hooks" in f.what]


@pytest.mark.parametrize('foreign', ['echo "poolhouse-workspace nudge"',
    'poolhouse-workspace nudge --agent claude --hook post && foreign-tool',
    '/foreign/poolhouse-workspace nudge --agent claude --hook post',
    'other-tool --note "claude-nudge.sh"', 'sh ~/.poolhouse/hooks/claude-nudge.sh'])
def test_install_preserves_foreign_commands_that_only_mention_owned_hooks(machine, foreign):
    path = machine / 'project' / '.claude' / 'settings.local.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'hooks': {'PostToolUse': [{'hooks': [{'type': 'command', 'command': foreign}]}]}}))
    agent_hooks.install(machine / 'project', ('claude-code',))
    assert commands(json.loads(path.read_text()), 'PostToolUse') == [foreign, agent_hooks.command('claude', 'post')]


@pytest.mark.parametrize('foreign', ['poolhouse-workspace nudge --hook unknown',
    'poolhouse-workspace nudge --agent own --agent other',
    'poolhouse-workspace codex-hook --assigned own extra', '"unterminated poolhouse-workspace nudge'])
def test_unknown_or_malformed_command_shapes_are_not_owned(foreign):
    assert not agent_hooks._own(foreign)

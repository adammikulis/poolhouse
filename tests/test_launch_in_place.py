"""A launch the person runs is on the project folder itself, with their own settings, MCP servers and network, pointed at the served model by
this run's arguments and environment alone; `--sandbox` is the confined path.

PLUMBING ONLY: no model is served. `--on` names an address nothing is asked at (the alias and the window are stood in for), and the harness is
either never started or a marked shell script that is no coding agent.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from poolhouse import (
    claude,
    codex,
    coding,
    coding_policy,
    harnessid,
    harnessing,
    isolation,
    opencode,
    pi,
)
from tests.isolation_kit import without_a_sandbox
from tests.sandbox_kit import AbsentBackend

URL = "http://127.0.0.1:18999"
ALIAS = "plumbing-NOT-A-MODEL"
OWN = {"claude": claude, "codex": codex, "opencode": opencode, "pi": pi}


@pytest.fixture
def person(tmp_path, monkeypatch):
    """A person's home with their own harness settings, a folder that is no git project, a computer with no sandbox and a launch typed inside an
    agent's shell, whose session names must not reach the harness."""
    home = tmp_path / "person"
    for name, text in ((".claude/settings.json", '{"model": "theirs"}'), (".codex/config.toml", 'model = "theirs"\n'),
                       (".config/opencode/opencode.json", "{}")):
        (home / name).parent.mkdir(parents=True, exist_ok=True)
        (home / name).write_text(text)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "their-hosted-key")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "the-typing-session")
    project = tmp_path / "plain-folder"
    project.mkdir()
    for module in OWN.values():
        monkeypatch.setattr(module, "alias_of", lambda *_: ALIAS)
    monkeypatch.setattr(harnessing, "window_of", lambda _url: 65536)
    without_a_sandbox(monkeypatch, AbsentBackend)
    return home, project.resolve()


def session(name, project, parent, say):
    return harnessid.HarnessSession(f"helper-{name[-6:]}")


def launched(harness, person, *words, extra=(), **options):
    """Run ``harness`` with a runner that keeps what it was given; returns its exit code, the run and the launcher's lines."""
    _home, project = person
    seen, lines = {}, []

    def runner(run):
        seen.update(argv=list(run.argv), env=dict(run.env), cwd=run.cwd, config=run.config,
                    files={word: Path(word).read_text() for word in run.argv[1:] if len(word) < 255 and Path(word).is_file()})
        if "OPENCODE_CONFIG" in run.env:
            seen["opencode"] = json.loads(Path(run.env["OPENCODE_CONFIG"]).read_text())
        return 7

    binary = shutil.which("true") or "/usr/bin/true"
    argv = ["--on", URL, "--project", str(project), f"--{harness}", binary, *words, *(("--", *extra) if extra else ())]
    code = OWN[harness].launch(argv, say=lines.append, session_factory=session, **{f"run_{harness}": runner}, **options)
    return code, seen, lines


@pytest.mark.parametrize("harness", OWN)
def test_a_launch_runs_on_the_project_folder_itself_on_a_computer_with_no_sandbox_and_a_folder_that_is_no_git_project(harness, person):
    home, project = person
    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    code, seen, lines = launched(harness, person)
    assert code == 7, lines
    assert seen["cwd"] == str(project) and seen["config"] is None, "no copy, no folder of the launcher's for the harness's own files"
    assert seen["env"]["HOME"] == str(home) and seen["env"]["PATH"] == os.environ["PATH"], "the person's own settings, MCP servers and programs"
    assert seen["env"]["POOLHOUSE_AGENT"] and "CLAUDECODE" not in seen["env"] and "CLAUDE_CODE_SESSION_ID" not in seen["env"], \
        "marked as an agent, and never as the session the command was typed in"
    assert {path: path.read_bytes() for path in home.rglob("*") if path.is_file()} == before, "nothing of the person's is written"
    assert list(project.iterdir()) == [], "nothing is written into the project"


def test_claude_is_pointed_at_the_model_by_this_runs_settings_and_keeps_the_persons_mcp_servers(person):
    code, seen, _ = launched("claude", person)
    assert code == 7
    assert "--strict-mcp-config" not in seen["argv"] and not [word for word in seen["argv"] if word.startswith("--mcp-config")]
    settings = json.loads(seen["files"][seen["argv"][seen["argv"].index("--settings") + 1]])
    assert settings["env"]["ANTHROPIC_BASE_URL"] == seen["env"]["ANTHROPIC_BASE_URL"] == URL, "over the person's own `env` setting"
    assert settings["env"]["ANTHROPIC_MODEL"] == ALIAS and settings["env"]["ANTHROPIC_API_KEY"] == "" and "ANTHROPIC_API_KEY" not in seen["env"]
    assert "hooks" not in settings, "the person's permission rules decide, and no rail of the launcher's refuses an install"
    assert not [word for word in seen["argv"] if word.startswith(("--permission-mode", "--disallowed-tools", "--dangerously"))]


def test_codex_is_pointed_at_the_model_by_overrides_and_keeps_its_own_home_and_sandbox(person):
    code, seen, _ = launched("codex", person)
    assert code == 7
    given = [seen["argv"][at + 1] for at, word in enumerate(seen["argv"]) if word == "-c"]
    assert 'model_provider="poolhouse"' in given and f'model="{ALIAS}"' in given
    assert f'model_providers.poolhouse.base_url="{URL}/v1"' in given and seen["env"]["POOLHOUSE_API_KEY"]
    assert "CODEX_HOME" not in seen["env"] or seen["env"]["CODEX_HOME"] == os.environ.get("CODEX_HOME")
    assert "--sandbox" not in seen["argv"] and not [each for each in given if each.startswith(("sandbox_mode", "approval_policy"))]


def test_opencode_runs_the_one_on_path_interactively_or_once_with_a_message(person, tmp_path, monkeypatch):
    bin_folder = tmp_path / "bin"
    bin_folder.mkdir()
    own = bin_folder / "opencode"
    own.write_text("#!/bin/sh\n# PLUMBING, NOT A CODING AGENT\n")
    own.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_folder}{os.pathsep}{os.environ['PATH']}")
    assert harnessing.binary_for("opencode") == str(own), "an opencode on PATH is the person's own"
    code, seen, _ = launched("opencode", person)
    assert code == 7 and seen["argv"][1:] == ["--model", f"poolhouse/{ALIAS}"], "no message: the interactive screen"
    made = seen["opencode"]
    assert made["provider"]["poolhouse"]["options"]["baseURL"] == URL + "/v1" and made["model"] == f"poolhouse/{ALIAS}"
    assert not {"mcp", "plugin", "enabled_providers", "permission", "share", "autoupdate"} & set(made), "the person's own settings stand"
    code, seen, _ = launched("opencode", person, extra=("fix the test",))
    assert code == 7 and seen["argv"][1:] == ["run", "--model", f"poolhouse/{ALIAS}", "fix the test"]


READ_ONLY_OF = {
    "claude": lambda seen: "--disallowed-tools=Bash,Edit,MultiEdit,Write,NotebookEdit" in seen["argv"],
    "codex": lambda seen: 'sandbox_mode="read-only"' in seen["argv"] and 'approval_policy="never"' in seen["argv"],
    "opencode": lambda seen: seen["opencode"]["permission"] == {"edit": "deny", "bash": "deny", "shell": "deny"},
    "pi": lambda seen: seen["argv"][seen["argv"].index("--tools") + 1] == "read,grep,find,ls",
}


@pytest.mark.redteam
@pytest.mark.parametrize("harness", OWN)
@pytest.mark.parametrize("words", [["--permissions", "read-only"], ["--execution-mode", "plan"]])
def test_a_read_only_or_planning_launch_in_place_denies_the_shell_and_editing_by_the_harnesss_own_rules(harness, words, person):
    code, seen, lines = launched(harness, person, *words)
    assert code == 7 and READ_ONLY_OF[harness](seen), (seen.get("argv"), lines)


def test_yolo_and_an_asked_for_guarded_print_run_use_the_harnesss_own_flags(person):
    _, seen, _ = launched("claude", person, "--permissions", "yolo")
    assert "--dangerously-skip-permissions" in seen["argv"]
    _, seen, _ = launched("claude", person, "--permissions", "guarded", extra=("--print", "go"))
    assert seen["argv"][seen["argv"].index("--permission-mode") + 1] == "acceptEdits"
    _, seen, _ = launched("codex", person, "--permissions", "guarded", extra=("exec", "go"))
    assert 'sandbox_mode="workspace-write"' in seen["argv"] and seen["argv"][-2:] == ["exec", "go"]
    _, seen, _ = launched("claude", person, extra=("--print", "go"))
    assert "--permission-mode" not in seen["argv"], "nothing asked for: the person's own settings"


def test_a_launch_asked_for_the_sandbox_takes_the_confined_path(person, monkeypatch):
    # the widenings of a sandboxed launch are decided by the node, which tests/test_isolation_gate.py holds; here they are the empty ones
    monkeypatch.setattr(harnessing, "decided", lambda args, asked: isolation.Launch((), (), False, False))
    code, seen, lines = launched("claude", person, "--sandbox", extra=("--print", "go"))
    assert code == 2 and not seen and "built-in sandbox tool is missing" in lines[-1]


def test_a_harness_started_by_the_launcher_runs_in_the_project_and_reaches_what_the_person_reaches(person, tmp_path):
    home, project = person
    script = tmp_path / "stand-in"
    script.write_text("#!/bin/sh\n# PLUMBING, NOT A CODING AGENT\necho \"$HOME $ANTHROPIC_BASE_URL\" > made-here.txt\nexit 3\n")
    script.chmod(0o755)
    code = claude.launch(["--on", URL, "--project", str(project), "--claude", str(script), "--", "--print", "go"],
                         say=lambda _: None, session_factory=session)
    assert code == 3
    assert (project / "made-here.txt").read_text().split() == [str(home), URL]


def test_a_launch_goes_on_without_a_workspace_session_when_none_can_be_made(person):
    def unreachable(*_):
        raise ValueError("the board is not reachable")

    _home, project = person
    lines = []
    code = claude.launch(["--on", URL, "--project", str(project), "--claude", shutil.which("true")], say=lines.append,
                         session_factory=unreachable, run_claude=lambda run: 5)
    assert code == 5 and any("without one" in line for line in lines), lines


def test_a_program_starts_in_place_unless_it_asks_for_the_sandbox(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setitem(coding.HARNESSES, "claude", lambda argv, **_: seen.append(argv) or 0)
    policy = coding_policy.Policy()
    coding.launch_coding_agent("m", policy, tmp_path, harness="claude")
    coding.launch_coding_agent("m", policy, tmp_path, harness="claude", sandbox=True)
    assert "--sandbox" not in seen[0] and "--sandbox" in seen[1]

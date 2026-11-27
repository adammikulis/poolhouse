"""The environment of a confined harness: a closed table, never a copy of the launcher's.

The launcher's own environment is hostile here (a token, an agent socket, a marker, another variable that names a toolchain), and the real
confined probe reads what its environment holds and says which names it has.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from poolhouse import sandbox
from poolhouse.isolation import Inputs, allowed, build, environment, proof
from poolhouse.isolation.environment import (
    ALWAYS,
    BY_HARNESS,
    INTERACTIVE,
    MODEL_VARS,
    OFFLINE,
    SHELL,
    SHELL_BOUNDS,
    WINDOW,
)
from poolhouse.isolation.profile import search_path
from tests.isolation_kit import world

pytest_plugins = ["tests.sandbox_kit"]
HOSTILE = {"GH_TOKEN": "ghp_not_a_real_token", "SSH_AUTH_SOCK": "/tmp/agent.sock", "LAUNCHER_MARKER": "marker-value-1234",
           "ANTHROPIC_API_KEY": "sk-not-real", "OPENAI_API_KEY": "sk-not-real", "AWS_SECRET_ACCESS_KEY": "not-real",
           "POOLHOUSE_WORKSPACE_TOKEN": "not-a-token", "POOLHOUSE_HOME": "/somewhere", "LD_PRELOAD": "/x.so", "DYLD_INSERT_LIBRARIES": "/x.dylib"}


@pytest.fixture
def hostile(monkeypatch):
    for name, value in HOSTILE.items():
        monkeypatch.setenv(name, value)


def made(harness: str, **changes):
    return environment(Inputs(harness, "/agent/home", path=("/agent/home/bin", "/usr/bin"), base_url="http://127.0.0.1:9/", token="bridge-token",
                              alias="CANNED-NOT-A-MODEL", **changes))


def test_the_environment_is_exactly_the_allow_list(hostile, tmp_path):
    expected = {
        "claude": {*ALWAYS, "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_TMPDIR", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", *MODEL_VARS, *OFFLINE,
                   *SHELL_BOUNDS},
        "codex": {*ALWAYS, "CODEX_HOME", "POOLHOUSE_API_KEY"},
        "pi": {*ALWAYS, "PI_CODING_AGENT_DIR", "PI_OFFLINE", "POOLHOUSE_NONINTERACTIVE"},
    }
    for harness, names in expected.items():
        env = made(harness)
        assert set(env) == names, harness
        assert not set(HOSTILE) & set(env) and not set(HOSTILE.values()) & set(env.values()), "nothing of the launcher's reaches the harness"
        assert env["HOME"] == "/agent/home" and env["PATH"] == "/agent/home/bin:/usr/bin" and env["POOLHOUSE_AGENT"] == "1"
    claude = made("claude", context=200000, interactive=True, terminal={"TERM": "xterm", "COLORTERM": "truecolor", "NO_COLOR": "1", "SHELL": "/the/persons/shell"},
                  extras={"CLAUDE_CODE_EXTRA_BODY": "{}", "CLAUDE_CODE_EFFORT_LEVEL": "low", "GH_TOKEN": "x"})
    assert set(claude) == {*expected["claude"], *INTERACTIVE, *WINDOW, "BASH_MAX_OUTPUT_LENGTH", "CLAUDE_CODE_EXTRA_BODY", "CLAUDE_CODE_EFFORT_LEVEL"}
    assert claude["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9" and claude["ANTHROPIC_AUTH_TOKEN"] == "bridge-token"
    assert claude["SHELL"] == SHELL and "GH_TOKEN" not in claude, "the shell is the launcher's choice, never the person's"
    assert set(made("claude", context=1000)) - expected["claude"] == {"CLAUDE_CODE_MAX_CONTEXT_TOKENS", "BASH_MAX_OUTPUT_LENGTH"}, "below the compaction window the caps"
    assert allowed("claude") == frozenset(expected["claude"] | set(BY_HARNESS["claude"])) and "TERM" in allowed("pi", True) and "TERM" not in allowed("pi")
    with pytest.raises(ValueError, match="no coding harness"):
        allowed("emacs")


def test_a_toolchain_directory_whose_files_name_a_variable_adds_a_path_entry_and_no_variable(tmp_path):
    version = tmp_path / "3.99"
    (version / "bin").mkdir(parents=True)
    (version / "pyvenv.cfg").write_text("SSH_AUTH_SOCK=/tmp/agent.sock\nGH_TOKEN=x\nhome = /x\n")
    (version / "bin" / "activate").write_text("export GH_TOKEN=x\nexport SSH_AUTH_SOCK=/s\n")
    plain = environment(Inputs("claude", "/agent/home", path=("/usr/bin",), base_url="http://127.0.0.1:9", token="t", alias="m"))
    granted = environment(Inputs("claude", "/agent/home", path=(str(version / "bin"), "/usr/bin"), base_url="http://127.0.0.1:9", token="t", alias="m"))
    assert set(granted) == set(plain) and {name for name in granted if granted[name] != plain[name]} == {"PATH"}
    assert granted["PATH"].startswith(str(version / "bin"))


def test_the_confined_probe_sees_exactly_the_allow_list_whatever_the_launcher_holds(hostile, tmp_path):
    with world(tmp_path) as w:
        done = proof.run(w.profile, w.services, lan=False, budget=20.0)
        line = next(each for each in done.lines if each.id == "P19")
        assert line.ok and line.guarantee == "G8" and line.must == "exactly the allow-list"
        assert set(w.profile.policy.env) <= allowed("claude") and not set(w.profile.policy.env) & set(HOSTILE)
        assert os.environ["GH_TOKEN"] == HOSTILE["GH_TOKEN"], "the launcher's own environment is untouched"
    assert Path(tmp_path).exists()


def test_the_boxs_shell_runs_quietly_with_its_bounds_and_its_git_and_python_run(tmp_path, seatbelt):
    """A real login shell in the real confinement, with the environment and the ``PATH`` a launch builds."""
    with world(tmp_path) as w:
        env = environment(Inputs("claude", w.spec.home, path=search_path(w.spec), base_url=f"http://127.0.0.1:{w.port}", token="t",
                                 alias="CANNED-NOT-A-MODEL", context=8192))
        line = ('echo "$CLAUDE_CODE_DISABLE_BACKGROUND_TASKS $BASH_DEFAULT_TIMEOUT_MS $BASH_MAX_TIMEOUT_MS $BASH_MAX_OUTPUT_LENGTH"\n'
                'git init -q probe && git -C probe status --short && echo git-ran\npython3 -c "print(\'python-ran\')"\n')
        done = sandbox.run([env["SHELL"], "-c", "-l", line], build(w.spec, (), env).policy, cwd=w.spec.stage, diagnose="never")
    assert done.stdout.splitlines() == ["1 180000 600000 8192", "git-ran", "python-ran"] and not done.stderr, (done.stdout, done.stderr)


def test_the_box_writes_no_bytecode():
    for harness in ("claude", "codex", "pi", "opencode"):
        made = environment(Inputs(harness, "/agent/home", path=("/usr/bin",), base_url="http://127.0.0.1:9", token="t", alias="m"))
        assert made["PYTHONDONTWRITEBYTECODE"] == "1", harness

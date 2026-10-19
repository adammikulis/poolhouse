"""A session retires its own identity when it ends for good, and a terminal starts as many sessions as it needs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse.board import client as board_client, session as board_session
from poolhouse.net import git

pytest_plugins = ["node_kit"]
ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "scripts/hooks"


@pytest.fixture
def project(tmp_path, monkeypatch, workspace_node):
    """A git project the hooks run in, and this tree's workspace command first on the path."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "poolhouse-workspace").write_text(f"#!{sys.executable}\nimport sys\nsys.argv[0] = 'poolhouse-workspace'\n"
                                              "from poolhouse.workspace.cli import main\nsys.exit(main())\n")
    (shim / "poolhouse-workspace").chmod(0o700)
    monkeypatch.setenv("PATH", os.pathsep.join([str(shim), str(Path(sys.executable).parent), str(Path(shutil.which("git")).parent)]))
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    repository = tmp_path / "project"
    git.run(["init", "-b", "development", str(repository)])
    git.run(["-c", "user.name=Hook fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m", "fixture"],
            cwd=repository)
    monkeypatch.chdir(repository)
    return repository


def hook(name: str, **event) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(HOOKS / name)], input=json.dumps(event), text=True, capture_output=True,
                          timeout=60, check=False)


def start(session: str) -> subprocess.CompletedProcess[str]:
    return hook("claude-session-start", model="claude-sonnet-5-5", session_id=session)


def end(session: str, reason: str = "other", **more) -> subprocess.CompletedProcess[str]:
    return hook("claude-session-end", hook_event_name="SessionEnd", session_id=session, reason=reason, **more)


def listed(node, watcher) -> dict:
    return {a.name: a for a in node.session(watcher).agents(retired=True)}


def test_a_session_that_ends_retires_itself_and_one_that_goes_on_under_clear_or_resume_does_not(workspace_node, project):
    watcher = workspace_node.member("watcher")
    names = {}
    for session in ("ends", "clears", "resumes", "subagent-ends"):
        assert start(session).returncode == 0
        names[session] = board_session.find("claude-code", session, client=workspace_node.client)
        assert names[session]
    assert end("clears", "clear").returncode == 0 and end("resumes", "resume").returncode == 0
    assert end("subagent-ends", agent_id="child").returncode == 0, "an event of a subagent retires nothing"
    gone = end("ends", "prompt_input_exit")
    assert gone.returncode == 0 and not gone.stderr
    rows = listed(workspace_node, watcher)
    assert rows[names["ends"]].retired and rows[names["ends"]].retired_reason == "done"
    for session in ("clears", "resumes", "subagent-ends"):
        assert not rows[names[session]].retired, session
    assert workspace_node.cli("whoami", who=workspace_node.adopt(names["clears"])).returncode == 0


def test_a_retired_session_has_no_token_and_resuming_it_from_its_terminal_brings_it_back(workspace_node, project):
    watcher = workspace_node.member("watcher")
    assert start("worker").returncode == 0
    name = board_session.find("claude-code", "worker", client=workspace_node.client)
    token = workspace_node.adopt(name).token
    assert end("worker").returncode == 0
    with pytest.raises(board_client.Denied):
        board_session.Session(workspace_node.client, workspace_node.board, token, name).whoami()
    assert workspace_node.cli("whoami", env={"POOLHOUSE_WORKSPACE_AGENT": name}).returncode != 0, "its token file is gone too"
    again = start("worker")
    assert again.returncode == 0 and not again.stderr
    assert board_session.find("claude-code", "worker", client=workspace_node.client) == name
    assert not listed(workspace_node, watcher)[name].retired
    assert workspace_node.adopt(name).token != token


def test_a_session_that_ends_twice_or_is_unknown_does_not_fail_the_hook(workspace_node, project):
    assert start("once").returncode == 0
    assert end("once").returncode == 0
    twice = end("once")
    assert twice.returncode == 0
    never = end("never-started")
    assert never.returncode == 0 and not never.stderr, "a session the board never named has nothing to retire"
    assert hook("claude-session-end").returncode == 0


def test_a_terminal_starts_as_many_sessions_as_it_needs_without_ending_any(workspace_node, project):
    for index in range(12):
        started = start(f"worker-{index}")
        assert started.returncode == 0 and not started.stderr
        assert board_session.find("claude-code", f"worker-{index}", client=workspace_node.client)

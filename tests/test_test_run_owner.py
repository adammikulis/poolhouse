"""A test run always has an owner: the workspace identity of its caller, resolved from the native session when no name is set, or a person's terminal; an agent with no identity is refused before the queue."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from poolhouse.workspace import testboard

pytest_plugins = ["node_kit"]
ROOT = Path(__file__).resolve().parents[1]
BARE = ("PATH", "HOME", "TMPDIR", "LANG")


def run_test_script(tmp_path: Path, **markers: str) -> subprocess.CompletedProcess:
    """`scripts/test all` on one file from a process that has only ``markers`` of an agent harness."""
    env = {name: os.environ[name] for name in BARE if name in os.environ}
    env.update(PYTHONPATH=str(ROOT / "src"), POOLHOUSE_HOME=str(tmp_path / "home"), **markers)
    return subprocess.run([sys.executable, str(ROOT / "scripts/test"), "all", "tests/test_queue_doctor.py"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120, check=False)


def test_an_agent_run_with_no_workspace_identity_is_refused_before_it_is_queued(tmp_path):
    done = run_test_script(tmp_path, CLAUDECODE="1", CLAUDE_CODE_SESSION_ID="a-native-session-no-board-knows")
    assert done.returncode == 4 and "no workspace identity, so it is not queued" in done.stderr, done.stderr
    assert "queued" not in done.stdout


def test_a_run_started_by_a_native_session_is_owned_by_the_session_the_board_named_for_it(workspace_node, monkeypatch):
    lead = workspace_node.member("native-lead")
    monkeypatch.delenv("POOLHOUSE_WORKSPACE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "native-lead")
    acting = testboard.acting()
    assert acting is not None and acting.name == lead.name
    assert testboard.identity(acting)["id"] == lead.name


def test_a_native_session_the_board_does_not_know_gives_no_identity(workspace_node, monkeypatch):
    monkeypatch.delenv("POOLHOUSE_WORKSPACE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "never-registered")
    assert testboard.acting() is None and testboard.agent_process()


def test_a_process_no_harness_started_is_a_persons_terminal(monkeypatch):
    for name in testboard.AGENT_MARKERS:
        monkeypatch.delenv(name, raising=False)
    assert not testboard.agent_process() and testboard.acting() is None

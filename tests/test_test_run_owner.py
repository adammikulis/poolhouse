"""Test jobs record their workspace session or the local process that started them."""

from __future__ import annotations

import json
import os
import runpy
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
    env.update(PYTHONPATH=str(ROOT / "src"), POOLHOUSE_HOME=str(tmp_path / "home"),
               DEV_TEST_SLOTS_DIR=str(tmp_path / "slots"), DEV_TEST_REUSE_DIR=str(tmp_path / "reuse"), **markers)
    return subprocess.run([sys.executable, str(ROOT / "scripts/test"), "all", "tests/test_queue_doctor.py"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120, check=False)


def test_an_agent_run_with_no_workspace_identity_runs_through_the_local_queue(tmp_path):
    done = run_test_script(tmp_path, CLAUDECODE="1", CLAUDE_CODE_SESSION_ID="a-native-session-no-board-knows")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "passed" in done.stdout
    specs = [json.loads(path.read_text()) for path in (tmp_path / "reuse").rglob("spec.json")]
    assert any(spec.get("owner", {}).get("source") == "local-process" and spec["owner"]["id"] == ""
               for spec in specs)


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


def test_workspace_failure_warning_redacts_environment_credentials(monkeypatch, capsys):
    secret = "test-secret-value-for-redaction-92831"
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_TOKEN", secret)

    def fail():
        raise OSError(f"connection failed for {secret}")

    assert testboard.guarded("session", fail) is None
    warning = capsys.readouterr().err
    assert "connection failed" in warning and secret not in warning


def test_a_missing_session_clears_the_previous_run_identity(monkeypatch):
    script = runpy.run_path(str(ROOT / "scripts/test"), run_name="test_runner_module")
    monkeypatch.setenv("DEV_TEST_AGENT", "stale")
    script["REUSE"]["agent"] = {"id": "old-session", "source": "workspace-session"}
    script["REUSE"]["events"] = object()
    script["set_session"](None, "")
    assert script["REUSE"]["agent"]["id"] == ""
    assert type(script["REUSE"]["events"]) is testboard.Events
    assert "DEV_TEST_AGENT" not in os.environ


def test_a_process_no_harness_started_is_a_persons_terminal(monkeypatch):
    for name in testboard.AGENT_MARKERS:
        monkeypatch.delenv(name, raising=False)
    assert not testboard.agent_process() and testboard.acting() is None

"""The suite clears agent markers for every test, and the automatic floor still refuses a marker a test sets."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from testagentenv_pytest import AGENT_VARIABLES

from poolhouse import caller
from poolhouse.caller import AgentRefused, refuse_agent

HARNESS_SET = ("AI_AGENT", "AGENT", "OPENCODE", "OPENCODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")
"""What a coding tool sets besides poolhouse's own markers for the commands it runs: OpenCode in a delegate's box the first four."""


def test_no_agent_marker_reaches_a_test_by_default():
    assert [name for name in AGENT_VARIABLES if os.environ.get(name)] == []
    assert [name for name in HARNESS_SET if os.environ.get(name)] == []


def test_a_run_the_runner_did_not_start_drops_the_markers_of_whoever_runs_it_too():
    """What a test sees does not depend on who runs the suite, or how: here pytest alone, in an agent's marked shell."""
    marked = {**os.environ, **dict.fromkeys(("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE", *HARNESS_SET), "1")}
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-n", "0", "-p", "no:cacheprovider",
                           f"{__file__}::test_no_agent_marker_reaches_a_test_by_default"], cwd=Path(__file__).parent.parent, env=marked,
                          capture_output=True, text=True, timeout=110, check=False)
    assert done.returncode == 0 and "1 passed" in done.stdout, done.stdout[-3000:] + done.stderr[-3000:]


def test_a_process_with_no_marker_runs_the_action_directly_with_no_terminal_and_the_audit_says_who():
    assert refuse_agent("grant", "it hands out a protected action") is None
    (row,) = caller.audit_rows()
    assert row["event"] == "ran" and row["action"] == "grant" and row["who"].startswith("user ")


@pytest.mark.parametrize("marker", ["CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE"])
def test_the_floor_still_refuses_a_marker_the_test_sets_at_once_with_the_reason(monkeypatch, marker):
    monkeypatch.setenv(marker, "1")
    with pytest.raises(AgentRefused, match=marker) as refused:
        refuse_agent("grant", "it hands out a protected action")
    assert "it hands out a protected action" in str(refused.value) and "outside the agent" in str(refused.value)
    (row,) = caller.audit_rows()
    assert (row["event"], row["action"], row["why"]) == ("refused", "grant", "it hands out a protected action")
    assert f"agent {marker}" in row["who"]

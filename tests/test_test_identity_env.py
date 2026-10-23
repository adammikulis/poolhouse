"""A test starts with no session and no board named by the shell that ran the suite."""

import os

from poolhouse.board import session

NAMES = ("POOLHOUSE_BOARD", "POOLHOUSE_WORKSPACE_AGENT", "POOLHOUSE_WORKSPACE_TOKEN", "POOLHOUSE_SUBAGENT",
         "POOLHOUSE_SESSION_HARNESS", "POOLHOUSE_SESSION_ID", "CLAUDE_CODE_SESSION_ID")


def test_the_board_and_session_of_the_agent_that_runs_the_suite_do_not_reach_a_test():
    leaked = [name for name in session.IDENTITY_ENV if name in os.environ]
    assert not leaked, f"the shell's {leaked} reached a test: a hook or a command under test would act as that session"


def test_every_variable_that_names_a_session_or_a_board_is_one_the_suite_clears():
    assert set(NAMES) <= set(session.IDENTITY_ENV)

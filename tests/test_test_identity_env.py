"""A test starts with no session and no board named by the shell that ran the suite."""

import os

from poolhouse.board import place, session
from poolhouse.testing.identity import IDENTITY_ENV


def test_the_board_and_session_of_the_agent_that_runs_the_suite_do_not_reach_a_test():
    leaked = [name for name in IDENTITY_ENV if name in os.environ]
    assert not leaked, f"the shell's {leaked} reached a test: a hook or a command under test would act as that session"


def test_every_variable_the_lookup_code_reads_to_name_a_session_or_a_board_is_one_the_suite_clears():
    read = {place.BOARD_ENV, session.AGENT_ENV, session.SUBAGENT_ENV, session.TOKEN_ENV,
            *(name for names in session.NATIVE_SESSIONS for name in names if name)}
    assert read <= set(IDENTITY_ENV), sorted(read - set(IDENTITY_ENV))

"""The environment variables by which a process names the board it works on or the session it acts as.

A test run clears them all (``tests/conftest.py``): the agent that runs the suite is itself a session on a board and its shell exports
them, so left in place they decide which board a hook resolves and whose token it looks for, and a rig that registers no session was
denied only in an agent's shell. ``tests/test_test_identity_env.py`` requires every name the lookup code reads (``board.place``,
``board.session``) to be in the list.
"""

__all__ = ["IDENTITY_ENV"]

IDENTITY_ENV = ("POOLHOUSE_BOARD", "POOLHOUSE_WORKSPACE_AGENT", "POOLHOUSE_SUBAGENT", "POOLHOUSE_WORKSPACE_TOKEN",
                "POOLHOUSE_SESSION_HARNESS", "POOLHOUSE_SESSION_ID", "CLAUDE_CODE_SESSION_ID")

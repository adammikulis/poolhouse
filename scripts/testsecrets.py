"""What a test run never shows: a credential it inherited, or the environment.

A long pytest traceback prints the arguments of every frame, so one failed native build printed the whole environment, a harness's
messaging token with it. A test needs no credential of the shell that started it, so the run drops each one before the first test
(no failure, assertion, captured output or child process can then show it), prints no frame's arguments or locals, and gives
``os.environ`` a repr without values.
"""

from __future__ import annotations

import os

from poolhouse.credentials.environment import child_environment

RUN_SETTINGS = ("DEV_TEST_", "GIT_AUTHOR_", "POOLHOUSE_AUTHORITY_FLOOR")
"""Prefixes of names that read like a credential's and are not the shell's secrets: the run's own admission tokens, a commit's
author, the authority floor."""


def withhold_credentials() -> None:
    """Remove from this process's environment, and so from every child it starts, each variable named like a credential."""
    kept = child_environment()
    for name in [name for name in os.environ if name not in kept and not name.startswith(RUN_SETTINGS)]:
        del os.environ[name]


def hide_environment(option) -> None:
    """Whatever the command line asked of ``option``, print no traceback that shows arguments or locals, and no value of
    ``os.environ`` in the repr an assertion about it shows."""
    if option.tbstyle in ("auto", "long"):
        option.tbstyle = "short"
    option.showlocals = False
    option.fulltrace = False
    type(os.environ).__repr__ = lambda environ: f"environ(<{len(environ)} variables, values not shown>)"

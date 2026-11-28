"""What a test run never shows: a credential it inherited; and unless its command line asks, the environment.

A long pytest traceback prints the arguments of every frame, so one failed native build printed the whole environment, a harness's
messaging token with it. A test needs no credential of the shell that started it, so ``scripts/test`` starts pytest without any and
``tests/conftest.py`` drops each one before the first test: no failure, assertion, captured output or child process can then show
it. The default traceback is the short one, which shows no frame's arguments, and ``os.environ`` has a repr without values.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from poolhouse.credentials.environment import child_environment

MORE_NAMES = re.compile(r"PASSPHRASE|SIGNING_?KEY|_PAT$|_PWD$", re.IGNORECASE)
"""Names of credentials that ``child_environment`` does not know."""
ADDRESS_NAME = re.compile(r"_(?:URL|URI|DSN)S?$", re.IGNORECASE)
"""Variables that name a service by its address."""
ADDRESS_PASSWORD = re.compile(r"(?:^|://)[^/?#\s:@]*:[^/?#\s]+@")
RUN_PREFIXES = ("DEV_TEST_", "GIT_AUTHOR_")
"""The run's own admission tokens, and a commit's author."""
RUN_NAMES = ("POOLHOUSE_AUTHORITY_FLOOR", "POOLHOUSE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "OPENCODE_SESSION_ID")
"""Names that read like a credential's and hold none: the authority floor, and the session names that mark a run as an agent's
(``poolhouse.caller`` and ``testscope`` refuse an agent some tests by them)."""


def build_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """``environment`` as a build whose output is printed may hold it: no variable named like a credential, and none that names an
    address carrying a password. A proxy's stays, since the build reaches the network through it."""
    return {name: value for name, value in child_environment(source=environment).items()
            if not MORE_NAMES.search(name) and not (ADDRESS_NAME.search(name) and ADDRESS_PASSWORD.search(value))}


def without_credentials(environment: Mapping[str, str]) -> dict[str, str]:
    """``environment`` as a test run may hold it: a build's, without a proxy that carries a password too (a test reaches no
    network), and with the run's own settings."""
    kept = build_environment(environment)
    return {name: value for name, value in environment.items()
            if name.startswith(RUN_PREFIXES) or name in RUN_NAMES
            or (name in kept and not (name.upper().endswith("_PROXY") and ADDRESS_PASSWORD.search(value)))}


def withhold_credentials() -> None:
    """Remove from this process's environment, and so from every child it starts, what ``without_credentials`` leaves out."""
    kept = without_credentials(os.environ)
    for name in [name for name in os.environ if name not in kept]:
        del os.environ[name]


def hide_environment(option) -> None:
    """Where the command line behind ``option`` chose no traceback style, print the short one; give ``os.environ``, which an
    assertion about it shows, a repr without values."""
    if getattr(option, "tbstyle", "auto") == "auto":   # absent without the terminal reporter, and then read as "auto"
        option.tbstyle = "short"
    type(os.environ).__repr__ = lambda environ: f"environ(<{len(environ)} variables, values not shown>)"

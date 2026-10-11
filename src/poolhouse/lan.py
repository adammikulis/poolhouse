"""Whether a node or daemon on this machine may listen on the network.

Safe by default: the network is allowed only for the installed software running on the account's real
state root. A process under a moved state root (a worktree, a rig, a test's temporary home) or under
pytest listens on loopback alone, and a missing variable never means "open". The variables only narrow
(``POOLHOUSE_NODE_LAN=off``) or let a person at the machine run the tests that listen on every interface
(``POOLHOUSE_LAN_TESTS=1``, never from an agent).
"""

from __future__ import annotations

import os
import sys

from poolhouse import home
from poolhouse.person import AGENT_MARKERS

OFF_ENV = "POOLHOUSE_NODE_LAN"
TESTS_ENV = "POOLHOUSE_LAN_TESTS"
LAN_FLAG = "--lan"


def under_test() -> bool:
    """Whether this process is pytest, or a child of a running test."""
    return "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ


def on_real_home() -> bool:
    """Whether the state root is the account's own default one (not moved by POOLHOUSE_HOME or a redirected HOME)."""
    return home.home().resolve() == home.account_roots()[0]


def on_the_persons_machine() -> bool:
    """Whether this process is the installed software on the account's real state root, not a test or a rig: the only place
    a dialog, a window or the network may be real."""
    return on_real_home() and not under_test()


def allowed() -> bool:
    """Whether this process may open a listening socket beyond loopback or join the beacon."""
    if os.environ.get(OFF_ENV) == "off":
        return False
    if os.environ.get(TESTS_ENV) == "1":
        return not any(os.environ.get(marker) for marker in AGENT_MARKERS)
    return on_the_persons_machine()


def clamp(args: list[str]) -> list[str]:
    """``args`` without the LAN flag when the network is not allowed here."""
    return args if allowed() else [word for word in args if word != LAN_FLAG]

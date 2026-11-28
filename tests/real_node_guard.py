"""A test process never reaches the account's own node.

Installed when `network_guard`, which the suite's conftest imports, first imports this, and never removed, so no fixture of any scope, no
teardown and no thread that outlives a test gets there, whatever ``HOME`` or ``POOLHOUSE_HOME`` say by then. A child process a test
starts is not covered: it is kept away by the environment it is given.
"""

from __future__ import annotations

import errno
import os
import socket

from poolhouse.home import account_roots

REFUSED = [os.path.realpath(account_roots()[0])]
"""The state roots whose sockets no test connects to."""


def inside(address: object) -> bool:
    """Whether a socket address names a file under a refused state root."""
    if not isinstance(address, str | bytes) or not address:
        return False
    path = os.path.realpath(os.fsdecode(address))
    return any(path.startswith(root + os.sep) for root in REFUSED)


def _refusing(real):
    def reach(self, address):
        if inside(address):
            raise PermissionError(errno.EACCES, f"a test may not connect to the account's own Poolhouse ({address})")
        return real(self, address)

    return reach


def install() -> None:
    """Refuse such an address on every socket of this process from now on."""
    for name in ("connect", "connect_ex"):
        setattr(socket.socket, name, _refusing(getattr(socket.socket, name)))


install()   # once, when first imported: before the first fixture and after the last

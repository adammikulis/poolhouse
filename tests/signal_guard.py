"""One test's change to the SIGTERM and SIGINT handlers is undone before the next test starts.

An entry point that serves until it is stopped hooks SIGTERM for the rest of its process (a
daemon turns it into ``KeyboardInterrupt``). A test that calls one in-process leaves that
handler behind, and the next test that sends SIGTERM to its own process interrupts the whole run
instead of the call it meant. The fixture is imported by ``conftest.py`` and applies to every test.
"""

from __future__ import annotations

import contextlib
import signal
from collections.abc import Iterator

import pytest

SIGNALS = tuple(getattr(signal, name) for name in ("SIGTERM", "SIGINT", "SIGBREAK") if hasattr(signal, name))


@contextlib.contextmanager
def handlers_restored() -> Iterator[None]:
    """Put the SIGTERM, SIGINT and SIGBREAK handlers back as they were on entry."""
    before = {number: signal.getsignal(number) for number in SIGNALS}
    try:
        yield
    finally:
        for number, handler in before.items():
            if handler is not None and signal.getsignal(number) != handler:
                signal.signal(number, handler)


@pytest.fixture(autouse=True)
def signal_handlers_are_restored():
    """Run the test, then restore the termination handlers it found."""
    with handlers_restored():
        yield

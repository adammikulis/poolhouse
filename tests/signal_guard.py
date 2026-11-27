"""What signals do to a test process stays inside the test they hit.

An entry point that serves until it is stopped hooks SIGTERM for the rest of its process (a
daemon turns it into ``KeyboardInterrupt``). A test that calls one in-process leaves that
handler behind, and the next test that sends SIGTERM to its own process interrupts the whole run
instead of the call it meant. The fixture is imported by ``conftest.py`` and applies to every test.

The test timeout is a signal too. Raised while Playwright's dispatcher greenlet runs, it ends that greenlet, and the sync API's wait
(``while not task.done(): dispatcher.switch()``) then switches to a dead greenlet for ever: the worker spins at a full core, the timeout is
spent and the run never ends. Importing this module makes such a switch raise.
"""

from __future__ import annotations

import contextlib
import signal
from collections.abc import Iterator

import pytest

SIGNALS = tuple(getattr(signal, name) for name in ("SIGTERM", "SIGINT", "SIGBREAK") if hasattr(signal, name))
STOPPED = "the Playwright connection of this process stopped (a test timeout interrupted it, or its driver exited)"


def stopped_playwright_raises() -> None:
    """Make a switch to a Playwright dispatcher that has ended raise `STOPPED`; nothing without Playwright."""
    try:
        from playwright._impl._errors import Error
        from playwright._impl._greenlets import MainGreenlet
    except ImportError:
        return
    switch = MainGreenlet.switch

    def live(self, *args, **kwargs):
        if self.dead:
            raise Error(STOPPED)
        return switch(self, *args, **kwargs)

    MainGreenlet.switch = live


stopped_playwright_raises()   # once, when conftest first imports this: before any Playwright connection exists


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

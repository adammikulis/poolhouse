"""Pytest plugin: a test that its own timeout does not end is named, shown with every thread's stack, and ends its test process.

`pytest-timeout` fails a test by raising in it once. A test that swallows that, a fixture that hangs while the failed test is torn down,
or a call that holds the interpreter lock goes on. This bounds the whole of a test, from setup to teardown: past the limit a thread writes
the test's name to the run's own standard error, ends the processes the test process started and exits it, which under xdist fails that
test as its worker's crash and the run goes on. When no Python thread can run, `faulthandler` writes the stacks and exits.
`testslots_pytest` registers it, so the wait for a CPU permit is outside the bound.
"""
from __future__ import annotations

import contextlib
import faulthandler
import os
import threading
import time
from collections.abc import Iterator

import psutil
import pytest

GRACE_ENV = "DEV_TEST_HANG_GRACE_S"
GRACE_S = 120.0
"""How long past a test's own timeout the test and its teardown may still take: the length of the default test timeout again."""
BACKSTOP_S = 30.0
"""How long after the limit `faulthandler`, which needs no interpreter lock, acts if the watching thread has not."""
HUNG_STATUS = 70
STILL_ENV = "DEV_TEST_STILL_S"
STILL_S = 60.0
"""How often the tests that have run at least this long are named: half the default test timeout, so a slow test is named before it fails."""
REPORT = pytest.StashKey[int]()
"""A duplicate of standard error as it is outside a test, which no capture replaces."""


def timeout_of(item) -> tuple[float, bool]:
    """The timeout `pytest-timeout` applies to ``item`` (0 for none) and whether it times the test function alone."""
    marker = item.get_closest_marker("timeout")
    given = item.config.getoption("timeout", None)
    func_only = str(item.config.getini("timeout_func_only")).lower() == "true"
    if marker is not None:
        func_only = marker.kwargs.get("func_only", func_only)
        if marker.args or "timeout" in marker.kwargs:
            given = marker.args[0] if marker.args else marker.kwargs["timeout"]
    if given is None:
        given = os.environ.get("PYTEST_TIMEOUT") or item.config.getini("timeout")
    return float(given or 0), bool(func_only)


def limit(item) -> float | None:
    """The seconds ``item`` may take from setup to teardown, or None for a test with no timeout. A timeout on the function alone leaves its
    fixtures the longest a native build may take."""
    seconds, func_only = timeout_of(item)
    if seconds <= 0:
        return None
    if func_only:
        import testnode  # loads library modules, which a run without such a test does not need

        seconds += testnode.BUILD_TIMEOUT_S
    return seconds + float(os.environ.get(GRACE_ENV) or GRACE_S)


def expire(nodeid: str, seconds: float, report: int) -> None:
    """Name the hung test with every thread's stack on ``report``, end what this process started and exit it."""
    os.write(report, f"\ntest: HUNG: {nodeid} had not ended {seconds:.0f} s after it began, past its own timeout; "
                     f"ending this test process\n".encode(errors="replace"))
    faulthandler.dump_traceback(file=report, all_threads=True)
    with contextlib.suppress(psutil.Error):
        for child in psutil.Process().children(recursive=True):
            with contextlib.suppress(psutil.Error):
                child.kill()
    os._exit(HUNG_STATUS)


@contextlib.contextmanager
def watched(nodeid: str, seconds: float, report: int) -> Iterator[None]:
    """Bound the block that runs the test ``nodeid`` to ``seconds``."""
    timer = threading.Timer(seconds, expire, (nodeid, seconds, report))
    timer.daemon = True
    timer.start()
    faulthandler.dump_traceback_later(seconds + BACKSTOP_S, exit=True, file=report)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()
        timer.cancel()


class Live:
    """In the process that prints the run's progress: names a test the moment its timeout fails it, and every `STILL_S` the tests that have
    been running that long, so a run that prints no more progress says what it is on."""

    def __init__(self, report: int, every: float) -> None:
        self.report, self.every = report, every
        self.running: dict[str, float] = {}
        self.guard = threading.Lock()
        self.ended = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True, name="testhang-live")
        self.thread.start()

    def say(self, text: str) -> None:
        with contextlib.suppress(OSError):
            os.write(self.report, f"\ntest: {text}\n".encode(errors="replace"))

    def _watch(self) -> None:
        while not self.ended.wait(self.every):
            now = time.monotonic()
            with self.guard:
                slow = [(now - began, nodeid) for nodeid, began in self.running.items() if now - began >= self.every]
            for seconds, nodeid in sorted(slow, reverse=True):
                self.say(f"still running after {seconds:.0f} s: {nodeid}")

    def pytest_runtest_logstart(self, nodeid) -> None:
        with self.guard:
            self.running[nodeid] = time.monotonic()

    def pytest_runtest_logfinish(self, nodeid) -> None:
        with self.guard:
            self.running.pop(nodeid, None)

    def pytest_runtest_logreport(self, report) -> None:
        raised = getattr(getattr(report.longrepr, "reprcrash", None), "message", "")
        if report.failed and raised.startswith("Failed: Timeout (>") and raised.endswith("from pytest-timeout."):
            self.say(f"TIMED OUT in {report.when}: {report.nodeid}")

    @pytest.hookimpl(optionalhook=True)
    def pytest_handlecrashitem(self, crashitem) -> None:
        self.pytest_runtest_logfinish(crashitem)

    def pytest_unconfigure(self) -> None:
        self.ended.set()
        self.thread.join(5)


def pytest_configure(config):
    config.stash[REPORT] = os.dup(2)
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(Live(config.stash[REPORT], float(os.environ.get(STILL_ENV) or STILL_S)), "testhang-live")


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    if REPORT in config.stash:
        os.close(config.stash[REPORT])
        del config.stash[REPORT]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    seconds = limit(item)
    if seconds is None or REPORT not in item.config.stash:
        yield
        return
    with watched(item.nodeid, seconds, item.config.stash[REPORT]):
        yield

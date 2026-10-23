"""Pytest collection handoff and per-test shared CPU admission."""
from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path

import pytest
import testslots
import testslots_rpc
from test_kernel_endpoint import AdmissionUnreachable

UNADMITTED_STATUS = 4
NOT_STARTED_STATUS = 75
_waited = [0.0]
_expired = []
WAIT_PROPERTY = "admission_wait"
"""The user property that carries the seconds a test waited for CPU permits to the controller."""
NOT_STARTED_PROPERTY = "not_started"
"""The user property that carries why a test never got its permit; the controller lists it apart."""


class NotStarted(Exception):
    """A test's wait for a permit ended at the wait limit, or an earlier test's did and this one did not ask."""

    def __init__(self, waited: float, limit: float, asked: bool = True) -> None:
        self.waited = waited
        self.reason = (f"NOT STARTED, machine overloaded: waited {waited:.0f} s for a permit (DEV_TEST_WAIT_S limit {limit:.0f} s)"
                       if asked else "NOT STARTED, machine overloaded: an earlier test of this process waited out the permit limit "
                       f"({limit:.0f} s, DEV_TEST_WAIT_S) and this one did not ask")
        super().__init__(self.reason)


def take_waited() -> float:
    """The seconds spent waiting for admission since the last call."""
    seconds, _waited[0] = _waited[0], 0.0
    return seconds


@contextlib.contextmanager
def slot(**fields):
    """Admission for one stretch of work. A server that cannot be reached stops the run with status 4: work that did not wait
    for its turn shares the machine with other runs, and its timings and its stored passes are not a valid run's."""
    limit = testslots.wait_limit()
    if _expired:
        raise NotStarted(0.0, limit, asked=False)
    manager = testslots_rpc.request("acquire", **fields)
    began = time.monotonic()
    try:
        manager.__enter__()
        _waited[0] += time.monotonic() - began
    except AdmissionUnreachable as exc:
        pytest.exit(f"testslots: {exc}; the run was not admitted and stops", returncode=UNADMITTED_STATUS)
    except (testslots_rpc.AdmissionWaitExpired, TimeoutError):
        _expired.append(time.monotonic() - began)
        raise NotStarted(_expired[-1], limit) from None
    try:
        yield
    except BaseException:
        if not manager.__exit__(*sys.exc_info()):
            raise
    else:
        manager.__exit__(None, None, None)


def _ready(operation: str) -> None:
    with testslots_rpc.request(operation):
        pass


def pytest_load_initial_conftests(early_config):
    if os.environ.get("PYTEST_XDIST_WORKER"):
        context = slot(label="pytest worker bootstrap", phase="collection")
        context.__enter__()
        early_config._testslots_bootstrap = context


def _release_bootstrap(config) -> None:
    context = getattr(config, "_testslots_bootstrap", None)
    if context is not None:
        config._testslots_bootstrap = None
        context.__exit__(None, None, None)


def pytest_unconfigure(config):
    _release_bootstrap(config)


def pytest_configure(config):
    if "DEV_TEST_PYTEST_ENDPOINT" not in os.environ:
        raise pytest.UsageError("testslots_pytest requires the testslots supervisor")
    if not hasattr(config, "workerinput"):
        workers = getattr(config.option, "numprocesses", 0) or 0
        if not isinstance(workers, int) or workers > int(os.environ["DEV_TEST_WORKERS"]):
            raise pytest.UsageError("xdist workers exceed the admitted pool maximum")
        config._testslots_collected = set()
        config.pluginmanager.register(Controller(), "testslots-controller")
        _ready("ready")


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids):
    config = node.config
    config._testslots_collected.add(node.gateway.id)
    if len(config._testslots_collected) == config.option.numprocesses:
        _ready("collected")


@pytest.hookimpl(hookwrapper=True)
def pytest_collection(session):
    if getattr(session.config, "_testslots_bootstrap", None) is not None:
        yield
    else:
        with slot(label="pytest collection", phase="collection"):
            yield


def pytest_collection_finish(session):
    config = session.config
    _release_bootstrap(config)
    if not hasattr(config, "workerinput") and not getattr(config.option, "numprocesses", 0):
        _ready("collected")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    """Take the test's permit before any other wrapper of the protocol runs."""
    heavy = Path(str(item.path)).stem in testslots.HEAVY_MODULES
    permit = contextlib.ExitStack()
    try:
        permit.enter_context(slot(label=item.nodeid[-256:], heavy=heavy))
    except NotStarted as refused:
        item.user_properties.extend([(WAIT_PROPERTY, refused.waited), (NOT_STARTED_PROPERTY, refused.reason)])
        item.add_marker(pytest.mark.skip(reason=refused.reason))
        yield
        return
    with permit:
        item.user_properties.append((WAIT_PROPERTY, take_waited()))
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Name the limit when a test's own timer fires, and the permit wait the timer did not count."""
    outcome = yield
    text = str(call.excinfo.value) if call.excinfo is not None else ""
    if text.startswith("Timeout (>") and text.endswith("from pytest-timeout."):
        waited = next((value for key, value in item.user_properties if key == WAIT_PROPERTY), 0.0)
        outcome.get_result().sections.append(("testslots", f"test timed out after {text[len('Timeout (>'):text.index('s)')]} s running "
                                              f"(the test timeout); it waited {waited:.1f} s for a permit first, which the timeout does not count"))


class Controller:
    """The controller's view of the tests that never got a permit: listed apart, and the run's status when nothing failed."""

    def __init__(self) -> None:
        self.refused: list[tuple[str, str]] = []

    def pytest_runtest_logreport(self, report) -> None:
        if report.when == "setup":
            self.refused.extend((report.nodeid, str(value)) for key, value in report.user_properties if key == NOT_STARTED_PROPERTY)

    def pytest_terminal_summary(self, terminalreporter) -> None:
        if self.refused:
            terminalreporter.section("not started")
            for node, reason in self.refused:
                terminalreporter.write_line(f"{node}  {reason}")

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus) -> None:
        if self.refused and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = NOT_STARTED_STATUS

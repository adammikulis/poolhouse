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
_waited = [0.0]
WAIT_PROPERTY = "admission_wait"
"""The user property that carries the seconds a test waited for CPU permits to the controller."""


def take_waited() -> float:
    """The seconds spent waiting for admission since the last call."""
    seconds, _waited[0] = _waited[0], 0.0
    return seconds


@contextlib.contextmanager
def slot(**fields):
    """Admission for one stretch of work. A server that cannot be reached stops the run with status 4: work that did not wait
    for its turn shares the machine with other runs, and its timings and its stored passes are not a valid run's."""
    manager = testslots_rpc.request("acquire", **fields)
    began = time.monotonic()
    try:
        manager.__enter__()
        _waited[0] += time.monotonic() - began
    except AdmissionUnreachable as exc:
        pytest.exit(f"testslots: {exc}; the run was not admitted and stops", returncode=UNADMITTED_STATUS)
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
    # Outermost, so pytest-timeout's clock starts after the wait for a slot: a test queued behind other runs is not timed out for
    # being queued, and the timeout cannot fire inside the wait and end the whole run with an internal error.
    heavy = Path(str(item.path)).stem in testslots.HEAVY_MODULES
    with slot(label=item.nodeid[-256:], heavy=heavy):
        item.user_properties.append((WAIT_PROPERTY, take_waited()))
        yield

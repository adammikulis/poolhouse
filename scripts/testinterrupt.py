"""Pytest plugin: when SIGTERM or Ctrl-C ends a run, print the test that was running."""

from __future__ import annotations

import signal

import pytest

current = [""]


def pytest_runtest_logstart(nodeid):
    current[0] = nodeid


def announce() -> None:
    print(f"\ntest: interrupted during {current[0]}", flush=True)


def stop(number, frame):
    announce()
    raise KeyboardInterrupt


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    if not hasattr(config, "workerinput"):
        signal.signal(signal.SIGTERM, stop)


def pytest_keyboard_interrupt(excinfo):
    if current[0]:
        announce()

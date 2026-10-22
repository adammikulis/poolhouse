"""The pytest options the test suite adds, shared by tests/conftest.py and the argument parsing in scripts/test."""

from __future__ import annotations

import functools

OPTIONS = (
    ("--slow", "also run the tests marked slow (a browser, a subprocess, a wheel build, a network timeout)"),
    ("--gate", "also run the tests marked gate (whole-tree scans that `scripts/test gate` runs)"),
    ("--redteam", "also run the tests marked redteam (they need the redteam extra)"),
    ("--real-model", "also run the tests marked real_model (a real model on a real llama-server; one missing fails the test)"),
)


def pytest_addoption(parser) -> None:
    """Register the suite's tier options."""
    for flag, help_text in OPTIONS:
        parser.addoption(flag, action="store_true", default=False, help=help_text)


def parse_arguments(arguments: list[str]):
    """A pytest config for ``arguments`` that loads no conftest, so the suite's imports are not paid."""
    import pytest

    return pytest.Config.fromdictargs({}, ["--noconftest", "-p", "testoptions", *arguments])


@functools.cache
def _positional(arguments: tuple[str, ...]) -> tuple[str, ...]:
    config = parse_arguments(list(arguments))
    try:
        return tuple(config.args)
    finally:
        config._ensure_unconfigure()


def positional(arguments: list[str]) -> list[str]:
    """The test paths and node ids among the pytest arguments; the same arguments are parsed once per process."""
    return list(_positional(tuple(arguments)))

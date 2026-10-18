"""The pytest options the test suite adds, shared by tests/conftest.py and the argument parsing in scripts/test."""

from __future__ import annotations

OPTIONS = (
    ("--slow", "also run the tests marked slow (a browser, a subprocess, a wheel build, a network timeout)"),
    ("--gate", "also run the tests marked gate (whole-tree scans that `scripts/test gate` runs)"),
    ("--redteam", "also run the tests marked redteam (they need the redteam extra)"),
)


def pytest_addoption(parser) -> None:
    """Register the suite's tier options."""
    for flag, help_text in OPTIONS:
        parser.addoption(flag, action="store_true", default=False, help=help_text)


def parse_arguments(arguments: list[str]):
    """A pytest config for ``arguments`` that loads no conftest, so the suite's imports are not paid."""
    import pytest

    return pytest.Config.fromdictargs({}, ["--noconftest", "-p", "testoptions", *arguments])

"""The per-test guard for a package an optional extra installs.

A module that needs one names this plugin and marks its tests (``pytest_plugins = ["extras"]``,
``pytestmark = pytest.mark.needs("ladybug", reason="poolhouse[store]")``). Where the package is missing each marked
test is reported as skipped with the reason, so the count of what ran says what was left out; a skip at module level
takes the whole file out of collection and the count falls without a word (the ``module-skips`` budget).
"""

from __future__ import annotations

import importlib

import pytest


def optional(name: str):
    """The module ``name`` when it is installed, else None: for a name a test module uses, which the `needs` mark on
    those tests keeps from being touched when it is None."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def absent(modules: tuple[str, ...]) -> str:
    """The first of ``modules`` that cannot be imported, "" when all can."""
    for name in modules:
        try:
            pytest.importorskip(name)
        except pytest.skip.Exception:
            return name
    return ""


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip the test, naming the package and the extra, when a package its ``needs`` mark names is not installed."""
    for mark in item.iter_markers("needs"):
        missing = absent(tuple(mark.args))
        if missing:
            pytest.skip(f"{missing} is not installed: {mark.kwargs.get('reason', 'an optional extra')}")

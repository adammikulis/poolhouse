"""One test's change to ``os.environ`` is undone before the next test starts.

Several entry points set a marker for the rest of their process (a hook sets
``POOLHOUSE_NONINTERACTIVE``, a worker sets ``POOLHOUSE_AGENT``) because each runs as its own
process. A test that calls one in-process leaves that marker behind, and on a serial run
(``-n 0``) every later test that registers a person sees an agent and is refused. The
fixture is imported by ``conftest.py`` and applies to every test.
"""

from __future__ import annotations

import os

import pytest

from poolhouse.node_supervise import PARENT_ENV


@pytest.fixture(scope="session", autouse=True)
def nodes_stop_with_this_process():
    """A node a test starts, from this process or a child of it, is stopped by its supervisor once this process has exited.
    Set for the session and not at import: the runner imports ``conftest.py`` to check the selectors, and the value must not reach its
    environment."""
    os.environ[PARENT_ENV] = str(os.getpid())
    yield
    os.environ.pop(PARENT_ENV, None)


@pytest.fixture(autouse=True)
def environment_is_restored():
    """Put ``os.environ`` back as it was when the test began."""
    before = dict(os.environ)
    yield
    if dict(os.environ) != before:
        os.environ.clear()
        os.environ.update(before)

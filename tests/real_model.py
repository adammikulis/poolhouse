"""A real local model for the tests that attack, ask or measure one.

No test here stands a fake in for a model. A test that needs one takes the ``real_model``
fixture: the smallest installed text model (``POOLHOUSE_TEST_GGUF`` names another), served
through the lease like anywhere else. Where this machine has no llama-server build, no such
model, or another server already running, the test is skipped with that reason and never
answered some other way.

Name ``pytest_plugins = ["serve_node"]`` in the test module and import the fixture:
``from real_model import real_model  # noqa: F401``.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from poolhouse.serve.process import every_server
from tests.test_serve_real_llama import llama_server, small_gguf

__all__ = ["real_model"]


@pytest.fixture
def real_model(_real_home, serve_leases, monkeypatch) -> Path:
    """The path of the real model to serve; ``LLAMA_CPP_SERVER`` names the machine's build."""
    account = _real_home.state.parent
    binary = llama_server(account)
    if binary is None:
        pytest.skip("no llama-server build on this machine; set POOLHOUSE_TEST_LLAMA_SERVER")
    model = small_gguf(account)
    if model is None:
        pytest.skip("no small installed GGUF to serve; set POOLHOUSE_TEST_GGUF")
    deadline = time.monotonic() + 45        # the previous test's server is still coming down
    while every_server() and time.monotonic() < deadline:
        time.sleep(1)
    if every_server():
        pytest.skip("another llama-server is running; a test does not load a model beside it")
    monkeypatch.setenv("LLAMA_CPP_SERVER", str(binary))
    # `serve()` keeps its manager, and that manager the lease table it was made with: one made
    # in an earlier test would ask a node whose state root that test removed
    monkeypatch.setattr("poolhouse.serve.manager._DEFAULT", None)
    return model

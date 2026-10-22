"""A real local model for the tests that attack, ask or measure one.

No test here stands a fake in for a model, and none skips when it is missing: a machine without
the llama-server build, the model, or with another server already running fails the test and
says what to run. Name ``pytest_plugins = ["serve_node"]`` in the test module and import the
fixture: ``from real_model import real_model, tool_model  # noqa: F401``.

``real_model`` is the smallest installed text model (``POOLHOUSE_TEST_GGUF`` names another), for
a test that only needs a model to answer. ``tool_model`` is the Qwen3-4B-Instruct-2507 Q4_K_M
that a test needs when the model has to choose and call tools, and fall for an injected
instruction, for the attack to mean anything. Both are served through the lease like anywhere else.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from poolhouse.serve.process import every_server
from tests.test_serve_real_llama import llama_server, small_gguf

__all__ = ["real_model", "tool_model"]

TOOL_FILE = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
TOOL_REF = f"hf:lmstudio-community/Qwen3-4B-Instruct-2507-GGUF/{TOOL_FILE}"
BUSY_WAIT_S = 300


def _fail(problem: str) -> None:
    pytest.fail(problem, pytrace=False)


def _computing() -> list[dict]:
    """The llama-servers that are running; a defunct one an agent left unreaped computes nothing."""
    return [one for one in every_server() if not one.get("defunct")]


def _ready(account: Path, monkeypatch) -> None:
    """Name the machine's llama-server, wait for another to finish and put the lease table in place."""
    binary = llama_server(account)
    if binary is None:
        _fail("LLAMA-SERVER MISSING: no llama-server build on this machine. A test of a model needs a "
              "real one and never skips: poolhouse-serve llama-cpp update (or set POOLHOUSE_TEST_LLAMA_SERVER)")
    deadline = time.monotonic() + BUSY_WAIT_S      # the previous test's server is still coming down
    while _computing() and time.monotonic() < deadline:
        time.sleep(1)
    if _computing():
        _fail("COMPUTE BUSY: another llama-server is running and stayed up for "
              f"{BUSY_WAIT_S} s. A test does not load a model beside someone else's work; "
              "`poolhouse-serve status` shows it.")
    monkeypatch.setenv("LLAMA_CPP_SERVER", str(binary))
    # `serve()` keeps its manager, and that manager the lease table it was made with: one made
    # in an earlier test would ask a node whose state root that test removed
    monkeypatch.setattr("poolhouse.serve.manager._DEFAULT", None)


@pytest.fixture
def real_model(_real_home, serve_leases, monkeypatch) -> Path:
    """The path of the smallest installed text model; ``LLAMA_CPP_SERVER`` names the machine's build."""
    account = _real_home.state.parent
    model = small_gguf(account)
    if model is None:
        _fail("REAL MODEL MISSING: no small installed GGUF to serve. A test of a model never runs on a "
              f"stand-in and never skips: poolhouse-models pull {TOOL_REF} (or set POOLHOUSE_TEST_GGUF)")
    _ready(account, monkeypatch)
    return model


@pytest.fixture
def tool_model(_real_home, serve_leases, monkeypatch) -> Path:
    """The path of Qwen3-4B-Instruct-2507 Q4_K_M, the small model that calls tools and follows an injected instruction."""
    account = _real_home.state.parent
    root = account / ".cache" / "huggingface" / "hub"
    found = sorted(root.rglob(TOOL_FILE)) if root.is_dir() else []
    if not found:
        _fail(f"REAL MODEL MISSING: {TOOL_FILE} (2.3 GB) is not in the Hugging Face cache ({root}). A test of a "
              f"model never runs on a stand-in and never skips: poolhouse-models pull {TOOL_REF} "
              "(pull, not fetch: only a pull pins the file, and an unpinned one asks a person on its first serve)")
    _ready(account, monkeypatch)
    return found[0]

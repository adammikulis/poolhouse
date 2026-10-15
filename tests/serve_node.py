"""A node under the serve tests: the lease table every `ServerManager` and `Broker` takes its memory, its slot and the GPU from.

Name the plugin in a test module (``pytest_plugins = ["serve_node"]``) and use the fixture ``serve_leases`` (``pytestmark =
pytest.mark.usefixtures("serve_leases")``). One real node runs for a worker's whole session on a short temporary root. A test runs
with a state root of its own on a short path, empty but for the node's directory, which is a link to the session's: the node is
where `poolhouse.leasetable.default` and every child process look for it, and nothing else of one test is left for the next.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from poolhouse import leasetable, node_binary as node_binary_module, node_launch, node_supervise

pytest_plugins = ["node_kit"]


@pytest.fixture(scope="session")
def lease_node(node_binary):
    """The state directory of a node that lives as long as the session."""
    root = Path(tempfile.mkdtemp(prefix="ml"))
    state = root / "node"
    state.mkdir(mode=0o700)
    node_supervise.point(state, node_binary, node_binary_module.sha256(node_binary))
    node_launch.ensure_node(state)
    yield state
    node_launch.stop_node(state)
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def serve_leases(monkeypatch, lease_node):
    """The lease table of the session's node, with no lease held and no memory budget, under a state root of this test's own.
    A model file the test places is served as a person approved it (`tests/test_publisher_trust.py` has the ask)."""
    monkeypatch.setattr("poolhouse.serve.guarded._first_use", lambda *_found: "approved")
    home = Path(tempfile.mkdtemp(prefix="ml"))
    (home / "node").symlink_to(lease_node)
    monkeypatch.setenv("POOLHOUSE_HOME", str(home))
    table = leasetable.default()
    yield table
    for lease in table.listing()["leases"]:
        table.release(lease["id"])
    (lease_node / leasetable.CONFIG).unlink(missing_ok=True)
    shutil.rmtree(home, ignore_errors=True)

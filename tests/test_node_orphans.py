"""A node left running by a killed pytest is found by the pid its root records, stopped and removed; a live owner's node is not touched."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import node_orphans
import pytest

from poolhouse import node_binary as node_binary_module, node_launch, node_supervise

pytest_plugins = ["node_kit"]


@pytest.fixture
def running(node_binary):
    """A node on a short temporary root, as the kits start one."""
    root = Path(tempfile.mkdtemp(prefix=node_orphans.PREFIX, dir="/tmp"))
    state = root / "node"
    state.mkdir(mode=0o700)
    node_supervise.point(state, node_binary, node_binary_module.sha256(node_binary))
    node_launch.ensure_node(state)
    yield root, state
    node_launch.stop_node(state)
    shutil.rmtree(root, ignore_errors=True)


def dead_pid() -> int:
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


def test_a_node_whose_pytest_was_killed_is_stopped_and_its_root_removed(running):
    root, state = running
    (root / node_orphans.OWNER).write_text(str(dead_pid()))
    assert node_launch.node_health(state) is not None
    assert root in node_orphans.reap(root.parent)
    assert node_launch.node_health(state) is None
    assert not root.exists()


def test_a_node_whose_pytest_is_running_is_left_alone(running):
    root, state = running
    (root / node_orphans.OWNER).write_text(str(os.getppid()))
    assert root not in node_orphans.reap(root.parent)
    assert node_launch.node_health(state) is not None and root.exists()


def test_a_root_that_records_no_owner_is_left_alone(running):
    root, state = running
    assert root not in node_orphans.reap(root.parent)
    assert node_launch.node_health(state) is not None


def test_a_kit_records_the_pid_of_its_pytest(workspace_node):
    assert node_orphans.owner_of(workspace_node.state.parent) == os.getpid()

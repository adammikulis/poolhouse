"""A node left running by a killed pytest is found by the pid its root records, stopped and removed; a live owner's node is not touched."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import node_orphans
import pytest

from poolhouse import node_binary as node_binary_module, node_launch, node_supervise

pytest_plugins = ["node_kit"]


def test_node_binary_fixture_does_not_reap_shared_state(tmp_path, monkeypatch):
    kit = sys.modules["node_kit"]
    binary = tmp_path / "source-node"
    binary.write_bytes(b"fixture binary")
    copied = tmp_path / "copy"
    copied.mkdir()

    def refuse_reap(*_args):
        pytest.fail("node binary setup must not scan shared orphan roots")

    monkeypatch.setattr(node_orphans, "reap", refuse_reap)
    monkeypatch.setattr(kit, "runner_binary", lambda: binary)
    factory = SimpleNamespace(mktemp=lambda _prefix: copied)
    found = kit.node_binary.__wrapped__(factory)
    assert found.read_bytes() == binary.read_bytes()


@pytest.fixture
def orphan_base():
    with tempfile.TemporaryDirectory(prefix="orphan-", dir="/tmp") as name:
        yield Path(name)


@pytest.fixture
def running(node_binary, orphan_base):
    """A node on a short temporary root, as the kits start one."""
    root = Path(tempfile.mkdtemp(prefix=node_orphans.PREFIX, dir=orphan_base))
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


def test_a_recorded_pid_that_is_another_live_process_is_not_signalled(orphan_base):
    bystander = subprocess.Popen(["sleep", "60"])
    root = Path(tempfile.mkdtemp(prefix=node_orphans.PREFIX, dir=orphan_base))
    try:
        (root / node_orphans.OWNER).write_text(str(dead_pid()))
        (root / "node").mkdir(mode=0o700)
        node_supervise.write_json(root / "node" / node_supervise.RUN, {"pid": bystander.pid, "supervisor": bystander.pid})
        assert root in node_orphans.reap(root.parent)
        assert not root.exists()
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()
        shutil.rmtree(root, ignore_errors=True)


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


def test_reaping_an_owned_base_leaves_other_roots_untouched(tmp_path):
    owned = tmp_path / "owned"
    owned.mkdir()
    mine = owned / "ml-owned"
    outside = tmp_path / "ml-outside"
    for root in (mine, outside):
        root.mkdir()
        (root / node_orphans.OWNER).write_text(str(dead_pid()))
    assert node_orphans.reap(owned) == [mine]
    assert outside.is_dir()

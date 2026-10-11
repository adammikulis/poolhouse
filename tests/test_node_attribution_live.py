"""Against a real node: the lineage it records for a session decides whether a change under its directory is this run's."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from node_attribution import NodeWatch
from node_kit import BOARD, STRIPPED, WRITE_LIMITS, WorkspaceNode
from state_attribution import Watch

from poolhouse import node_binary as node_binary_module, node_supervise
from poolhouse.board import client as board_client

pytest_plugins = ["node_kit"]
NAMES, TOKENS = "node/boards/demo/names.json", "node/tokens.json"
START = "import sys\nfrom pathlib import Path\nfrom poolhouse import node_launch\nnode_launch.ensure_node(Path(sys.argv[1]))\n"
AGENT = """
import json, sys, time
from pathlib import Path
from poolhouse.board import session
from poolhouse.board.client import Client
state, go, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
out.with_suffix(".up").write_text("{}")
for _ in range(3000):
    if go.exists():
        break
    time.sleep(0.02)
made = session.register(Client(state), "demo", session.Native("claude-sonnet-5-5", "claude-code", "elsewhere"))
out.write_text(json.dumps({"name": made.name}))
"""


@pytest.fixture
def outside_node(node_binary, monkeypatch):
    """A running node on a short temporary root that a process which has since exited started, so it is no part of this run."""
    root = Path(tempfile.mkdtemp(prefix="ml", dir="/tmp"))
    state = root / "node"
    state.mkdir(mode=0o700)
    (state / "write-limits.json").write_text(json.dumps(WRITE_LIMITS))
    monkeypatch.setenv("POOLHOUSE_HOME", str(root))
    monkeypatch.setenv("POOLHOUSE_BOARD", BOARD)
    for name in STRIPPED:
        if name != "POOLHOUSE_BOARD":
            monkeypatch.delenv(name, raising=False)
    node_supervise.point(state, node_binary, node_binary_module.sha256(node_binary))
    node = WorkspaceNode(state, board_client.Client(state), node_binary)
    subprocess.run([sys.executable, "-c", START, str(state)], env=node.env(), check=True, timeout=120)
    try:
        yield node
    finally:
        node.stop()
        shutil.rmtree(root, ignore_errors=True)


def other_agent(node: WorkspaceNode, folder: Path) -> Path:
    """Start a process outside this run, as an agent started elsewhere is; it registers a session when ``go`` appears in
    ``folder``. Returns the file it writes the session's name to."""
    out = folder / "name.json"
    argv = [sys.executable, "-c", AGENT, str(node.state), str(folder / "go"), str(out)]
    launcher = f"import subprocess, sys; subprocess.Popen({argv!r}, start_new_session=True, stdout=subprocess.DEVNULL, env={node.env()!r})"
    subprocess.run([sys.executable, "-c", launcher], check=True)
    return out


def written(path: Path) -> dict:
    for _ in range(600):
        if path.exists():
            return json.loads(path.read_text())
        time.sleep(0.05)
    raise AssertionError(f"{path} was not written")


def test_a_session_registered_from_outside_the_run_is_excused_and_one_registered_from_it_fails(outside_node, tmp_path):
    node = outside_node
    root = node.state.parent
    node.member("started-before")
    out = other_agent(node, tmp_path)
    written(out.with_suffix(".up"))
    watch = Watch(root, lambda r: {}, lambda b, a: [NAMES, TOKENS], lambda: [(4242, "poolhouse-node run")],
                  attribute=NodeWatch(root).judge)
    (tmp_path / "go").write_text("")
    assert written(out)["name"] in json.loads((root / NAMES).read_text())
    assert watch.verdict()[:2] == ([], [])
    inside = node.member("in-this-run")
    assert inside.name in json.loads((root / NAMES).read_text())
    failed, possible, _ = watch.verdict()
    assert failed == [NAMES, TOKENS] and possible == []
    assert f"written for {inside.name}, registered from this test run" in watch.settle()

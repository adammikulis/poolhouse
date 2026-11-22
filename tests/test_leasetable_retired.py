"""A lease table heals itself: a session the node retired, or one a process of another tree registered, is registered again from where the
process runs and the lease goes through, with nobody asked."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from node_kit import SRC

from poolhouse import leasetable, node_health
from poolhouse.node_health import Refused

pytest_plugins = ["node_kit"]


def stale(workspace_node) -> Path:
    """The token file of the lease table's session holding a token the node does not know, as one left by a retired session is."""
    table = leasetable.Table(workspace_node.state)
    table.release(table.acquire([leasetable.gpu()], leasetable.Terms(tag="first"))["id"])
    (path,) = (workspace_node.state / "client" / leasetable.BOARD).glob("*.token")
    path.write_text("0" * 64 + "\n", encoding="utf-8")
    return path


def test_a_retired_session_registers_again_and_the_lease_is_taken(workspace_node):
    path = stale(workspace_node)
    fresh = leasetable.Table(workspace_node.state)
    sent: list[str] = []
    real = fresh._send

    def counting(method, params, token="", wait_s=0.0):
        sent.append(method)
        return real(method, params, token, wait_s)

    fresh._send = counting
    held = fresh.acquire([leasetable.gpu()], leasetable.Terms(tag="again", wait=False))
    assert held["state"] == "held"
    assert sent.count("register") == 1, "registered once, not in a loop"
    assert sent.count("lease_acquire") == 2, "the stale token was tried once, then the fresh one"
    kept = path.parent.glob("*.token")
    assert all(p.read_text(encoding="utf-8").strip() != "0" * 64 for p in kept), "the stale token was dropped"
    assert any(p.read_text(encoding="utf-8").strip() for p in path.parent.glob("*.token")), "the new token is kept for other processes"
    assert fresh.release(held["id"])


def test_the_gate_takes_its_lease_after_the_session_was_retired(workspace_node):
    stale(workspace_node)
    table = leasetable.Table(workspace_node.state)
    held = table.acquire([leasetable.gpu()], leasetable.Terms(tag="turn.test", wait=False))
    assert held["state"] == "held" and table.release(held["id"])


def test_only_the_nodes_own_unknown_token_denial_is_a_retired_session():
    unknown = "the token is not known: its session was retired to free its resources (an idle session is) or it never registered; the next registration of its harness's session gives it a token"
    assert node_health.token_unknown(Refused(unknown, code="denied"))
    assert node_health.token_unknown(Refused("denied: " + unknown, code="denied"))
    assert node_health.token_unknown(Refused("the token is not known: it was registered from a terminal or process tree that has ended", code="denied"))
    assert not node_health.token_unknown(Refused(unknown, code="invalid"))
    assert not node_health.token_unknown(Refused("this token may not do that", code="denied"))
    assert not node_health.token_unknown(Refused("denied: a grant is missing for the token", code="denied"))


REGISTER_AND_HOLD = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "from poolhouse import leasetable\n"
    "state, out, stop = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])\n"
    "table = leasetable.Table(state)\n"
    "held = table.acquire([leasetable.gpu()], leasetable.Terms(tag='orphan', wait=False))\n"
    "table.release(held['id'])\n"
    "out.write_text(held['state'])\n"
    "until = time.time() + 40\n"
    "while not stop.exists() and time.time() < until:\n"
    "    time.sleep(0.05)\n"
)


def orphan(workspace_node, tmp_path) -> tuple[Path, Path]:
    """A process that registers the lease table's session from a tree of its own (its parent exits at once, as a daemon's does) and runs
    until the returned stop file exists; the file it writes when it holds its lease."""
    out, stop = tmp_path / "out", tmp_path / "stop"
    env = {**os.environ, "PYTHONPATH": SRC, "POOLHOUSE_HOME": str(workspace_node.state.parent)}
    quoted = " ".join(shlex.quote(a) for a in (sys.executable, "-c", REGISTER_AND_HOLD, str(workspace_node.state), str(out), str(stop)))
    subprocess.run(["/bin/sh", "-c", f"{quoted} >/dev/null 2>&1 &"], env=env, start_new_session=True, check=True, timeout=30)
    until = time.monotonic() + 30
    while not out.exists() and time.monotonic() < until:
        time.sleep(0.05)
    assert out.read_text() == "held", "the process of another tree took a lease"
    return out, stop


def forget_tokens(workspace_node) -> None:
    for kept in (workspace_node.state / "client" / leasetable.BOARD).glob("*.token"):
        kept.unlink()


def incarnation(workspace_node) -> str:
    return str(workspace_node.client.call("session_lookup", leasetable.BOARD, harness=leasetable.NATIVE["harness"], session=leasetable.NATIVE["session"])["incarnation"])


def test_a_process_of_another_tree_takes_leases_while_the_first_still_runs_and_after_it_ended(workspace_node, tmp_path):
    _, stop = orphan(workspace_node, tmp_path)
    before = incarnation(workspace_node)
    forget_tokens(workspace_node)
    table = leasetable.Table(workspace_node.state)
    held = table.acquire([leasetable.gpu()], leasetable.Terms(tag="sibling", wait=False))
    assert held["state"] == "held" and table.release(held["id"]), "a live process of another tree registers the shared session too"
    assert incarnation(workspace_node) == before, "and the session stays with the tree that runs it"

    stop.write_text("")
    for _ in range(100):
        if not subprocess.run(["pgrep", "-f", str(stop)], capture_output=True, check=False).stdout.strip():
            break
        time.sleep(0.1)
    forget_tokens(workspace_node)
    heir = leasetable.Table(workspace_node.state)
    taken = heir.acquire([leasetable.gpu()], leasetable.Terms(tag="heir", wait=False))
    assert taken["state"] == "held" and heir.release(taken["id"]), "after the first tree ended this process registers the session"
    assert incarnation(workspace_node) != before, "which moved to this process's tree"

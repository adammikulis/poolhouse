"""A lease table whose session the node retired heals itself: it registers the session again and the lease goes through, with nobody asked."""

from __future__ import annotations

from pathlib import Path

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
    unknown = "the token is not known: its session was retired (an idle session is retired after a day) or never registered; start a new session"
    assert node_health.token_unknown(Refused(unknown, code="denied"))
    assert node_health.token_unknown(Refused("denied: " + unknown, code="denied"))
    assert not node_health.token_unknown(Refused(unknown, code="invalid"))
    assert not node_health.token_unknown(Refused("this token may not do that", code="denied"))
    assert not node_health.token_unknown(Refused("denied: a grant is missing for the token", code="denied"))


def test_a_process_of_another_tree_gets_its_own_session_instead_of_a_refusal(workspace_node):
    table = leasetable.Table(workspace_node.state)
    real = table._send

    def bound_elsewhere(method, params, token="", wait_s=0.0):
        if method == "register" and params.get("session") == leasetable.NATIVE["session"]:
            raise Refused("this session is registered from another process lineage (another terminal or program)", code="denied")
        return real(method, params, token, wait_s)

    table._send = bound_elsewhere
    held = table.acquire([leasetable.gpu()], leasetable.Terms(tag="elsewhere", wait=False))
    assert held["state"] == "held", "the node accepted the per-process session's token"
    assert table.release(held["id"])
    assert not list((workspace_node.state / "client" / leasetable.BOARD).glob("*.token")), "a per-process token is not kept on disk"

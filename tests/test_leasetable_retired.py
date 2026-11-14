"""A lease table whose session the node retired stops at once with one error that names the person's step; it never retries the same token."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from poolhouse import gate, leasetable, node_health
from poolhouse.node_health import Refused

pytest_plugins = ["node_kit"]


def stale(workspace_node) -> Path:
    """The token file of the lease table's session holding a token the node does not know, as one left by a retired session is."""
    leasetable.Table(workspace_node.state).acquire([leasetable.gpu()], leasetable.Terms(tag="first"))
    (path,) = (workspace_node.state / "client" / leasetable.BOARD).glob("*.token")
    path.write_text("0" * 64 + "\n", encoding="utf-8")
    return path


def test_a_retired_session_is_one_error_that_names_the_person_step_and_is_asked_once(workspace_node):
    path = stale(workspace_node)
    before = sorted(p.name for p in path.parent.iterdir())
    fresh = leasetable.Table(workspace_node.state)
    sent: list[str] = []
    real = fresh._send

    def counting(method, params, token="", wait_s=0.0):
        sent.append(method)
        return real(method, params, token, wait_s)

    fresh._send = counting
    with pytest.raises(node_health.SessionRetired) as gone:
        fresh.acquire([leasetable.gpu()], leasetable.Terms(tag="again"))
    assert "retired" in str(gone.value) and "poolhouse-workspace rebind" in str(gone.value) and "approval" in str(gone.value)
    assert "0" * 16 not in str(gone.value), "the error never carries a token"
    assert sent.count("lease_acquire") == 1, "the same token is not asked with twice"
    assert path.read_text(encoding="utf-8").strip() == "0" * 64, "no token was replaced, minted or borrowed"
    assert sorted(p.name for p in path.parent.iterdir()) == before, "no other session was registered in its place"


def test_the_gate_stops_at_a_retired_session_instead_of_waiting_out_its_allowance(workspace_node):
    stale(workspace_node)
    began = time.monotonic()
    with pytest.raises(node_health.SessionRetired):
        gate._await(leasetable.Table(workspace_node.state), "gpu", "turn.test", 30.0, None)
    assert time.monotonic() - began < 10


def test_only_the_nodes_own_unknown_token_denial_is_a_retired_session():
    unknown = "the token is not known: its session was retired (an idle session is retired after a day) or never registered; start a new session"
    assert node_health.token_unknown(Refused(unknown, code="denied"))
    assert node_health.token_unknown(Refused("denied: " + unknown, code="denied"))
    assert not node_health.token_unknown(Refused(unknown, code="invalid"))
    assert not node_health.token_unknown(Refused("this token may not do that", code="denied"))
    assert not node_health.token_unknown(Refused("denied: a grant is missing for the token", code="denied"))

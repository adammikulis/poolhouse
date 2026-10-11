"""A phone for tests (`phone_keys`) and the node that listens for it.

Use `pytest_plugins = ["node_kit", "phone_kit"]` in a test module to get `phone_node`: the test node, restarted listening for phones on
loopback the way a person's `phone add` turns it on.
"""

from __future__ import annotations

from typing import Any

import pytest
from phone_keys import Endpoint, PhoneKeys

from poolhouse import node_launch
from poolhouse.board import phone

__all__ = ["Endpoint", "PhoneKeys", "add_phone", "endpoint_of", "phone_node"]


def endpoint_of(team: Any) -> Endpoint:
    """The phone endpoint of the node ``team`` runs."""
    return Endpoint(team.state, int(team.client.call("phone_info")["port"]))


def add_phone(team: Any, name: str = "Pixel", *, by: PhoneKeys | None = None) -> PhoneKeys:
    """Add a phone as the person's command does: leave the code, let the phone use it (a later phone waits for ``by`` to approve it)."""
    endpoint = endpoint_of(team)
    secret = phone.expect_phone(team.state)
    device = str(team.client.call("phone_info", secret=secret)["device"])
    added = PhoneKeys(name)
    reply = endpoint.ok("add", added.add_body(device, secret))
    added.token, added.id = reply["token"], reply["phone"]
    if by is not None:
        assert reply["state"] == "pending"
        seen = endpoint.ok("get", {"id": reply["approval"]}, token=by.token)
        assert endpoint.ok("decide", by.decision(seen, "approve"))["state"] == "approved"
    else:
        assert reply["state"] == "active"
    return added


@pytest.fixture
def phone_node(workspace_node):
    """The test node, restarted listening for phones on loopback: the way a person's `phone add` turns it on."""
    node_launch.stop_node(workspace_node.state)
    phone.listen_on(workspace_node.state, "127.0.0.1:0")
    node_launch.ensure_node(workspace_node.state)
    return workspace_node

"""A phone for tests (`phone_keys`) and the node that listens for it.

Use `pytest_plugins = ["node_kit", "phone_kit"]` in a test module to get `phone_node`: the test node, restarted listening for phones on
loopback the way a person's `phone add` turns it on.
"""

from __future__ import annotations

from typing import Any

import pytest
from phone_keys import Endpoint, PhoneKeys
from pool_check import wire
from pool_check.steps_phone import link_secret

from poolhouse import node_launch
from poolhouse.board import phone

__all__ = ["Endpoint", "PhoneKeys", "add_phone", "code_secret", "endpoint_of", "phone_node"]


def endpoint_of(team: Any) -> Endpoint:
    """The phone endpoint of the node ``team`` runs."""
    return Endpoint(team.state, int(team.client.call("phone_info")["port"]))


def code_secret(team: Any, by: PhoneKeys | None) -> str:
    """The one-time secret of the next phone's code: a first phone's is made by the node when the person types yes to its question, and
    a further phone's by a session's request once a key exists."""
    if by is None:
        said = wire.answer_presence(team.state, team.client.call("register", team.board, "", model="claude-sonnet-5-5", harness="claude-code", session="phone-adder")["token"], "phone")
        assert said["ok"] and said["result"]["state"] == "confirmed", f"the person's yes was not taken: {said}"
        return str(said["result"]["secret"])
    token = team.client.call("register", team.board, "", model="claude-sonnet-5-5", harness="claude-code", session="phone-offerer")["token"]
    return link_secret(str(team.client.call("phone_offer", "", token)["link"]))


def add_phone(team: Any, name: str = "Pixel", *, by: PhoneKeys | None = None, link: str = "") -> PhoneKeys:
    """Add a phone as the person's command does: leave the code, let the phone use it (a later phone waits for ``by`` to approve it); ``link`` is a code that was already asked for."""
    endpoint = endpoint_of(team)
    secret = link_secret(link) if link else code_secret(team, by)
    device = str(team.client.call("phone_info", secret=secret)["device"])
    added = PhoneKeys(name)
    reply = endpoint.ok("add", added.add_body(device, secret))
    added.token, added.id = reply["token"], reply["phone"]
    if by is not None:
        assert reply["state"] == "pending"
        seen = endpoint.ok("get", {"id": reply["approval"]}, token=by.token)
        assert len(reply["number"]) == 2, "the new phone is given a number to show, and the approving phone picks it"
        assert endpoint.ok("decide", by.decision(seen, "approve", pick=reply["number"]))["state"] == "approved"
    else:
        assert reply["state"] == "active"
    return added


@pytest.fixture
def phone_node(workspace_node):
    """The test node, restarted listening for phones on loopback: the way a person's `phone add` turns it on."""
    node_launch.stop_node(workspace_node.state)
    phone.listen_on(workspace_node.state, "127.0.0.1:0")
    workspace_node.start()
    return workspace_node

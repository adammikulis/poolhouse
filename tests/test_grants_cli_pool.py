"""In a pool a typed yes signs no grant that reaches beyond the device, and the command says so before any prompt."""
from __future__ import annotations

import argparse
from types import SimpleNamespace
from typing import Any

import pytest

from poolhouse.board import person_key
from poolhouse.board.client import Denied
from poolhouse.workspace import grants_cli

DEVICE_LOCAL = ["link_read", "link_remove", "source_add", "quarantine_release", "pool_leave"]


def listed(governance: str) -> dict[str, Any]:
    """What the node answers to `grants` for a device in a pool or in none."""
    return {"grants": [], "ignored": [], "persons": [], "registry": {"actions": [], "device_local": DEVICE_LOCAL}, "now_ms": 0, "device": "d" * 64,
            "governance": governance, "authority": [], "may_issue": [], "may_revoke": []}


class Carrier:
    """A session whose node answers `grants` for a device in a pool or in none."""

    board = "demo"

    def __init__(self, governance: str) -> None:
        self.governance = governance

    def call(self, method: str, **_: Any) -> dict[str, Any]:
        assert method == "grants", method
        return listed(self.governance)


class Key:
    """A key whose use is a prompt: the test fails if it is used."""

    public = b"k" * 32

    def sign_bytes(self, data: bytes) -> bytes:
        raise AssertionError("a typed yes was asked for")


def ctx(governance: str) -> Any:
    return SimpleNamespace(client=None, carrier=Carrier(governance), key=Key())


def asked(action: str) -> argparse.Namespace:
    return argparse.Namespace(action=action)


@pytest.mark.parametrize("action", ["file_delete", "shard_consent", "task_authority", "sync_now"])
def test_a_grant_that_reaches_beyond_the_device_is_refused_in_a_pool_before_any_prompt(action):
    with pytest.raises(Denied, match="your phone gives standing grants"):
        grants_cli.grant(asked(action), ctx("governed"))


def test_grants_setup_in_a_pool_is_refused_before_it_asks_which_session_or_for_a_yes(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("a question was asked"))
    with pytest.raises(Denied, match="your phone gives standing grants"):
        grants_cli.setup(argparse.Namespace(session=None, family="", days=30, open_registration=False), ctx("governed"))


def test_a_device_local_grant_is_not_refused_in_a_pool_and_a_device_in_none_refuses_nothing(monkeypatch):
    monkeypatch.setattr(grants_cli, "_held", lambda _ctx: (_ for _ in ()).throw(AssertionError("reached the key")))
    with pytest.raises(AssertionError, match="reached the key"):
        grants_cli.grant(asked("link_read"), ctx("governed"))
    with pytest.raises(AssertionError, match="reached the key"):
        grants_cli.grant(asked("file_delete"), ctx("unfounded"))


def refused_for(governance: str, monkeypatch, tmp_path):
    """The provider of a person's key on a node of this governance, whose key fails the test when it is used to sign and whose node
    fails it when a grant arrives."""

    class Node:
        def __init__(self, state):
            assert state == tmp_path

        def call(self, method, board, token, **_):
            assert (method, board, token) == ("grants", "demo", "carrier"), (method, board, token)
            return listed(governance)

    monkeypatch.setattr(person_key, "Client", Node)
    monkeypatch.setattr(person_key, "held", lambda state, attended=False: Key())
    monkeypatch.setattr(person_key, "call", lambda *a, **k: pytest.fail("a signed grant reached the node"))
    return person_key.provider(tmp_path, acting=lambda board: ("lead-1", "carrier"))


def hint(action: str) -> dict[str, Any]:
    return {"board": "demo", "name": "lead-1", "action": action, "resource": "*", "device": "*"}


@pytest.mark.parametrize("action", ["file_delete", "shard_consent", "task_authority", "sync_now"])
def test_the_persons_key_is_not_asked_for_a_one_use_grant_a_pool_would_refuse(action, monkeypatch, tmp_path):
    allow = refused_for("governed", monkeypatch, tmp_path)
    assert allow("token", hint(action)) == ""


def test_the_persons_key_is_asked_for_a_device_local_grant_in_a_pool_and_for_any_grant_in_none(monkeypatch, tmp_path):
    for governance, action in (("governed", "link_read"), ("unfounded", "file_delete")):
        allow = refused_for(governance, monkeypatch, tmp_path)
        with pytest.raises(AssertionError, match="a typed yes was asked for"):
            allow("token", hint(action))


class Signs:
    """A key that signs with a fixed answer."""

    public = b"k" * 32

    def sign_bytes(self, data: bytes) -> bytes:
        return b"s" * 64


class PoolNode:
    """A session on a device in a pool that lists one admin key, and the requests the command made."""

    board = "demo"
    token = "t"

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.client = self

    def call(self, method: str, board: str = "", token: str = "", **params: Any) -> dict[str, Any]:
        self.sent.append((method, params))
        if method == "pool_status":
            return {"pool": "p" * 32, "governance": {"admins": [{"fingerprint": "a" * 64, "since": 3}]}}
        return {"key": params["key"], "distrusted": True, "entry": "e"}


def test_distrust_signs_the_key_and_the_epoch_it_was_listed_at_and_reaches_the_node_as_a_distrust(monkeypatch):
    monkeypatch.setattr(grants_cli, "_held", lambda ctx: ctx.key)
    node = PoolNode()
    done = grants_cli.distrust(argparse.Namespace(key="a" * 64, reason="lost"), SimpleNamespace(client=None, carrier=node, key=Signs()))
    assert done["distrusted"] == "a" * 64
    method, params = node.sent[-1]
    assert method == "grant_distrust"
    assert (params["key"], params["since"], params["pool"], params["reason"]) == ("a" * 64, 3, "p" * 32, "lost")
    assert "op" not in params and "board" not in params and len(params["sig"]) == 128 and params["person"] == grants_cli.grants.fingerprint(Signs.public)


def test_distrust_of_a_key_the_pool_does_not_list_is_refused_before_anything_is_signed(monkeypatch):
    monkeypatch.setattr(grants_cli, "_held", lambda ctx: ctx.key)
    node = PoolNode()
    with pytest.raises(RuntimeError, match="does not list that key"):
        grants_cli.distrust(argparse.Namespace(key="b" * 64, reason=""), SimpleNamespace(client=None, carrier=node, key=Key()))
    assert [m for m, _ in node.sent] == ["pool_status"]

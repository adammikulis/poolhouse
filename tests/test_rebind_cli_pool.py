"""In a pool the move of a main session to a terminal waits for an admin phone's tap, and a typed yes moves nothing."""
from __future__ import annotations

import argparse
import time
from types import SimpleNamespace
from typing import Any

import pytest

from poolhouse.board.client import Denied
from poolhouse.workspace import grants_cli

ANCHOR = {"pid": 4242, "start": 99}


class Carrier:
    """A session whose node answers `grants` for a device in a pool or in none."""

    board = "demo"
    token = "t"

    def __init__(self, governance: str) -> None:
        self.governance = governance

    def call(self, method: str, **_: Any) -> dict[str, Any]:
        assert method == "grants", method
        return {"grants": [], "ignored": [], "persons": [], "registry": {"actions": [], "device_local": []}, "now_ms": 0, "device": "d" * 64,
                "governance": self.governance, "authority": [], "may_issue": [], "may_revoke": []}


class Node:
    """The node as the command sees it: it makes a request, answers pending for a number of polls, then moves the session."""

    def __init__(self, pending: int, expires_ms: int | None = None, ends: str = "") -> None:
        self.pending, self.calls, self.ends = pending, [], ends
        self.expires_ms = int((time.time() + 600) * 1000) if expires_ms is None else expires_ms

    def call(self, method: str, board: str = "", token: str = "", **params: Any) -> dict[str, Any]:
        if method == "lineage":
            return {"lineage": [{"pid": 1, "start": 1}, {"pid": ANCHOR["pid"], "start": ANCHOR["start"]}]}
        assert method == "rebind", method
        self.calls.append(params)
        if "approval" not in params:
            return {"id": "ap-0123456789abcdef", "code": "47", "state": "pending", "expires_ms": self.expires_ms}
        if self.ends:
            raise Denied("denied", self.ends)
        if self.pending:
            self.pending -= 1
            return {"state": "pending", "moved": False, "expires_ms": self.expires_ms}
        return {"name": "lead", "moved": True, "tokens_ended": 2}


class Key:
    """A key whose use is a prompt: the test fails if it is used."""

    public = b"k" * 32

    def sign_bytes(self, data: bytes) -> bytes:
        raise AssertionError("a typed yes was asked for")


def ctx(governance: str, node: Node) -> Any:
    return SimpleNamespace(client=node, carrier=Carrier(governance), key=Key())


ARGS = argparse.Namespace(harness="claude-code", session="native-1")


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(grants_cli.time, "sleep", lambda _s: None)
    monkeypatch.setattr(grants_cli, "_held", lambda _ctx: (_ for _ in ()).throw(AssertionError("a typed yes was asked for")))


def test_a_move_in_a_pool_shows_the_number_waits_for_the_tap_and_completes_from_this_terminal(capsys):
    node = Node(pending=2)
    done = grants_cli.rebind(ARGS, ctx("governed", node))
    assert done["moved"] == "lead" and done["tokens_ended"] == 2
    asking, *polls = node.calls
    assert asking == {"harness": "claude-code", "session": "native-1", "anchor": ANCHOR}, "the first call asks, naming this terminal"
    assert len(polls) == 3 and all(p == {**asking, "approval": "ap-0123456789abcdef"} for p in polls)
    shown = capsys.readouterr()
    assert "choose 47" in shown.err + shown.out, "the terminal shows the number the person picks"


def test_a_move_nobody_taps_in_time_is_refused_in_one_sentence():
    node = Node(pending=10**6, expires_ms=int((time.time() - 60) * 1000))
    with pytest.raises(Denied, match="Nobody approved the move in time"):
        grants_cli.rebind(ARGS, ctx("governed", node))


def test_the_node_ending_the_request_without_a_tap_ends_the_command():
    node = Node(pending=0, ends="That move ended without a tap (denied): ask again if the session is still to move.")
    with pytest.raises(Denied, match="ended without a tap"):
        grants_cli.rebind(ARGS, ctx("governed", node))


def test_a_device_in_no_pool_still_asks_for_the_typed_yes():
    with pytest.raises(AssertionError, match="a typed yes was asked for"):
        grants_cli.rebind(ARGS, ctx("unfounded", Node(pending=0)))

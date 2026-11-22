"""What an asker is told when the node does not make the secret or the ticket at once, and what the pool's status lists of a node that asks its user.

The launcher starts a node that asks nobody (`--presence auto`), so every ask is confirmed as it is made and these words are never read in a run
of the product. A node somebody started by hand to ask its user (`--presence os` or `terminal`) answers `asking`, `refused`, `expired` or `failed`:
nothing here waits for a person, so the ask is refused at once with the reason and the way out (start the node as the launcher does).

The node in these tests is a stand-in for the replies of the Rust node (the words are the ones its tests pin in ``app/poolhouse-node``): what is under
test is the Python side that turns them into what the agent reads.
"""

from __future__ import annotations

import time

import pytest

from poolhouse.api import _node, pool
from poolhouse.board import presence
from poolhouse.board.client import Denied

SENTENCE = "Poolhouse - make the pool home. Say yes only if you are founding it now."


class Node:
    """A node that answers every ``presence_ask`` with ``reply`` and records what it was asked."""

    def __init__(self, reply: dict) -> None:
        self.reply, self.asked = reply, []

    def call(self, method: str, *_args, **params) -> dict:
        assert method == "presence_ask", f"nothing is polled: {method}"
        self.asked.append(params)
        return self.reply


@pytest.mark.parametrize("state", ["asking", "refused", "expired", "failed"])
def test_a_node_that_does_not_confirm_is_refused_at_once_with_the_way_out_and_nothing_is_waited_for(state):
    node = Node({"state": state, "presence": "os", "sentence": SENTENCE})
    began = time.monotonic()
    with pytest.raises(Denied) as refused:
        presence.ask_card(node, "token", "pool_key", presence.Card("c" * 64))
    said = str(refused.value)
    assert state in said and "start it as the launcher does, which asks nobody" in said and "--presence" in said, said
    assert len(node.asked) == 1, "asked once and not again"
    assert time.monotonic() - began < 1, "no wait for a person"


def test_a_confirmed_reply_without_a_secret_is_no_secret():
    with pytest.raises(Denied, match="confirmed"):
        presence.ask(Node({"state": "confirmed", "presence": "auto"}), "token", "phone")
    said = presence.ask(Node({"state": "confirmed", "presence": "auto", "secret": "ticket"}), "token", "phone")
    assert said["secret"] == "ticket"


def test_the_pools_status_lists_every_request_that_waits_for_a_user_word_for_word(monkeypatch):
    raw = {"pool": "p", "policy": "secure", "fingerprint": "f" * 64, "members": [], "governance": {"mode": "unfounded", "say": "This device is in no pool yet."},
           "waiting": [{"by": "pool/claude-a", "say": f"waiting for you: {SENTENCE}", "sentence": SENTENCE, "via": "os", "left_ms": 90_000}]}
    monkeypatch.setattr(_node, "call", lambda *_a, **_k: raw)
    assert pool.status().waiting == (f"waiting for you: {SENTENCE}",)
    monkeypatch.setattr(_node, "call", lambda *_a, **_k: {k: v for k, v in raw.items() if k != "waiting"})
    assert pool.status().waiting == ()

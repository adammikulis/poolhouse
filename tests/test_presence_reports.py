"""What an asker and the person are told about a request that needs the person's answer: a no, a question nobody answered and a prompt that could
not be shown are three different things, and a request that waits is listed in the pool's status for as long as it waits.

The node here is a stand-in for the node's replies to ``presence_ask`` and ``presence_status`` (the words below are the ones the Rust node's
tests pin in ``app/poolhouse-node``); what is under test is the Python side that turns them into what the agent and the person read.
"""

from __future__ import annotations

import pytest

from poolhouse.api import _node, pool
from poolhouse.board import presence
from poolhouse.board.client import Denied

SENTENCE = "Poolhouse - make the pool home. Say yes only if you are founding it now."


class Node:
    """The replies of a node to a question: ``asked`` to ``presence_ask``, then each of ``later`` to ``presence_status``."""

    def __init__(self, asked: dict, *later: dict) -> None:
        self.replies = [asked, *later]

    def call(self, method: str, *_args, **_params) -> dict:
        assert method in ("presence_ask", "presence_status")
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def asked(node: Node, voice: presence.Voice = presence.SILENT) -> dict:
    return presence.ask_card(node, "token", "pool_key", presence.Card("c" * 64), voice)


def test_a_dialog_that_could_not_be_shown_is_told_as_a_failure_with_the_systems_words_and_not_as_a_no():
    node = Node({"state": "failed", "error": "The system's password prompt did not open (exit status 1): execution error (-1750)", "presence": "os"})
    with pytest.raises(Denied) as failure:
        asked(node)
    said = str(failure.value)
    assert "-1750" in said and "not a no" in said and "You said no" not in said, said
    assert "tell the person" in said, "the asker is told what to do next, not to wait a time"


def test_a_question_nobody_answered_is_not_a_no_and_may_be_asked_again_at_once():
    node = Node({"state": "expired", "presence": "os"})
    with pytest.raises(Denied, match="Nobody said yes") as expired:
        asked(node)
    assert "not a no" in str(expired.value) and "minutes" not in str(expired.value)


def test_only_a_refusal_is_told_as_the_persons_no():
    with pytest.raises(Denied, match="You said no"):
        asked(Node({"state": "refused", "presence": "os"}))


def test_the_person_is_told_the_sentence_they_are_asked_and_where_to_answer_it(monkeypatch):
    monkeypatch.setattr(presence, "POLL_S", 0)
    told: list[str] = []
    node = Node({"state": "asking", "presence": "os", "sentence": SENTENCE}, {"state": "confirmed", "presence": "os", "secret": "ticket"})
    assert asked(node, presence.Voice(terminal="", say=told.append))["secret"] == "ticket"
    assert len(told) == 1 and told[0].startswith(f"Waiting for you: {SENTENCE}") and "password or fingerprint" in told[0], told


def test_the_pools_status_lists_every_request_that_waits_for_the_person_word_for_word(monkeypatch):
    raw = {"pool": "p", "policy": "secure", "fingerprint": "f" * 64, "members": [], "governance": {"mode": "unfounded", "say": "This device is in no pool yet."},
           "waiting": [{"by": "pool/claude-a", "say": f"waiting for you: {SENTENCE}", "sentence": SENTENCE, "via": "os", "left_ms": 90_000}]}
    monkeypatch.setattr(_node, "call", lambda *_a, **_k: raw)
    assert pool.status().waiting == (f"waiting for you: {SENTENCE}",)
    monkeypatch.setattr(_node, "call", lambda *_a, **_k: {k: v for k, v in raw.items() if k != "waiting"})
    assert pool.status().waiting == ()

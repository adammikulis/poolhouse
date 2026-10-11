"""Who reads, follows and answers a conversation on the node: its two ends, and no other session."""

from __future__ import annotations

import pytest

from poolhouse.board.client import NodeError
from poolhouse.errors import Denied
from poolhouse.workspace import board_cli, mail_cli

pytest_plugins = ["node_kit"]


@pytest.fixture
def team(workspace_node):
    """Two coordinators, an observer and an outsider on one board, each as its own session."""
    node = workspace_node
    for name in ("worker-a", "worker-b", "observer", "outsider"):
        setattr(node, name.replace("-", "_"), node.session(node.member(name)))
    return node


def ref(sent: dict) -> str:
    """The number of a message a post answered with."""
    return sent["id"].rsplit(":", 1)[-1]


def coordination(team) -> str:
    return ref(team.worker_a.post(team.worker_b.name, "note", "Review documentation", subject="Doc review"))


def test_a_conversation_is_discovered_and_read_by_its_two_ends_only(team):
    a, b, observer, outsider = team.worker_a, team.worker_b, team.observer, team.outsider
    root = coordination(team)
    b.post(a.name, "note", "Recommendation", reply_id=root)
    for end, other in ((a, b), (b, a)):
        assert [(c["a"], c["b"], c["messages"]) for c in mail_cli.conversations(end)] == [(*sorted((a.name, b.name)), 2)]
        assert len(end.read_all(conversation=other.name)) == 2 and len(end.read_all(thread=root)) == 2
    for stranger in (observer, outsider):
        assert mail_cli.conversations(stranger) == []
        assert stranger.read_all(conversation=a.name) == [] and stranger.read_all(conversation=b.name) == []
        assert stranger.read_all(inbox=True) == [] and stranger.read_all(mentions=True) == [] and stranger.read_all(digest=True) == []
        with pytest.raises(NodeError, match="no thread"):
            stranger.read_all(thread=root)
        with pytest.raises(NodeError):
            stranger.subscribe("thread", root)


def test_a_reader_gets_no_participant_reply_authority(team):
    root = coordination(team)
    with pytest.raises(Denied, match="participant"):
        team.observer.post(team.worker_b.name, "note", "Inject reply", reply_id=root)
    assert len(team.worker_a.read_all(thread=root)) == 1


def test_a_thread_subscription_of_a_participant_delivers_each_reply_once(team):
    a, b = team.worker_a, team.worker_b
    root = coordination(team)
    seen = b.read(inbox=True).cursor
    b.subscribe("thread", root, "inbox")
    reply = a.post(b.name, "note", "Update", reply_id=root)
    assert [e.id for e in b.read(seen, inbox=True).entries] == [reply["id"]]


def test_a_digest_subscription_of_a_participant_summarises_new_replies(team):
    a, b = team.worker_a, team.worker_b
    root = coordination(team)
    b.subscribe("thread", root, "digest")
    a.post(b.name, "note", "Ready", reply_id=root)
    summary = board_cli.collected(b, False)
    assert "Ready" in summary and "1 new" in summary


def test_a_digest_does_not_reveal_the_subject_of_a_root_its_reader_may_not_read(team):
    a, b, outsider = team.worker_a, team.worker_b, team.outsider
    root = ref(outsider.post(a.name, "note", "Foreign detail", subject="Foreign subject"))
    b.subscribe("agent", a.name, "digest")
    a.post(b.name, "note", "Project reply", reply_id=root)
    summary = board_cli.collected(b, False)
    assert "Project reply" in summary and "Foreign subject" not in summary and "Foreign detail" not in summary
    with pytest.raises(NodeError, match="no thread"):
        b.read_all(thread=root)


def test_a_thread_never_shows_a_participant_a_reply_it_may_not_read(team):
    a, b, outsider = team.worker_a, team.worker_b, team.outsider
    root = coordination(team)
    a.post(outsider.name, "note", "Separate detail", reply_id=root)
    assert [e.text for e in b.read_all(thread=root)] == ["Review documentation"]
    assert [e.text for e in a.read_all(thread=root)] == ["Review documentation", "Separate detail"]
    with pytest.raises(NodeError, match="no thread"):
        outsider.read_all(thread=root)
    assert [e.text for e in outsider.read_all(inbox=True)] == ["Separate detail"]

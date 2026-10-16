"""What one session may write to the node: sends in a minute, announcements, subagents."""

from __future__ import annotations

import pytest

from poolhouse.board.client import Quota

pytest_plugins = ["node_kit"]

NODE_WRITE_LIMITS: dict[str, int] = {}


@pytest.fixture
def team(workspace_node):
    workspace_node.lead = workspace_node.member("lead")
    return workspace_node


def test_messages_notes_and_claims_share_thirty_sends_a_minute_and_the_commands_say_it_is_a_quota(team):
    a = team.session(team.lead)
    for n in range(10):
        a.post("#general", "status", f"message {n}")
        a.note_add("fact", f"note {n}", "a note")
        a.claim("port", str(9600 + n))
    with pytest.raises(Quota, match="as fast as it may"):
        a.post("#general", "status", "one more")
    with pytest.raises(Quota, match="as fast as it may"):
        a.note_add("fact", "one more", "a note")
    with pytest.raises(Quota, match="as fast as it may"):
        a.claim("port", "9700")
    sent = team.cli("send", "#general", "status", "a command over the same limit", who=team.lead)
    assert sent.returncode == 4, (sent.stdout, sent.stderr)
    other = team.session(team.member("other"))
    other.post("#general", "status", "another session has its own count")


def test_an_announcement_over_what_a_session_announces_is_written_and_the_board_is_told(team):
    for n in range(8):
        done = team.cli("announce", "milestone", f"announcement {n}", "--json", who=team.lead)
        assert done.returncode == 0, (n, done.stderr, done.stdout)
    audit = team.session(team.lead).read_all(kind="audit")
    assert [e.fields["subject"] for e in audit if e.fields["event"] == "announce_over_quota"] == [team.lead.name] * 2


def test_a_session_has_sixteen_live_subagents_and_the_seventeenth_registers_as_a_quota(team):
    for n in range(16):
        team.member(f"child-{n}", parent=team.lead)
    with pytest.raises(Quota, match="16 live subagents"):
        team.member("child-16", parent=team.lead)
    team.session(team.members["child-0"]).retire()
    team.member("child-16", parent=team.lead)

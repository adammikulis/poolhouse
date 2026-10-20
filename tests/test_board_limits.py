"""What one session may write to the node: messages, notes, claims and announcements as fast as it sends them, and any number of subagents."""

from __future__ import annotations

import json

import pytest

pytest_plugins = ["node_kit"]


@pytest.fixture
def team(workspace_node):
    workspace_node.lead = workspace_node.member("lead")
    return workspace_node


def test_messages_notes_and_claims_are_written_as_fast_as_a_session_sends_them(team):
    a = team.session(team.lead)
    for n in range(40):
        a.post("#general", "status", f"message {n}")
        a.note_add("fact", f"note {n}", "a note")
        a.claim("port", str(9600 + n))
    sent = team.cli("send", "#general", "status", "a command after forty of each", who=team.lead)
    assert sent.returncode == 0, (sent.stdout, sent.stderr)


def test_announcements_are_posted_without_a_warning_however_many_a_session_makes(team):
    for n in range(12):
        done = team.cli("announce", "milestone", f"announcement {n}", "--json", who=team.lead)
        assert done.returncode == 0, (n, done.stderr, done.stdout)
        assert "warning" not in json.loads(done.stdout)
    audit = team.session(team.lead).read_all(kind="audit")
    assert [e for e in audit if e.fields["event"] == "announce_over_quota"] == []


def test_a_session_may_have_any_number_of_live_subagents(team):
    for n in range(24):
        team.member(f"child-{n}", parent=team.lead)
    team.session(team.members["child-0"]).retire()
    team.member("child-24", parent=team.lead)

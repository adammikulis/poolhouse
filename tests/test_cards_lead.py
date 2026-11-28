"""Who the cards answer to, on a real node: a release records the cards, a card runs only in a sandbox and for a live lead, no unattended agent
grades, a lead's grade comes from its own session, an ended run is settled by `cards wait` or the grade, and facts are saved only with accepted
work. Git is real; the runner's computer is `test_cards_run.Computer`, which gives its own run of a check. No model is served or asked."""

from __future__ import annotations

import pytest
from cards_kit import card, project as card_project
from delegate_kit import agent_names, said
from test_cards_run import Computer, ended, release

from poolhouse import (
    cards,
    cards_ledger,
    cards_run,
    delegate,
    delegate_confined,
    delegate_memory,
    delegate_record,
    delegate_store as store,
    isolation,
)

pytest_plugins = ["node_kit", "delegate_kit"]
ACCEPTING = ["grade", "run-1", "correct", "--critique", "the file is gone", "--check", "test ! -e junk.txt => exit 0"]


@pytest.fixture
def carded(lead_node):
    node, lead, checkout = lead_node
    root = card_project(checkout.resolve())
    card(root, "junk-card")
    return node, lead, root


@pytest.fixture
def ran(carded, monkeypatch):
    """A card's delegate that has ended with its work committed, which nothing has looked at since: the node, its lead, the project and the
    runner's computer, which `cards wait` and `grade` are given too."""
    node, lead, root = carded
    computer = Computer()
    computer.board = node.board
    host = computer.host()
    monkeypatch.setattr(delegate_record.jobs, "alive", lambda _kind, home: 4242)
    release(root, lead.name)
    assert cards_run.step(root, host)[0] == "launched"
    monkeypatch.setattr(cards_run, "Host", lambda *_a, **_k: host)
    ended("run-1")
    return node, lead, root, computer


def state(root) -> str:
    return cards_ledger.read(root)["cards"]["junk-card"]["state"]


def test_a_release_records_each_card_as_it_stands(carded, capsys):
    _node, _lead, root = carded
    assert delegate.main(["cards", "release", "--why", "go", "--project", str(root)]) == 0, said(capsys)
    [found] = cards.load(root)[0]
    assert cards_ledger.read(root)["release"]["cards"] == {"junk-card": cards_run.card_digest(root, found)}


def test_a_cards_delegate_does_not_start_where_nothing_can_sandbox_it(carded, detached, monkeypatch, capsys):
    node, lead, root = carded
    monkeypatch.setattr(delegate_confined, "unsandboxable", lambda: "this platform has no sandbox")
    assert delegate.main(["start", "--task", "work it", "--edit", "--project", str(root), "--card", "junk-card", "--model", "canned.gguf"]) == 2
    assert "only in a sandbox" in said(capsys) and agent_names(node, lead) == [] and detached == []


def test_a_lead_that_ended_is_seen_to_be_gone_and_is_not_registered_again(carded, monkeypatch):
    node, lead, _root = carded
    short = node.member("short-lead")
    asked = {"lead": short.name, "board": node.board}
    assert cards_run.lead_gone(asked) == ""
    node.session(short).retire()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", short.session)   # what a runner the ended lead started still holds
    assert short.name in cards_run.lead_gone(asked)
    assert [each.retired for each in node.session(lead).agents(True) if each.name == short.name] == [True]


def test_cards_wait_settles_an_ended_run_that_no_runner_polls(ran, capsys):
    _node, _lead, root, computer = ran
    assert delegate.main(["cards", "wait", "--project", str(root)]) == 0
    assert "review branch delegate/run-1" in said(capsys) and state(root) == "review" and computer.rechecked


def test_a_grade_settles_the_ended_run_it_grades(ran, capsys):
    _node, _lead, root, computer = ran
    assert delegate.main(ACCEPTING) == 0, said(capsys)
    assert state(root) == "accepted" and computer.rechecked


def test_no_unattended_agent_grades(ran, monkeypatch, capsys):
    _node, lead, root, _computer = ran
    monkeypatch.setattr(isolation.ancestry, "unattended", lambda: True)
    assert delegate.main([*ACCEPTING, "--as", lead.name]) == 2, said(capsys)
    assert store.graded("run-1")["grades"] == [] and state(root) == "running"


def test_a_leads_grade_comes_from_its_own_session_and_from_no_other_that_names_it(ran, monkeypatch, capsys):
    node, lead, root, _computer = ran
    other = node.member("another-session")
    for native in (other.session, ""):   # another session, and a process that dropped its identity
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", native)
        assert delegate.main([*ACCEPTING, "--as", lead.name]) == 2
        assert "its own live session" in said(capsys) and store.graded("run-1")["grades"] == [] and state(root) == "running"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", lead.session)
    assert delegate.main(ACCEPTING) == 0, said(capsys)
    assert state(root) == "accepted"


def test_work_that_was_sent_back_saves_no_fact_whatever_its_grade(ran, capsys):
    _node, _lead, root, computer = ran
    computer.fails = "its check does not pass"
    delegate_memory.propose("run-1", "the parser lives in one module")
    digest = delegate_memory.proposals("run-1")[0]
    assert delegate.main([*ACCEPTING, "--facts", digest]) == 0
    heard = said(capsys)
    assert "has not accepted" in heard and state(root) == "returned" and delegate_memory.held("run-1")["saved"] is None, heard


def test_the_result_and_the_record_show_every_proposed_fact(ran, capsys):
    delegate_memory.propose("run-1", "the parser lives in one module")
    for words in (["result", "run-1"], ["record", "run-1"]):
        assert delegate.main(words) == 0
        assert "the parser lives in one module" in said(capsys), words

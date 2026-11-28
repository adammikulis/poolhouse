"""A session card end to end: the lead releases the runner, the runner starts a real delegate in the real confinement, the delegate recalls and
proposes a fact, asks its lead and runs the check, the lead is told, grades it, and only then is the fact saved. The model side is the kit's marked
canned server and the harness a marked program (`test_delegate_confined.HARNESS`, NOT A MODEL and NOT OPENCODE)."""

from __future__ import annotations

import pytest
from cards_kit import card, project as card_project
from delegate_kit import agent_names, said
from test_delegate_confined import CANNED, answering, editing  # noqa: F401 - the fixture

from poolhouse import (
    cards,
    cards_ledger,
    cards_run,
    delegate,
    delegate_memory,
    delegate_run,
    delegate_store as store,
    harnessing,
    isolation,
)
from poolhouse.memory.project import detect
from poolhouse.memory.store import Setup, Store
from tests import memory_keys
from tests.isolation_world import git

pytest_plugins = ["node_kit", "delegate_kit", "tests.confined_kit"]
ring = memory_keys.ring
MODEL = f"{harnessing.PREFERRED_MODEL}.gguf"


def host(**more) -> cards_run.Host:
    """The runner on a computer whose card model is served and unused; its delegates start with one pass and no self-check."""
    def start(words: list[str]) -> int:
        return delegate.main([*words, *(("--no-review", "--resumes", "0") if words[0] == "start" else ())])

    return cards_run.Host(start, status=lambda: {"claims": {}, "servers": []},
                          servers=lambda: {8080: {"model": MODEL, "context": 262_144, "parallel": 1}}, live=lambda _entry: True,
                          measuring=lambda: None, busy=lambda _url: False, queued=lambda _url: 0, idle_s=0.0, **more)


def remembered(root, text: str = "") -> list[str]:
    mine = Store(setup=Setup(project=detect(explicit=root)))
    try:
        if text:
            mine.add(text, "note", "agent-observed")
        return [fact.text for fact in mine.facts()]
    finally:
        mine.close()


def test_a_card_is_run_asked_about_reported_graded_and_only_then_remembered(editing, capsys):  # noqa: F811
    node, lead, checkout = editing
    root = card_project(checkout)
    card(root, "junk-card", task="mode:card Delete junk.txt.", closes=True)
    git(root, "add", "HANDOFF.md", "AGENTS.md", "docs", "junk.txt", "left.txt")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "cards")
    remembered(root, "junk.txt is listed by no other file")
    assert delegate.main(["cards", "release", "--why", "the speed changes are in", "--project", str(root)]) == 0, said(capsys)
    assert cards_ledger.read(root)["release"]["lead"] == lead.name
    kind, text = cards_run.step(root, host())
    assert kind == "launched", (text, said(capsys))
    [name] = agent_names(node, lead)
    rec = store.load(name)
    assert (rec.lead, rec.card, rec.memory, rec.branch) == (lead.name, "junk-card", True, f"delegate/{name}")
    assert "junk.txt is listed by no other file" in rec.message() and rec.message().startswith(f"You are {name}.")
    asked = answering(node, lead, name, "the root one")
    status = delegate_run.run(name)
    asked.join(30)
    asked.done.set()
    row = store.result(name)
    assert status == 0, row
    answer = row["text"]
    assert CANNED in answer and f"{lead.name} answered:" in answer and "the root one" in answer
    for tool in ("lead_ask", "lead_note", "lead_recall", "lead_remember"):
        assert tool in answer.split("offered=")[1].split(" | ")[0]
    assert "junk.txt is listed by no other file" in answer.split("recalled=")[1].split(" | proposed=")[0]
    assert "saved only if your lead's review accepts" in answer.split("proposed=")[1].split(" | ")[0]
    assert "the root module is at the top" not in remembered(root), "nothing is saved before the lead accepts"
    told = [(e.fields.get("type"), e.fields.get("subject", "")) for e in node.session(lead).read(inbox=True, limit=100).entries
            if e.sender == name]
    assert ("question", "question") in told and ("handoff", "delegate done (review waiting)") in told, told
    assert cards_run.step(root, host())[0] == "settled"
    entry = cards_ledger.read(root)["cards"]["junk-card"]
    assert entry["state"] == "review" and entry["commit"] == row["tree"]["commit"], entry
    assert name in cards_run.needs(root)[0]
    assert delegate.main(["grade", name, "correct", "--critique", "the file is gone", "--check", "test ! -e junk.txt => exit 0"]) == 0
    heard = said(capsys)
    assert "card junk-card accepted" in heard and "1 of 1 proposed fact(s) saved" in heard, heard
    assert "the root module is at the top" in remembered(root)
    assert cards_ledger.read(root)["cards"]["junk-card"]["state"] == "accepted"
    assert delegate_memory.held(name)["saved"] and (root / "junk.txt").exists(), "the work waits on its branch for the lead's pull request"
    assert not cards.problems(root)


@pytest.mark.parametrize("words", [["release", "--why", "go"], ["hold", "--why", "stop"], ["retry", "junk-card"], ["run", "--once"]])
def test_a_delegate_and_what_it_starts_can_neither_release_hold_retry_nor_run_the_cards(tmp_path, monkeypatch, capsys, words):
    root = card_project(tmp_path / "project")
    card(root, "junk-card")
    monkeypatch.setattr(isolation.ancestry, "unattended", lambda: True)
    assert delegate.main(["cards", *words, "--project", str(root)]) == 2
    assert cards_ledger.read(root)["release"] == {} and cards_ledger.read(root)["hold"] == "", said(capsys)

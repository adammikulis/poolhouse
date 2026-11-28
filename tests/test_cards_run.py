"""The card runner: held until a lead releases it, a card only on an unused served model, one at a time, outcomes read from the run's own record,
and a lead's grade deciding what happens to the work. What the computer says of its servers is given here; no model is served or asked."""

from __future__ import annotations

import hashlib
from itertools import pairwise
from pathlib import Path

import pytest
from cards_kit import card, project

from poolhouse import (
    cards_ledger,
    cards_run,
    delegate_record,
    delegate_store as store,
    harnessing,
    jobs,
)

MODEL = f"{harnessing.PREFERRED_MODEL}.gguf"
SERVER = {"model": MODEL, "context": 262_144, "parallel": 1}


class Computer:
    """What the runner is told: its servers and holders, and the command lines it started and stopped delegates by."""

    def __init__(self) -> None:
        self.holders: list[dict] = []
        self.claims: dict = {}
        self.servers = {8080: dict(SERVER)}
        self.processing, self.waiting, self.now = False, 0, 1000.0
        self.asked: list[list[str]] = []
        self.refuses = False

    def start(self, words: list[str]) -> int:
        self.asked.append(words)
        if words[0] != "start" or self.refuses:
            return 2 if self.refuses else 0
        given = dict(pairwise(words))
        name = f"run-{len(self.asked)}"
        store.save(store.Delegate(name, given["--as"], "board", given["--task"], given["--project"], given["--model"], card=given["--card"]))
        store.set_status(name, "working")
        jobs.record(delegate_record.JOB, pid=4242, argv=[], log="", home=store.folder(name))
        return 0

    def host(self, idle_s: float = 0.0) -> cards_run.Host:
        return cards_run.Host(
            self.start, status=lambda: {"claims": self.claims, "servers": [{"port": 8080, "holders": self.holders}]},
            servers=lambda: self.servers, live=lambda _entry: True, measuring=lambda: None, busy=lambda _url: self.processing,
            queued=lambda _url: self.waiting, clock=lambda: self.now, sleep=lambda _s: None, idle_s=idle_s)


@pytest.fixture
def root(tmp_path, monkeypatch):
    found = project(tmp_path / "project")
    card(found, "junk-card")
    card(found, "left-card", row="A paused row", write='["left.txt"]', check="test ! -e left.txt")
    monkeypatch.setattr(delegate_record.jobs, "alive", lambda _kind, home: 4242)   # the runners of these records are not processes
    return found


def release(root: Path, lead: str = "lead-main", tiers: tuple[str, ...] = (), stated: str = "") -> None:
    digest = hashlib.sha256(stated.encode()).hexdigest()[:16]
    cards_ledger.edit(root, lambda ledger: ledger.update(release={"lead": lead, "tiers": list(tiers), "hold": digest}, hold=""))


def states(root: Path) -> dict[str, str]:
    return {each.card.id: each.state for each in cards_run.standing(root)}


def ended(name: str, state: str = "done", *, commit: str = "abc123", checked: int | None = 0, peak: int = 50_000,  # noqa: PLR0913
          edited: bool = False) -> None:
    """The record a delegate's runner leaves: a step of ``peak`` tokens, the check with exit ``checked``, an edit after it, and the result."""
    delegate_record.append(name, "usage", input_tokens=peak - 1000, cache_read_tokens=500, output_tokens=500)
    if checked is not None:
        delegate_record.append(name, "result", tool="shell", command="test ! -e junk.txt", exit=checked, status="completed")
    if edited:
        delegate_record.append(name, "result", tool="edit", file="junk.txt", status="completed")
    store.save_result(name, {"state": state, "input_tokens": 9, "output_tokens": 3, "cache_read_tokens": 1, "error": "",
                             "tree": {"branch": f"delegate/{name}", "commit": commit}})
    store.set_status(name, state)


def test_nothing_starts_until_a_lead_releases_the_runner_and_a_new_stated_reason_holds_it_again(root):
    computer = Computer()
    assert cards_run.step(root, computer.host())[0] == "held"
    (root / "docs" / "cards" / "hold.txt").write_text("No work until the speed changes land.\n", encoding="utf-8")
    kind, text = cards_run.step(root, computer.host())
    assert kind == "held" and "speed changes land" in text and computer.asked == []
    release(root, stated="No work until the speed changes land.")
    assert cards_run.step(root, computer.host())[0] == "launched"
    (root / "docs" / "cards" / "hold.txt").write_text("A new reason.\n", encoding="utf-8")
    assert cards_run.held(root, cards_ledger.read(root)) == "A new reason."


def test_a_paused_tier_is_worked_only_when_the_release_names_it(root):
    release(root)
    assert states(root) == {"junk-card": "ready", "left-card": "paused"}
    release(root, tiers=("5",))
    assert states(root) == {"junk-card": "ready", "left-card": "ready"}


def test_a_card_starts_on_a_branch_bound_to_the_releasing_lead_with_memory_and_only_one_runs(root):
    release(root, "lead-two")
    computer = Computer()
    assert cards_run.step(root, computer.host()) == ("launched", "junk-card: started run-1 for lead-two")
    words = computer.asked[0]
    given = dict(pairwise(words))
    assert (given["--as"], given["--card"], given["--model"]) == ("lead-two", "junk-card", MODEL) and "--edit" in words and "--memory" in words
    assert "--in-place" not in words and "--no-review" not in words and "Your lead is lead-two" in given["--task"]
    assert cards_run.step(root, computer.host())[0] == "running" and len(computer.asked) == 1
    assert states(root)["junk-card"] == "running"


@pytest.mark.parametrize(("change", "said"), [
    ({"holders": [{"pid": 77, "label": "poolhouse-opencode in a session"}]}, "in use by poolhouse-opencode"),
    ({"claims": {"gpu-training": {"info": {"purpose": "a training run"}}}}, "a training run"),
    ({"processing": True}, "answering a request"),
    ({"processing": None}, "answering a request"),
    ({"waiting": 1}, "answering a request"),
    ({"servers": {}}, "never loads a model"),
    ({"servers": {8080: {**SERVER, "context": 131_072}}}, "never loads a model"),
    ({"servers": {8080: {**SERVER, "model": "Qwen3.8-27B-UD-Q3_K_XL.gguf"}}}, "never loads a model"),
])
def test_a_model_that_is_used_claimed_or_not_served_with_room_starts_no_card(root, change, said):
    release(root)
    computer = Computer()
    for name, value in change.items():
        setattr(computer, name, value)
    kind, text = cards_run.step(root, computer.host())
    assert (kind, computer.asked) == ("waiting", []) and said in text


def test_a_holder_that_only_keeps_the_server_up_is_no_use_and_the_model_has_to_stay_unused_a_while(root):
    release(root)
    computer = Computer()
    computer.holders = [{"pid": 5, "label": "poolhouse-serve up Qwen.gguf"}]
    host = computer.host(idle_s=600)
    assert cards_run.step(root, host)[0] == "waiting"
    computer.now += 599
    assert cards_run.step(root, host)[0] == "waiting"
    computer.processing = True
    assert cards_run.step(root, host)[0] == "waiting"
    computer.processing = False
    computer.now += 599
    assert cards_run.step(root, host)[0] == "waiting", "the use in between starts the wait again"
    computer.now += 600
    assert cards_run.step(root, host)[0] == "launched"


def test_the_runner_gives_the_model_up_to_someone_else_and_the_card_is_ready_again(root):
    release(root)
    computer = Computer()
    host = computer.host()
    cards_run.step(root, host)
    computer.holders = [{"pid": 4242, "label": "its own"}]
    assert cards_run.step(root, host)[0] == "running"
    computer.holders.append({"pid": 77, "label": "the app's chat"})
    kind, text = cards_run.step(root, host)
    assert kind == "waiting" and "the app's chat" in text and computer.asked[-1] == ["stop", "run-1"]
    assert states(root)["junk-card"] == "ready"


@pytest.mark.parametrize(("how", "state", "why"), [
    ({}, "review", ""),
    ({"checked": 1}, "returned", "did not pass"),
    ({"checked": None}, "returned", "never ran the check"),
    ({"edited": True}, "returned", "files changed after it"),
    ({"commit": ""}, "returned", "changed nothing"),
    ({"state": "failed"}, "returned", "ended failed"),
    ({"peak": 215_000}, "returned", "oversize"),
])
def test_an_ended_run_waits_for_review_only_when_its_record_shows_the_check_passing(root, how, state, why):
    release(root)
    computer = Computer()
    host = computer.host()
    cards_run.step(root, host)
    ended("run-1", **how)
    assert cards_run.step(root, host)[0] == "settled"
    entry = cards_ledger.read(root)["cards"]["junk-card"]
    assert entry["state"] == state and why in entry["why"], entry
    assert entry["peak"] == how.get("peak", 50_000) and entry["tokens"]["input_tokens"] == 9 and entry["memory"] is True
    assert "junk-card" in cards_run.needs(root)[0]
    assert cards_run.step(root, host)[0] == "idle", "neither is started again by itself"


def test_a_returned_card_is_ready_again_once_its_file_changes(root):
    release(root)
    computer = Computer()
    cards_run.step(root, computer.host())
    ended("run-1", checked=1)
    cards_run.step(root, computer.host())
    assert states(root)["junk-card"] == "returned"
    card(root, "junk-card", task="Delete junk.txt, and nothing else.")
    assert states(root)["junk-card"] == "ready"


def test_a_checked_grade_accepts_the_work_and_a_lower_one_returns_the_reasons_to_the_next_run(root):
    release(root)
    computer = Computer()
    cards_run.step(root, computer.host())
    ended("run-1")
    cards_run.step(root, computer.host())
    graded = lambda verdict, verified, name="run-1": cards_ledger.graded(   # noqa: E731
        root, "junk-card", name, {"verdict": verdict, "verified": verified, "critique": "the other file went too"})
    assert graded("correct", False).endswith("(--check)") and states(root)["junk-card"] == "review"
    assert graded("wrong", True, "another-run") == ""
    assert "rejected" in graded("partly", True) and states(root)["junk-card"] == "ready"
    assert cards_run.step(root, computer.host())[0] == "launched"
    words = computer.asked[-1]
    assert "- partly: the other file went too" in words[words.index("--task") + 1]
    ended("run-2")
    cards_run.step(root, computer.host())
    assert "accepted" in graded("correct", True, "run-2") and states(root)["junk-card"] == "accepted"
    assert cards_run.step(root, computer.host())[0] == "idle" and cards_run.needs(root) == []


def test_a_delegate_that_cannot_be_started_holds_the_runner_until_a_lead_releases_it_again(root):
    release(root)
    computer = Computer()
    computer.refuses = True
    kind, text = cards_run.step(root, computer.host())
    assert kind == "held" and "releases the runner again" in text
    assert cards_run.step(root, computer.host())[0] == "held" and len(computer.asked) == 1


def test_the_listing_the_web_page_shows_holds_each_card_and_why_it_waits(root):
    shown = cards_run.listing(root)
    assert cards_ledger.published()["cards"] == shown["cards"] and shown["held"].startswith("no lead has released")
    assert [(row["id"], row["state"]) for row in shown["cards"]] == [("junk-card", "ready"), ("left-card", "paused")]
    assert shown["cards"][0]["budget"] > 0

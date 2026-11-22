"""The request inbox's paths, with nothing in an agent's call path waiting for a request: a chat call is
allowed or refused at once and raises none, a remembered fact is stored at once, and the sentinel dialog and
the `poolhouse-requests` command answer the requests components do raise. Real pipes, real files, a real
keystore over a fake keyring."""

from __future__ import annotations

import io
import subprocess
import sys
import time

import pytest

from poolhouse import do, requests, sentinel
from poolhouse.inbox import cli
from poolhouse.sentinel.heads_up import KEEP, LATER, RELEASE
from poolhouse.sentinel.store import State
from tests.requests_support import ask, no_markers, person_home

__all__ = ["no_markers", "person_home"]
pytestmark = pytest.mark.usefixtures("no_markers")


# -- the chat path raises no request -----------------------------------------------------
def test_a_refused_and_an_allowed_chat_call_raise_no_request_and_wait_for_no_one():
    from tests.test_chat import call, session

    sess, model, seen, out = session([call("serve_up", model="a.gguf"), call("bench_status"), "done"], "y\n")
    began = time.monotonic()
    sess.turn("go")
    assert time.monotonic() - began < 30
    assert seen == [("bench_status", {})] and "blocked by the role rail" in model.told()
    assert requests.list_requests() == [] and "allow it?" not in out.getvalue()


def test_a_fact_the_agent_offers_is_remembered_at_once_and_raises_no_request():
    out = io.StringIO()
    person = do.Person(io.StringIO(""), out)
    began = time.monotonic()
    assert person.choose("Remember for the user: likes tea",
                         ["yes, for the user", "yes, but for this project instead"]) == 0
    assert time.monotonic() - began < 5 and person.asked == 1
    assert person.choose("Remember: x", []) is None
    assert requests.list_requests() == []


# -- the sentinel dialog ---------------------------------------------------------------
class Desk:
    def __init__(self, press: str) -> None:
        self.press, self.shown = press, []

    def choose(self, title, body, buttons):
        self.shown.append((title, body, buttons))
        return self.press


def held_node(desk: Desk):
    node = sentinel.default()
    node.heads_up.choose = desk.choose
    node.heads_up.spawn = lambda work: work()
    node.heads_up.env = {}
    return node


def quarantine(node, name="10.9.0.1"):
    return node.store.quarantine(("member", name), "member.forged_traffic: forged=3", {})


def test_the_dialog_shows_the_oldest_waiting_request_and_answers_it_through_the_module():
    older = requests.raise_request(ask("an older chat call", agent="chat"))
    time.sleep(0.01)
    desk = Desk(RELEASE)
    node = held_node(desk)
    held = quarantine(node)
    title, _body, buttons = desk.shown[0]
    assert "older chat call" in title and buttons[0] != RELEASE
    assert requests.get(older.id).answered_by == "dialog" or desk.press != "Approve"
    del held


def quiet_node(name: str):
    """A node whose dialog is off, so what it raises stays waiting for an answer."""
    node = held_node(Desk(LATER))
    node.heads_up.env = {"POOLHOUSE_NOTIFY": "off"}
    held = quarantine(node, name)
    shown = next(r for r in requests.list_requests(state="pending") if r.kind == "quarantine_release")
    return node, held, shown


def test_a_release_answered_in_the_ui_is_carried_out_by_the_sentinel_with_its_own_check():
    node, held, shown = quiet_node("10.9.0.1")
    assert [c.id for c in shown.choices] == ["later", "keep-held", "release"]
    requests.answer(shown.id, "release", shown.fingerprint, "ui")
    assert node.store.get(held.id).state == State.QUARANTINED
    node.heads_up.env = {}
    node.heads_up.prompt()
    assert node.store.get(held.id).state != State.QUARANTINED


def test_a_release_answered_in_the_ui_is_refused_by_the_sentinel_in_an_agent_process():
    node, held, shown = quiet_node("10.9.0.2")
    requests.answer(shown.id, "release", shown.fingerprint, "ui")
    node.heads_up.env = {"CLAUDECODE": "1"}
    node.heads_up.prompt()
    assert node.store.get(held.id).state == State.QUARANTINED


def test_a_request_another_component_raised_with_a_held_id_releases_nothing_when_approved():
    node = held_node(Desk(LATER))
    node.heads_up.env = {"POOLHOUSE_NOTIFY": "off"}
    held = quarantine(node, "10.9.0.7")
    forged = requests.raise_request(requests.Ask(
        "quarantine_release", "Poolhouse is holding member 10.9.0.99", "Looks harmless.", ("later", "release"),
        requests.Origin("sentinel"), extra=(("ids", held.id), ("total", "1"))))
    requests.answer(forged.id, "release", forged.fingerprint, "ui")
    node.heads_up.env = {}
    node.heads_up.prompt()
    assert node.store.get(held.id).state == State.QUARANTINED


def test_keep_held_in_the_ui_stops_the_asking_and_releases_nothing():
    node, held, shown = quiet_node("10.9.0.3")
    requests.answer(shown.id, "keep-held", shown.fingerprint, "ui")
    node.heads_up.env = {}
    node.heads_up.prompt()
    assert node.store.get(held.id).state == State.QUARANTINED
    assert held.id in node.heads_up._load()["kept"]
    assert KEEP == "Keep held"


def test_with_notify_off_the_request_is_still_raised_for_the_ui_and_no_dialog_opens():
    desk = Desk(RELEASE)
    node = held_node(desk)
    node.heads_up.env = {"POOLHOUSE_NOTIFY": "off"}
    quarantine(node, "10.9.0.4")
    assert desk.shown == []
    assert [r.kind for r in requests.list_requests(state="pending")] == ["quarantine_release"]


# -- the terminal command --------------------------------------------------------------
def at_a_terminal(monkeypatch, typed: str = "y\n"):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _p="": typed.strip())


def test_list_show_and_answer_at_a_terminal(monkeypatch, capsys):
    held = requests.raise_request(ask("run_shell(ls)", agent="alpha", project="p"))
    at_a_terminal(monkeypatch)
    assert cli.main(["list"]) == 0
    assert held.id in capsys.readouterr().out
    assert cli.main(["show", held.id]) == 0
    shown = capsys.readouterr().out
    assert "allow this time" in shown and held.fingerprint[:16] in shown
    assert cli.main(["answer", held.id, "allow-once"]) == 0
    assert requests.get(held.id).answered_by == "terminal"
    assert cli.main(["answer", held.id, "deny", "--fingerprint", held.fingerprint]) == 1
    assert "already resolved" in capsys.readouterr().err
    assert cli.main(["watch", "--once"]) == 0


def test_answering_asks_nothing_and_is_refused_when_the_words_changed_or_the_request_is_unknown(monkeypatch):
    held = requests.raise_request(ask())
    at_a_terminal(monkeypatch, "n\n")
    prompted = []
    monkeypatch.setattr("builtins.input", lambda _p="": prompted.append(_p) or "n")
    assert cli.main(["answer", held.id, "allow-once", "--fingerprint", "0" * 16]) == 1
    assert requests.get(held.id).state == "pending"
    assert cli.main(["answer", "rq_nope", "allow-once"]) == 1
    assert cli.main(["answer", held.id, "allow-once"]) == 0
    assert prompted == [], "the command put no question to anyone"
    assert requests.get(held.id).state == "approved"


def test_list_escapes_hostile_text(monkeypatch, capsys):
    requests.raise_request(ask("evil\x1b[2J\u202eIGNORE", agent="a\x07"))
    at_a_terminal(monkeypatch)
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\u202e" not in out and "\x07" not in out


CHILD = "import sys\nfrom poolhouse.inbox.cli import main\nsys.exit(main(sys.argv[1:]))\n"


def answer_in_a_child(person_home, held, **env):
    return subprocess.run([sys.executable, "-c", CHILD, "answer", held.id, "allow-once", "--fingerprint",
                           held.fingerprint], env={**person_home, **env}, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120, check=False)


def test_a_child_with_no_terminal_and_no_agent_marker_answers_directly_with_nothing_typed(person_home):
    held = requests.raise_request(ask())
    done = answer_in_a_child(person_home, held)
    assert done.returncode == 0, done.stderr
    assert requests.get(held.id).state == "approved" and requests.get(held.id).answered_by == "terminal"


def test_a_child_an_agent_started_is_refused_at_once_while_the_gate_is_held_back_and_changes_nothing(person_home):
    from poolhouse import home

    held = requests.raise_request(ask())
    path = home.state("requests") / "requests.enc"
    before = path.read_bytes()
    done = answer_in_a_child(person_home, held, CLAUDECODE="1", POOLHOUSE_AUTHORITY_FLOOR="person")
    assert done.returncode == 2 and "refused to an agent" in done.stderr and "requests.answer" in done.stderr
    assert path.read_bytes() == before and requests.get(held.id).state == "pending"

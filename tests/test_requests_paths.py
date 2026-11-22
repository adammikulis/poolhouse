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

from poolhouse import do, requests
from poolhouse.inbox import cli
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

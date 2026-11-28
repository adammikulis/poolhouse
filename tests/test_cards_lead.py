"""Who the cards answer to, on a real node: a release records the cards, a card runs only in a sandbox and for a live lead, no unattended agent
grades, a lead's grade comes from its own session, an ended run is settled by `cards wait` or the grade, and facts are saved only with accepted
work. Git is real; the runner's computer is `test_cards_run.Computer`, which gives its own run of a check. No model is served or asked."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

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
    harnessing,
    isolation,
    jobs,
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


DETACHED = """
import sys
sys.path[:0] = %(paths)r
import pytest  # a test run: the state root may be moved to the node of the test
import json, os, time
from pathlib import Path
from poolhouse import cards_run, delegate, delegate_store as store, jobs, node_launch
from poolhouse.isolation import ancestry
deadline = time.monotonic() + 30
while os.getppid() == int(sys.argv[1]) and time.monotonic() < deadline:   # the command that started it has not ended yet
    time.sleep(0.02)
node_launch.default_state = lambda: Path(%(state)r)


def detach(module, argv, **more):   # the delegate's own runner is recorded and not run: nothing leases a model
    jobs.record(more["kind"], pid=os.getpid(), argv=argv, log=str(more["log"]), home=more["home"], refuse_if_alive=False)
    return jobs.Detached(pid=os.getpid(), log=more["log"], command=(module, *argv), started="now")


jobs.detach = detach
host = cards_run.Host(delegate.main, status=lambda: {"claims": {}, "servers": []},
                      servers=lambda: {8080: {"model": %(model)r, "context": 262144, "parallel": 1}}, live=lambda _entry: True,
                      measuring=lambda: None, busy=lambda _url: False, queued=lambda _url: 0, takes=lambda _model: True, idle_s=0.0)
placed = ancestry.attest().agent   # asked, as the widening is, before a start registers the card delegate's session from this process
widened = delegate.main(["start", "--task", "widened", "--edit", "--project", %(root)r, "--model", %(model)r, "--as", %(lead)r])
step = cards_run.step(Path(%(root)r), host)
made = [store.load(name) for name in store.names()]
print("RESULT " + json.dumps({"placed": placed, "step": step, "widened": widened,
                              "made": [[each.lead, each.source, each.sandboxed, each.card] for each in made]}), flush=True)
"""
STARTS = """
import os, subprocess, sys
subprocess.Popen([sys.executable, "-c", sys.argv[1], str(os.getpid())], start_new_session=True, stdin=subprocess.DEVNULL,
                 stdout=open(sys.argv[2], "w"), stderr=subprocess.STDOUT)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="a card runs only in a sandbox, and the node places no process on Windows")
def test_a_detached_runner_the_node_places_nowhere_starts_the_released_leads_card_and_is_still_refused_every_widening(carded, tmp_path, capsys):
    """`cards run --detach`: the command ends, and the runner, left to the system's first process, is a marked agent's with no session above it."""
    node, lead, root = carded
    card(root, "junk-card", memory=False)   # a recall may lease an embedding model
    assert delegate.main(["cards", "release", "--why", "go", "--project", str(root)]) == 0, said(capsys)
    here = Path(__file__).resolve().parent
    code = DETACHED % {"paths": [str(here.parent / "src"), str(here), str(here.parent)], "state": str(node.state), "root": str(root),
                       "lead": lead.name, "model": f"{harnessing.PREFERRED_MODEL}.gguf"}
    out = tmp_path / "runner.log"
    env = node.env(None, {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": lead.session})
    subprocess.run([sys.executable, "-c", STARTS, code, str(out)], env=env, cwd=root, check=True, timeout=60)
    deadline = time.monotonic() + 120
    while not (found := [line for line in out.read_text().splitlines() if line.startswith("RESULT ")]) and time.monotonic() < deadline:
        time.sleep(0.2)
    heard = out.read_text()
    assert found, heard
    result = json.loads(found[0][len("RESULT "):])
    assert result["placed"] is False, "the control: the node finds no session above the runner"
    assert result["step"][0] == "launched", heard
    assert result["made"] == [[lead.name, str(root), True, "junk-card"]], "sandboxed, in the project itself, as the released lead's"
    assert result["widened"] == 2 and "finds no session above it" in heard, "naming a project is still refused to the same process"


def test_a_detached_runner_starts_in_its_project_so_the_folder_it_was_started_from_may_go(carded, tmp_path, monkeypatch, capsys):
    _node, _lead, root = carded
    asked: list[tuple[list[str], Path]] = []

    def detach(_module: str, argv: list[str], *, log: Path, **_more) -> jobs.Detached:
        asked.append((argv, Path.cwd()))
        return jobs.Detached(pid=4242, log=log, command=tuple(argv), started="now")

    monkeypatch.setattr(jobs, "detach", detach)
    monkeypatch.chdir(tmp_path)
    assert delegate.main(["cards", "run", "--detach", "--project", str(root)]) == 0, said(capsys)
    [(argv, folder)] = asked
    assert argv[:2] == ["cards", "run"] and folder == root and Path.cwd() == tmp_path.resolve()


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

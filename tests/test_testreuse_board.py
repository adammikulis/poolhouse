"""The test runner against a real node: agent identity on leases, board threads and inbox notices, subscriptions,
board scoping and runner entries as task evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from local_kit import build
from node_kit import node_binary, workspace_node  # noqa: F401  (fixtures)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import testreuse_store as storage  # noqa: E402

from poolhouse.activity import reuse  # noqa: E402
from poolhouse.board import session as sessions  # noqa: E402
from poolhouse.board.client import Denied, Quota  # noqa: E402
from poolhouse.board.tasks import Tasks  # noqa: E402
from poolhouse.workspace import slots, testboard, testruns  # noqa: E402

FILE = "tests/test_a.py"
KEY = "ab12cd34ef56" + "0" * 52


@pytest.fixture
def team(workspace_node, monkeypatch):  # noqa: F811
    kit = build(workspace_node, monkeypatch)
    kit.sessions = {name: workspace_node.session(workspace_node.member(name)) for name in ("alice", "bob", "carol")}
    return kit


def inbox(s):
    """What reached the session's inbox since it last looked."""
    page = s.read(s.cursor("inbox"), inbox=True)
    s.keep("inbox", page.cursor)
    return page.entries


def test_a_subscriber_to_a_runs_thread_is_told_exactly_once_when_it_lands(team):
    owner, watcher = testboard.BoardEvents(team.sessions["alice"]), team.sessions["bob"]
    thread = owner.claimed(FILE, KEY)
    assert thread > 0
    assert testboard.follow_thread(watcher, thread)
    owner.finished(FILE, KEY, "pass", "entry0123456789abcdef")
    [message] = inbox(watcher)
    assert "test-result" in message.text and "outcome=pass" in message.text
    assert "entry=entry0123456789abcdef" in message.text and message.sender == team.sessions["alice"].name
    assert inbox(watcher) == []


def test_waiters_are_told_when_the_run_they_waited_on_failed(team):
    owner, watcher = testboard.BoardEvents(team.sessions["alice"]), testboard.BoardEvents(team.sessions["bob"])
    thread = owner.claimed(FILE, KEY)
    watcher.waiting(FILE, {"thread": thread, "agent": {"id": "alice"}})
    owner.finished(FILE, KEY, "fail", "entryfail")
    [message] = inbox(team.sessions["bob"])
    assert "outcome=fail" in message.text and "run it yourself" in message.text


def test_a_job_ending_reaches_each_follower_and_task_watcher_once_and_not_its_own_submitter(team):
    lead = Tasks(team.session(team.lead))
    task = lead.create({"title": "Inspect", "description": "Look.", "acceptance": ["ok"],
                        "source_key": "repo:demo/sim:issue:1"})
    Tasks(team.sessions["bob"]).subscribe(task["id"])
    for name in ("alice", "bob", "carol"):
        inbox(team.sessions[name])
    events = testboard.BoardEvents(team.sessions["alice"], lambda: lead.watchers(task["id"]))
    thread = events.job_started("j1", {"argv": ["all", FILE]})
    testboard.follow_thread(team.sessions["carol"], thread)
    testboard.follow_thread(team.sessions["bob"], thread)
    events.job_done("j1", {"argv": ["all", FILE]}, {"exit": 0, "state": "done", "summary": {"ran": 1, "reused": 2}}, thread)
    [message] = inbox(team.sessions["bob"])
    assert "outcome=pass" in message.text and "ran=1 reused=2" in message.text
    assert "result=scripts/test result j1" in message.text
    [carols] = inbox(team.sessions["carol"])
    assert "test-job job=j1" in carols.text
    assert inbox(team.sessions["alice"]) == [] and inbox(team.sessions["bob"]) == []


def test_a_task_watcher_who_does_not_follow_the_thread_is_told_once(team):
    lead = Tasks(team.session(team.lead))
    task = lead.create({"title": "Inspect", "description": "Look.", "acceptance": ["ok"],
                        "source_key": "repo:demo/sim:issue:2"})
    Tasks(team.sessions["bob"]).subscribe(task["id"])
    inbox(team.sessions["bob"])
    events = testboard.BoardEvents(team.sessions["alice"], lambda: lead.watchers(task["id"]))
    thread = events.job_started("j3", {"argv": ["fast"]})
    events.job_done("j3", {"argv": ["fast"]}, {"exit": 1, "state": "done"}, thread)
    [message] = inbox(team.sessions["bob"])
    assert "outcome=fail" in message.text and "test-job job=j3" in message.text
    assert inbox(team.sessions["bob"]) == []


def test_running_a_job_creates_no_identity_and_no_token_file(team):
    alice = team.sessions["alice"]

    def state():
        return (sorted(a.name for a in alice.agents(retired=True)),
                sorted(p.name for p in (team.node.state / "client" / team.node.board).glob("*.token")))
    before = state()
    events = testboard.BoardEvents(alice)
    thread = events.job_started("j2", {"argv": ["fast"]})
    key = events.claimed(FILE, KEY)
    events.finished(FILE, KEY, "pass", "entry1")
    events.canary_mismatch(FILE, "detail")
    events.job_done("j2", {"argv": ["fast"]}, {"exit": 1, "state": "done"}, thread)
    assert key > 0 and state() == before


def test_the_runner_records_the_session_that_ran_a_file_on_its_entry(team):
    alice = team.sessions["alice"]
    agent = testboard.BoardEvents(alice).agent
    assert agent == {"id": alice.name, "parent": "", "source": "workspace-session"}
    assert testboard.BoardEvents(team.sessions["bob"]).agent["id"] == team.sessions["bob"].name


def test_a_canary_mismatch_posts_one_announcement_line(team):
    alice = team.sessions["alice"]
    testboard.BoardEvents(alice).canary_mismatch(FILE, "cached pass from e1 but a fresh run failed")
    rows = alice.read_all(channel="#announcements")
    assert len(rows) == 1 and rows[0].fields["type"] == "blocked" and "canary mismatch" in rows[0].text
    assert "\n" not in rows[0].text and len(rows[0].text) <= 200


def test_another_boards_agent_cannot_follow_a_runs_thread(team):
    alice = team.sessions["alice"]
    thread = testboard.BoardEvents(alice).claimed(FILE, KEY)
    made = sessions.register(team.node.client, "other-board", sessions.Native("claude-sonnet-5-5", "claude-code", "zed"))
    zed = sessions.Session(team.node.client, "other-board", made.token, made.name)
    with pytest.raises(Exception, match="no thread"):
        testboard.follow_thread(zed, thread)
    testboard.BoardEvents(zed).claimed(FILE, KEY)
    assert [e.sender for e in alice.read_all(thread=str(thread))] == [alice.name]


def test_a_poisoned_thread_number_changes_neither_the_hit_nor_the_run(team, tmp_path):
    events = testboard.BoardEvents(team.sessions["bob"])
    events.waiting(FILE, {"thread": 10 ** 6, "agent": {"id": "alice"}})
    events.waiting(FILE, {"thread": "not a number"})
    store = storage.Store(tmp_path / "store")
    assert store.claim(KEY, {"agent": {"id": "x"}}) is None
    store.note_thread(KEY, 10 ** 6)
    assert store.claimed(KEY)["thread"] == 10 ** 6 and store.claimed(KEY)["pid"] > 0


def test_a_lease_names_its_agent_and_the_board_checks_the_identity(team, tmp_path, monkeypatch):
    folder = tmp_path / "slots"
    folder.mkdir(exist_ok=True)
    monkeypatch.setenv(slots.SLOTS_ENV, str(folder))
    alice = team.sessions["alice"]
    for number, agent in enumerate(({"id": alice.name, "label": "reuse", "job": "j1"}, {"id": "mallory"})):
        (folder / f"{number}.slot").write_text(json.dumps({
            "label": "tests\x1b[31m", "pid": os.getpid(), "want": 2, "minimum": 1, "granted": number, "agent": agent}))
    (folder / "2.slot").write_text(json.dumps({"label": "free text", "pid": os.getpid(), "want": 1, "granted": 1}))
    known = {a.name for a in alice.agents()}
    seen = slots.testslots(lambda name: name in known)
    named = {item["agent"]["id"]: item for item in seen["running"] + seen["waiting"] if item["agent"]}
    assert named[alice.name]["agent"]["registered"] is True and named[alice.name]["label"].startswith(f"{alice.name} (tests")
    assert named["mallory"]["agent"]["registered"] is False
    assert "\x1b" not in named[alice.name]["label"]
    assert [i["agent"] for i in seen["running"] if i["label"] == "free text"] == [None]


def put_entry(base: Path, scope: str, **over) -> str:
    store = storage.Store(base / scope)
    fields = {"lookup": KEY, "file": FILE, "outcome": "pass", "manifest": {"files": {}, "dirs": {}, "dists": [],
              "env": {}, "tree": ""}, "manifest_digest": "d" * 64, "command": ["pytest", FILE],
              "junit_sha256": "e" * 64, "counts": {"tests": 3, "failed": 0, "skipped": 0}, "tree": "t" * 40, "commit": "c" * 40, "clean": True,
              "runner": {"pid": 1, "started": 1.0}, "agent": {"id": "alice"}, **over}
    return store.put(fields)


def test_a_runner_entry_is_verified_for_its_own_project_only(tmp_path):
    base = tmp_path / "reuse"
    repo = tmp_path / "repo"
    repo.mkdir()
    for step in (["init", "-q"], ["-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-q",
                                  "--allow-empty", "-m", "x"]):
        subprocess.run(["git", "-C", str(repo), *step], check=True, capture_output=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    tree = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], capture_output=True,
                          text=True).stdout.strip()
    entry = put_entry(base, "scope-a", commit=head, tree=tree)
    facts = testruns.verified(entry, "scope-a", base, head, repo)
    assert (facts["file"], facts["outcome"], facts["agent"], facts["junit_sha256"]) == (FILE, "pass", "alice", "e" * 64)
    with pytest.raises(ValueError, match="verifies"):
        testruns.verified(entry, "scope-b", base, head, repo)
    path = base / "scope-a" / "entries" / f"{entry}.json"
    forged = json.loads(path.read_text())
    forged["counts"]["tests"] = 300
    path.write_text(json.dumps(forged))
    with pytest.raises(ValueError, match="verifies"):
        testruns.verified(entry, "scope-a", base, head, repo)
    assert reuse.find("../x", "scope-a", base) is None


@pytest.fixture
def notice_clock(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(testboard, 'time', SimpleNamespace(monotonic=lambda: clock[0]), raising=False)
    return clock


def test_optional_claim_notices_back_off_after_typed_quota_and_recover(notice_clock, capsys):
    calls = []
    refused = [True]

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        if refused[0]:
            raise Quota('quota', 'private-token-must-not-be-printed')
        return {'seq': len(calls)}

    session = SimpleNamespace(name='fixture-agent', post=post)
    events = testboard.BoardEvents(session)
    for index in range(151):
        assert events.claimed(FILE, str(index)) == 0
    assert len(calls) == 1 and len(events.threads) == 151
    warning = capsys.readouterr().err
    assert warning.count('test: board notice (claim)') == 1
    assert 'private-token' not in warning and '60' in warning
    notice_clock[0] += 59
    assert events.claimed(FILE, 'before-expiry') == 0 and len(calls) == 1
    refused[0] = False
    notice_clock[0] += 1
    assert events.claimed(FILE, 'recovered') == 2
    assert events.claimed(FILE, 'next') == 3
    assert events.threads['recovered'] == 2 and capsys.readouterr().err == ''


def test_claim_cooldown_is_local_and_does_not_suppress_results_or_lifecycle(notice_clock, capsys):
    calls = []

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        raise Quota('quota', 'quota refused')

    session = SimpleNamespace(name='fixture-agent', post=post)
    events = testboard.BoardEvents(session)
    events.threads['earlier'] = 7
    assert events.claimed(FILE, KEY) == 0
    assert testboard.BoardEvents(session).claimed(FILE, KEY) == 0
    events.finished(FILE, 'earlier', 'fail', 'local-receipt')
    events.job_started('job', {'argv': ['all']})
    events.job_done('job', {'argv': ['all']}, {'exit': 1}, 8)
    events.canary_mismatch(FILE, 'local failure')
    assert len(calls) == 6
    warning = capsys.readouterr().err
    for operation in ('finish', 'job start', 'job done', 'canary'):
        assert f'board notice ({operation})' in warning


def test_nonquota_claim_faults_are_reported_each_time_without_cooldown(notice_clock, capsys):
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        raise Denied('denied', 'the session is not authorized')

    events = testboard.BoardEvents(SimpleNamespace(name='fixture-agent', post=post))
    assert events.claimed(FILE, KEY) == events.claimed(FILE, 'other') == 0
    assert len(calls) == 2
    assert capsys.readouterr().err.count('the session is not authorized') == 2

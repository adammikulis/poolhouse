"""The test runner against a real node: agent identity on leases, the two notices a run writes, board scoping and
runner entries as task evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from local_kit import build
from node_kit import node_binary, workspace_node  # noqa: F401  (fixtures)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import testreuse_store as storage  # noqa: E402

from poolhouse.activity import reuse  # noqa: E402
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


def written(s):
    """The entries the node holds on this session's board, by kind."""
    return s.status()["entries"]


def test_a_run_of_many_files_writes_no_board_row(team):
    alice, events = team.sessions["alice"], testboard.BoardEvents(team.sessions["alice"])
    before = written(alice)
    for number in range(30):
        file, key = f"tests/test_{number}.py", f"{number:012x}" + "0" * 52
        events.claimed(file, key)
        events.waiting(file, {"agent": {"id": "bob"}})
        events.wait_failed(file, {"agent": {"id": "bob"}})
        events.finished(file, key, "pass" if number % 2 else "fail", f"entry{number}")
    events.job_done("j1", {"argv": ["all", FILE]}, {"exit": 1, "state": "done", "summary": {"ran": 30, "reused": 0}})
    assert written(alice) == before


def test_a_job_ending_tells_a_task_watcher_once_and_not_its_own_submitter(team):
    lead = Tasks(team.session(team.lead))
    task = lead.create({"title": "Inspect", "description": "Look.", "acceptance": ["ok"],
                        "source_key": "repo:demo/sim:issue:7"})
    Tasks(team.sessions["bob"]).subscribe(task["id"])
    for name in ("alice", "bob"):
        inbox(team.sessions[name])
    events = testboard.BoardEvents(team.sessions["alice"], lambda: lead.watchers(task["id"]))
    before = written(team.sessions["alice"])
    events.job_done("j7", {"argv": ["fast"]}, {"exit": 0, "state": "done", "summary": {"ran": 1, "reused": 2}})
    [message] = inbox(team.sessions["bob"])
    assert "test-job job=j7" in message.text and "outcome=pass" in message.text and "ran=1 reused=2" in message.text
    assert inbox(team.sessions["bob"]) == [] and inbox(team.sessions["alice"]) == []
    assert written(team.sessions["alice"])["message"] == before.get("message", 0) + 1


def test_a_job_ending_with_no_watcher_but_its_submitter_writes_no_row(team):
    alice = team.sessions["alice"]
    before = written(alice)
    testboard.BoardEvents(alice).job_done("j8", {"argv": ["fast"]}, {"exit": 0, "state": "done"})
    testboard.BoardEvents(alice, lambda: [alice.name]).job_done("j9", {"argv": ["fast"]}, {"exit": 0, "state": "done"})
    assert written(alice) == before


def test_running_a_job_creates_no_identity_and_no_token_file(team):
    alice = team.sessions["alice"]

    def state():
        return (sorted(a.name for a in alice.agents(retired=True)),
                sorted(p.name for p in (team.node.state / "client" / team.node.board).glob("*.token")))
    before = state()
    events = testboard.BoardEvents(alice)
    events.finished(FILE, KEY, "pass", "entry1")
    events.canary_mismatch(FILE, "detail")
    events.job_done("j2", {"argv": ["fast"]}, {"exit": 1, "state": "done"})
    assert state() == before


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

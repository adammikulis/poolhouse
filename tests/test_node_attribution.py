"""A change under the real node directory is excused when the live node made it for another agent's session, and fails when this run caused it."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import node_attribution
import psutil
import pytest
from conftest import changed_files, file_mtimes
from node_attribution import OFFSET_MS, NodeWatch, anchor
from state_attribution import Watch

OTHER = [(4242, "poolhouse-node run --state elsewhere")]
NAMES, TOKENS, LEASES = "node/boards/demo/names.json", "node/tokens.json", "node/leases.json"
ALL = [NAMES, TOKENS, LEASES]
STAND_IN = """
import fcntl, json, os, sys, time
from pathlib import Path
import psutil
node, outbox = Path(sys.argv[1]), Path(sys.argv[2])
lock = open(node / "node.lock", "a")
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
(node / "node-run.json").write_text(json.dumps({"pid": os.getpid(), "started_at": psutil.Process().create_time()}))
while True:
    for job in sorted(outbox.glob("*.json")):
        todo = json.loads(job.read_text())
        target = node / todo["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        part.write_text(todo["text"])
        part.replace(target)
        job.unlink()
    time.sleep(0.02)
"""


def ident(lineage, digest="d1", seen=1):
    """A session as the node keeps it in ``names.json``."""
    return {"digest": digest, "family": "claude", "parent": "", "model": "", "model_state": "unknown", "harness": "claude-code",
            "retired": False, "retired_reason": "", "seen_ms": seen, "lineage": lineage}


def token(name, lineage, seq=1):
    """A token as the node keeps it in ``tokens.json``."""
    return {"board": "demo", "name": name, "seq": seq, "lineage": lineage}


def lease(name, pid=0, start=None, remote=False):
    """A lease as the node keeps it in ``leases.json``."""
    return {"id": f"l-{name}", "holder": f"demo/{name}", "board": "demo", "name": name, "class": "interactive", "state": "held",
            "resources": [{"type": "claim", "kind": "branch", "name": "demo/dev"}], "pid": pid, "pid_start": start, "remote": remote,
            "local": not remote, "seq": 1, "ttl_s": 900}


class Directory:
    """A state root whose ``node`` directory a stand-in node owns: its own process holds ``node.lock`` and writes what it is asked to."""

    def __init__(self, root: Path) -> None:
        self.root, self.dir, self.outbox = root, root / "node", root / "outbox"
        self.dir.mkdir()
        self.outbox.mkdir()
        self.pids: list[int] = []
        self.children: list[subprocess.Popen] = []
        self.agent: dict = {}
        self.jobs = 0

    def put(self, name: str, value: object) -> None:
        """Write ``name`` (below the state root) as test setup."""
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def start(self, *, orphan: bool) -> int:
        """Start the stand-in node outside this run (``orphan``) or as a child of the test, and wait until it holds the lock."""
        argv = [sys.executable, "-c", STAND_IN, str(self.dir), str(self.outbox)]
        if orphan:
            launcher = f"import subprocess, sys; subprocess.Popen({argv!r}, start_new_session=True, stdout=subprocess.DEVNULL)"
            subprocess.run([sys.executable, "-c", launcher], check=True)
        else:
            self.children.append(subprocess.Popen(argv, stdout=subprocess.DEVNULL))
        for _ in range(200):
            try:
                pid = json.loads((self.dir / "node-run.json").read_text())["pid"]
                break
            except (OSError, ValueError):
                time.sleep(0.05)
        else:
            raise AssertionError("the stand-in node did not come up")
        self.pids.append(pid)
        return pid

    def ask(self, name: str, value: object) -> None:
        """Have the stand-in node write ``name`` and wait until it has."""
        self.jobs += 1
        job = self.outbox / f"{self.jobs:04d}.json"
        job.with_suffix(".tmp").write_text(json.dumps({"path": name.removeprefix("node/"), "text": json.dumps(value)}))
        job.with_suffix(".tmp").replace(job)
        for _ in range(200):
            if not job.exists():
                return
            time.sleep(0.02)
        raise AssertionError("the stand-in node did not write")


@pytest.fixture
def state(tmp_path):
    held = Directory(tmp_path)
    yield held
    for pid in held.pids:
        try:
            psutil.Process(pid).send_signal(signal.SIGKILL)
        except psutil.Error:
            continue
    for child in held.children:
        child.wait()


@pytest.fixture
def served(state):
    """The state root with a stand-in node outside this run and one session of another agent, known before the run."""
    pid = state.start(orphan=True)
    state.agent = anchor(pid)
    state.put(NAMES, {"agent-aaaaaa": ident([state.agent])})
    state.put(TOKENS, {"h1": token("agent-aaaaaa", [state.agent])})
    state.put(LEASES, {"leases": [lease("agent-aaaaaa")], "next_seq": 2})
    return state


def foreign_changes(state: Directory) -> None:
    """What the node writes while another agent's session works: its seen time, its token's counter, a lease it takes."""
    state.ask(NAMES, {"agent-aaaaaa": ident([state.agent], seen=9)})
    state.ask(TOKENS, {"h1": token("agent-aaaaaa", [state.agent], seq=7)})
    state.ask(LEASES, {"leases": [lease("agent-aaaaaa"), lease("agent-aaaaaa") | {"id": "l-new"}], "next_seq": 3})


def test_changes_the_live_node_made_for_a_session_of_another_agent_are_excused(served):
    watch = NodeWatch(served.root)
    foreign_changes(served)
    assert watch.judge(ALL) == (set(ALL), {})


def test_a_session_another_agent_registers_from_a_process_older_than_the_run_is_excused(served):
    watch = NodeWatch(served.root)
    served.ask(NAMES, {"agent-aaaaaa": ident([served.agent]), "agent-bbbbbb": ident([served.agent], digest="d2")})
    served.ask(TOKENS, {"h1": token("agent-aaaaaa", [served.agent]), "h2": token("agent-bbbbbb", [served.agent])})
    assert watch.judge([NAMES, TOKENS]) == ({NAMES, TOKENS}, {})


def test_the_same_changes_are_refused_when_the_node_that_holds_the_directory_is_part_of_the_run(state):
    state.start(orphan=False)
    watch = NodeWatch(state.root)
    foreign_changes(state)
    excused, refused = watch.judge(ALL)
    assert excused == set() and set(refused) == set(ALL)
    assert all("inside this test run" in why for why in refused.values())


@pytest.mark.parametrize("which", [NAMES, TOKENS, LEASES])
def test_a_session_registered_from_a_process_of_this_run_is_refused(served, which):
    me = anchor(os.getpid())
    watch = NodeWatch(served.root)
    served.ask(NAMES, {"agent-aaaaaa": ident([served.agent]), "claude-cccccc": ident([me], digest="d3")})
    served.ask(TOKENS, {"h1": token("agent-aaaaaa", [served.agent]), "h3": token("claude-cccccc", [me])})
    served.ask(LEASES, {"leases": [lease("agent-aaaaaa"), lease("claude-cccccc")], "next_seq": 3})
    excused, refused = watch.judge([which])
    assert excused == set() and list(refused) == [which]
    assert "registered from this test run" in refused[which]


def test_a_lease_held_by_a_process_of_this_run_is_refused_whoever_the_session_is(served):
    watch = NodeWatch(served.root)
    me = anchor(os.getpid())
    served.ask(LEASES, {"leases": [lease("agent-aaaaaa", pid=me["pid"], start=me["start"])], "next_seq": 3})
    assert watch.judge([LEASES])[1].keys() == {LEASES}


def test_one_entry_from_the_run_refuses_a_file_the_node_also_wrote_for_others(served):
    watch = NodeWatch(served.root)
    me = anchor(os.getpid())
    served.ask(NAMES, {"agent-aaaaaa": ident([served.agent], seen=9), "claude-cccccc": ident([me], digest="d3")})
    assert watch.judge([NAMES])[1].keys() == {NAMES}


def test_a_session_whose_origin_cannot_be_placed_is_neither_excused_nor_refused(served):
    watch = NodeWatch(served.root)
    later = {"pid": 2, "start": int(time.time() * 1000) + 60_000 - OFFSET_MS}
    served.ask(NAMES, {"agent-aaaaaa": ident([served.agent]), "agent-dddddd": ident([later], digest="d4")})
    served.ask(TOKENS, {"h1": token("agent-aaaaaa", [served.agent]), "h4": token("agent-dddddd", [])})
    assert watch.judge([NAMES, TOKENS]) == (set(), {})


@pytest.mark.parametrize("name", ["node/device.key", "node/device.key.wrapped", "node/device.key.enc", "node/device.custody.json", "node/device.cert", "node/enroll_secret.json", "node/persons.json",
                                  "node/person/custody.json", "node/grants/book.json", "node/boards/demo/signing.key"])
def test_keys_secrets_grants_and_person_files_are_never_excused(served, name):
    watch = NodeWatch(served.root)
    served.ask(name, {"changed": True})
    excused, refused = watch.judge([name])
    assert excused == set() and list(refused) == [name]


def test_nothing_is_excused_when_no_node_holds_the_directory(served):
    watch = NodeWatch(served.root)
    foreign_changes(served)
    psutil.Process(served.pids[0]).send_signal(signal.SIGKILL)
    for _ in range(100):
        if not psutil.pid_exists(served.pids[0]):
            break
        time.sleep(0.05)
    assert watch.judge(ALL) == (set(), {})


def test_a_node_run_file_naming_another_process_excuses_nothing(served):
    watch = NodeWatch(served.root)
    foreign_changes(served)
    served.put("node/node-run.json", {"pid": os.getppid(), "started_at": psutil.Process(os.getppid()).create_time()})
    assert watch.judge(ALL) == (set(), {})


def test_a_node_run_file_with_another_start_time_excuses_nothing(served):
    watch = NodeWatch(served.root)
    foreign_changes(served)
    served.put("node/node-run.json", {"pid": served.pids[0], "started_at": time.time() - 3600})
    assert watch.judge(ALL) == (set(), {})


def test_a_table_too_large_to_read_places_nothing(served, monkeypatch):
    monkeypatch.setattr(node_attribution, "LARGEST", 600)
    watch = NodeWatch(served.root)
    assert watch.before.names["demo"] is not None
    (served.root / NAMES).write_text(json.dumps({"agent-aaaaaa": ident([served.agent], seen=9)}) + " " * 700)
    assert watch.judge([NAMES]) == (set(), {})


def test_paths_outside_the_node_directory_are_left_alone(served):
    assert NodeWatch(served.root).judge(["workspace/land-runner.log", "keystore/rate.json"]) == (set(), {})


def test_the_watch_leaves_out_what_the_node_wrote_for_others_and_fails_what_the_run_caused_with_other_writers_live(served):
    seen = Watch(served.root, lambda r: file_mtimes(r, attribute_external=False), changed_files, lambda: OTHER,
                 attribute=NodeWatch(served.root).judge)
    foreign_changes(served)
    failed, possible, _ = seen.verdict()
    assert failed == [] and possible == []
    me = anchor(os.getpid())
    served.ask(NAMES, {"agent-aaaaaa": ident([served.agent]), "claude-cccccc": ident([me], digest="d3")})
    failed, possible, _ = seen.verdict()
    assert failed == ["node/boards/demo/names.json"] and possible == []
    assert "registered from this test run" in seen.settle()

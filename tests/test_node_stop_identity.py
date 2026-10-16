"""`node_launch.stop_node` signals only the node and the supervisor of the state directory it is asked to stop.

The pids come from `node-run.json`, a file in a directory the caller names. Every case here plants that file; processes the
tests start stand in for the victims, and a recorder replaces `os.kill` where the victim would be the machine itself.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from poolhouse import node_launch, node_supervise
from poolhouse.lock import only_one

SLEEPER = "import signal, time\nif {ignore}: signal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('ready', flush=True)\ntime.sleep(120)\n"


@pytest.fixture
def spawned():
    """Processes a test started, all ended afterwards."""
    made: list[subprocess.Popen] = []
    yield made
    for child in made:
        if child.poll() is None:
            child.kill()
        child.wait()


def ready(child: subprocess.Popen) -> subprocess.Popen:
    assert child.stdout.readline().strip() == "ready"
    return child


def unrelated(spawned, *, ignore_term: bool) -> subprocess.Popen:
    """A live process of the same user that is neither a node nor a supervisor."""
    child = subprocess.Popen([sys.executable, "-c", SLEEPER.format(ignore=ignore_term)], stdout=subprocess.PIPE, text=True)
    spawned.append(child)
    return ready(child)


def node_process(spawned, tmp_path: Path, shown_state: Path, *, ignore_term: bool = False) -> subprocess.Popen:
    """A process whose command line is ``poolhouse-node run --state <shown_state>``."""
    script = tmp_path / "launched" / "run"
    script.parent.mkdir(exist_ok=True)
    script.write_text(SLEEPER.format(ignore=ignore_term), encoding="utf-8")
    child = subprocess.Popen(["poolhouse-node", "run", "--state", str(shown_state)], executable=sys.executable, cwd=script.parent,
                             stdout=subprocess.PIPE, text=True)
    spawned.append(child)
    return ready(child)


def supervisor_process(spawned, shown_state: Path) -> subprocess.Popen:
    """A process whose command line holds ``-m poolhouse.node_launch supervise --state <shown_state>``."""
    child = subprocess.Popen([sys.executable, "-c", SLEEPER.format(ignore=False), "-m", "poolhouse.node_launch", "supervise",
                              "--state", str(shown_state)], stdout=subprocess.PIPE, text=True)
    spawned.append(child)
    return ready(child)


def plant(state: Path, **run) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / node_supervise.RUN).write_text(json.dumps({"started_at": time.time(), **run}), encoding="utf-8")


@pytest.fixture
def state(tmp_path):
    return tmp_path / "node"


@pytest.fixture
def supervised(state):
    """The state directory's supervisor lock held, so a stop waits out its whole time and goes on to the forced kill."""
    state.mkdir(parents=True, exist_ok=True)
    with only_one(state / node_supervise.LOCK, wait=False, note="a test supervisor"):
        yield


@pytest.fixture
def signals(monkeypatch):
    """Every signal sent to a process or a group is recorded and not sent."""
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)) if sig else None)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: sent.append((-pgid, sig)))
    return sent


def test_a_planted_record_does_not_end_an_unrelated_process(spawned, state, supervised):
    stubborn, plain = unrelated(spawned, ignore_term=True), unrelated(spawned, ignore_term=False)
    plant(state, pid=stubborn.pid, supervisor=plain.pid)
    node_launch.stop_node(state, wait_s=0.3)
    time.sleep(0.2)
    assert stubborn.poll() is None, "the planted node pid was killed"
    assert plain.poll() is None, "the planted supervisor pid was terminated"


PLANTED = {"caller": os.getpid, "caller's parent": os.getppid, "caller's group": lambda: -os.getpid(), "init": lambda: 1,
           "zero": lambda: 0, "minus one": lambda: -1, "true": lambda: True, "float": lambda: 2.0, "text": lambda: "1",
           "none": lambda: None, "list": lambda: [1], "huge": lambda: 2 ** 70}


@pytest.mark.parametrize("which", PLANTED)
@pytest.mark.parametrize("field", ["pid", "supervisor"])
def test_a_planted_record_never_names_the_caller_init_or_a_group(signals, state, supervised, field, which):
    plant(state, **{field: PLANTED[which]()})
    node_launch.stop_node(state, wait_s=0.2)
    assert signals == []


def test_a_planted_record_does_not_end_a_node_of_another_state_directory(spawned, tmp_path, state, supervised):
    other = tmp_path / "production" / "node"
    plant(state, pid=node_process(spawned, tmp_path, other).pid, supervisor=supervisor_process(spawned, other).pid)
    node_launch.stop_node(state, wait_s=0.3)
    time.sleep(0.2)
    assert [child.poll() for child in spawned] == [None, None]


def test_a_record_of_a_much_earlier_start_does_not_end_a_node_of_the_same_directory(spawned, tmp_path, state, supervised):
    child = node_process(spawned, tmp_path, state, ignore_term=True)
    plant(state, pid=child.pid, started_at=time.time() - 3600)
    node_launch.stop_node(state, wait_s=0.3)
    time.sleep(0.2)
    assert child.poll() is None


def test_the_node_and_the_supervisor_of_the_directory_are_stopped(spawned, tmp_path, state):
    node, supervisor = node_process(spawned, tmp_path, state), supervisor_process(spawned, state)
    plant(state, pid=node.pid, supervisor=supervisor.pid)
    assert node_launch.stop_node(state, wait_s=1)
    assert node.wait(timeout=5) == -signal.SIGTERM
    assert supervisor.wait(timeout=5) == -signal.SIGTERM


def test_a_node_that_ignores_the_stop_is_killed(spawned, tmp_path, state, supervised):
    node = node_process(spawned, tmp_path, state, ignore_term=True)
    plant(state, pid=node.pid)
    node_launch.stop_node(state, wait_s=0.3)
    assert node.wait(timeout=5) == -signal.SIGKILL


def test_the_node_is_found_through_a_link_to_its_directory(spawned, tmp_path, state):
    state.mkdir()
    link = tmp_path / "link"
    link.symlink_to(state)
    node = node_process(spawned, tmp_path, link)
    plant(state, pid=node.pid)
    assert node_launch.stop_node(state, wait_s=1)
    assert node.wait(timeout=5) == -signal.SIGTERM

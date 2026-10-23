"""Replacing the daemon on a port with real daemon processes on a scratch state root: a restart ends with a daemon answering.

The daemons here are the real ones (`python -m poolhouse.fleet.launch`) on a scratch home and root; nothing is faked. A restart that
cannot start its successor must leave the previous daemon answering again, and one that works must end only once the new daemon answers,
twice in a row, the second against the daemon the first started.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from poolhouse import jobs, spawned
from poolhouse.fleet import daemon_control, handover
from poolhouse.fleet.launch import already_running

pytestmark = [pytest.mark.slow, pytest.mark.timeout(600)]

REPO = Path(__file__).resolve().parent.parent
UP_S = 240.0
"""How long a daemon on a loaded test machine is given to answer, and a restart to finish."""


class World:
    """A scratch state root and port, with the daemons started in them."""

    def __init__(self, base: Path) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.base, self.root = base, base / "root"
        self.env = {**os.environ, "POOLHOUSE_HOME": str(base / "home"), "HOME": str(base / "home"), "PYTHONPATH": str(REPO / "src"),
                    "PYTHONUNBUFFERED": "1", "POOLHOUSE_RUNTIME_ENSURE": "off"}
        self.logs = 0

    def launch(self, *extra: str) -> tuple[subprocess.Popen, Path]:
        self.logs += 1
        log = self.base / f"launch-{self.logs}.log"
        with log.open("ab") as out:
            child = subprocess.Popen([sys.executable, "-m", "poolhouse.fleet.launch", "--no-browser", "--port", str(self.port),
                                      "--root", str(self.root), *extra], env=self.env, stdout=out, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
        return child, log

    def answering(self) -> dict | None:
        return already_running(self.port)

    def up(self, child: subprocess.Popen) -> dict:
        deadline = time.monotonic() + UP_S
        while time.monotonic() < deadline:
            if (said := self.answering()) is not None:
                return said
            assert child.poll() is None, f"the daemon ended with status {child.returncode} before it answered"
            time.sleep(0.3)
        raise AssertionError("the daemon never answered")

    def end(self) -> None:
        """Stop every process that was started on this root, the way a person quits: through the daemon's own control first."""
        running = self.answering()
        if running is not None and running.get("launcher_control"):
            with contextlib.suppress(daemon_control.ControlError, OSError):
                daemon_control.request_replacement(self.root, self.port, running, "end")
        deadline = time.monotonic() + 60
        while self.answering() is not None and time.monotonic() < deadline:
            time.sleep(0.3)
        for one in psutil.process_iter(["cmdline"]):
            if str(self.base) in " ".join(one.info.get("cmdline") or []):
                one.terminate()


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    (tmp_path / "home").mkdir()
    yield made
    made.end()


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_a_restart_whose_successor_cannot_start_leaves_the_previous_daemon_answering(world):
    first, _ = world.launch()
    before = world.up(first)

    restart, log = world.launch("--restart", "--mode", "bogus")
    code = restart.wait(timeout=UP_S)

    after = world.answering()
    assert after is not None, "NOTHING answers on the port after a restart whose successor could not start:\n" + _text(log)[-1500:]
    assert after["launcher_control"] != before["launcher_control"], "the previous daemon was started again, it is not the one that was told to stop"
    assert "the previous daemon answers again" in _text(log)
    assert code == 1, "a restart that did not happen says so in its status"


def test_a_daemon_records_how_it_was_started_so_a_failed_replacement_can_start_it_again(world):
    first, _ = world.launch("--restart")
    world.up(first)
    command = daemon_control.launch_of(world.root, world.port)
    assert command is not None and command[0] == sys.executable and "--port" in command
    assert "--restart" not in command, "a restart is not replayed as one"


def test_a_restart_ends_once_the_new_daemon_answers_and_a_second_restart_replaces_that_one(world):
    first, _ = world.launch()
    generations = [world.up(first)["launcher_control"]]

    for _ in range(2):
        restart, log = world.launch("--restart")
        assert restart.wait(timeout=UP_S) == 0, "the restart did not end with the new daemon answering:\n" + _text(log)[-1500:]
        said = world.answering()
        assert said is not None and said["launcher_control"] not in generations, _text(log)[-1500:]
        generations.append(said["launcher_control"])
        record = daemon_control._record(world.root, world.port)
        assert record["instance"] == said["launcher_control"], "the answering daemon owns the launcher control record"


def test_two_restarts_started_together_are_both_carried_out_one_after_the_other(world):
    first, _ = world.launch()
    generations = [world.up(first)["launcher_control"]]
    one, log_one = world.launch("--restart")
    other, log_other = world.launch("--restart")
    assert one.wait(timeout=UP_S) == 0 and other.wait(timeout=UP_S) == 0, _text(log_one)[-800:] + _text(log_other)[-800:]
    said = world.answering()
    assert said is not None and said["launcher_control"] not in generations
    answers = _text(log_one).count("the replacement daemon answers") + _text(log_other).count("the replacement daemon answers")
    assert answers == 2, "each restart replaced the daemon and proved its successor, the second after the first:\n" + _text(log_one) + _text(log_other)


def test_a_second_launcher_waits_for_the_claim_of_the_first(tmp_path):
    order = []
    held = threading.Event()
    release = threading.Event()

    def first():
        with handover.claim(tmp_path / "root", 8770):
            held.set()
            release.wait(timeout=10)
            order.append("first done")

    def second():
        held.wait(timeout=10)
        with handover.claim(tmp_path / "root", 8770):
            order.append("second")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for one in threads:
        one.start()
    held.wait(timeout=10)
    time.sleep(0.5)
    assert order == [], "the second launcher went ahead while the first still held the claim"
    release.set()
    for one in threads:
        one.join(timeout=10)
    assert order == ["first done", "second"]


@pytest.mark.skipif(os.name == "nt", reason="a shell kills itself with a signal")
@pytest.mark.parametrize("disclaimed", [False, True])
def test_a_process_that_was_killed_is_reported_as_killed_not_as_ended(tmp_path, disclaimed):
    with (tmp_path / "out").open("ab") as out:
        started = jobs.start(["/bin/sh", "-c", "kill -KILL $$"], out=out, disclaimed=disclaimed)
    child = spawned.child_of(started)
    deadline = time.monotonic() + 120
    while child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    code = child.poll()
    assert code is not None and spawned.exit_words(code) == "was killed by SIGKILL"

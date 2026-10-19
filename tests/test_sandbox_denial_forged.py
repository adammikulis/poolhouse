"""A cut refusal report is charged only to the process its head names; text any local process can have the kernel log is not a process."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from poolhouse import sentinel
from poolhouse.sandbox import denial_log, process_tree, run
from poolhouse.sandbox.backend import CUT_REFUSAL_LOG, CUT_UNTRACED_LOG, UNREPORTED_FAILURE_LOG
from poolhouse.sandbox.denial_log import DenialLog
from poolhouse.sandbox.seatbelt import Seatbelt
from poolhouse.sentinel.adapters import sandbox_listener
from poolhouse.sentinel.score import UNREPORTED_FAILURE_WEIGHT, WATCH_AT, WEIGHTS
from tests.sandbox_kit import (
    QuietBackend,
    accented_file,
    beside,
    cut_report,
    forge_cut_report,
    long_file,
    policy,
)

pytest_plugins = ["tests.sandbox_kit"]

FORGED_TAIL = "(%d) deny(" + "z" * 900
"""What a local process puts in the message of its own refusal to have a report read as another process's."""


@pytest.mark.parametrize("message", [
    "Sandbox: sandbox-exec(777) deny(1) process-exec* /usr/bin/true\n" + FORGED_TAIL % 4242,
    "3 duplicate reports for Sandbox: sandbox-exec(777) deny(1) process-exec* /usr/bin/true\n" + FORGED_TAIL % 4242,
    "Sandbox: sandbox-exec(777) deny(1) process-exec* /usr/bin/true (4242) deny(1) x\n" + "z" * 900,
], ids=["message", "message-of-repeats", "target"])
def test_a_pid_in_the_target_or_the_message_of_a_report_is_not_the_process_that_was_refused(message):
    assert denial_log.reported_pids(message) == (777,)


@pytest.mark.parametrize("name", ["n" * 32, "é" * 16, "a(1) deny(2) b", "(9) deny(9) (9) deny(9)"],
                         ids=["longest", "two-byte-characters", "name-holding-a-deny", "name-holding-two"])
def test_the_pid_after_a_process_name_of_the_longest_length_is_found(name):
    assert len(name.encode()) <= denial_log.NAME_BYTES
    head = f"Sandbox: {name}(4321) deny(1) file-read-data /aaaa"
    assert 4321 in denial_log.reported_pids(head) and 4321 in denial_log.reported_pids("12 duplicate reports for " + head)


def test_a_report_names_a_process_only_by_bytes_the_kernel_can_have_given_its_name():
    name = "n" * (denial_log.NAME_BYTES + 1)
    assert 4321 not in denial_log.reported_pids(f"Sandbox: {name}(4321) deny(1) file-read-data /aaaa")


class Spy(Seatbelt):
    """The real Seatbelt backend, noting the process each run started as."""

    def __init__(self) -> None:
        self.pids: list[int] = []

    def started(self, tag: str, pid: int) -> None:
        self.pids.append(pid)
        super().started(tag, pid)


def forged_beside(tmp_path: Path, script: str):
    """The result of a run that is going while a process that no run follows has the kernel log a report whose message names that run."""
    spy = Spy()

    def forge() -> None:
        deadline = time.monotonic() + 5
        while not spy.pids and time.monotonic() < deadline:
            time.sleep(0.01)
        forge_cut_report(FORGED_TAIL % spy.pids[0])

    return beside(tmp_path, forge, script=script, via=spy)[1]


def test_a_report_forged_to_name_a_run_that_started_one_process_leaves_that_run_readable(tmp_path, seatbelt_alone):
    sealed = forged_beside(tmp_path, "exec /bin/sleep 2")
    assert not sealed.denials_unavailable and sealed.denials == []


def test_a_report_forged_to_name_a_run_that_forked_makes_it_unreadable_as_any_report_of_a_process_nobody_follows_does(tmp_path, seatbelt_alone):
    forked = forged_beside(tmp_path, "sleep 2; exit 1")
    assert forked.denials_unavailable and forked.denials_detail == CUT_UNTRACED_LOG


def named_program(tmp_path: Path, name: str) -> Path:
    """A copy of ``cat`` called ``name``."""
    program = tmp_path / "bin" / name
    program.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy("/bin/cat", program)
    subprocess.run(["/usr/bin/codesign", "--force", "-s", "-", str(program)], capture_output=True, check=True)
    return program


@pytest.mark.parametrize("name", ["n" * 100, "a(1) deny(2) b"], ids=["name-of-100-characters", "name-holding-a-deny"])
def test_a_cut_refusal_of_a_program_with_such_a_name_is_still_the_runs(tmp_path, seatbelt_alone, name):
    program = named_program(tmp_path, name)
    project = tmp_path / "project"
    project.mkdir()
    result = run([str(program), str(long_file(tmp_path, 1000))], policy(read=[project, program.parent], exec_=[str(program)]))
    assert result.returncode != 0 and result.denials_unavailable and result.denials_detail == CUT_REFUSAL_LOG


def failing_score(tmp_path: Path, name: str, script: str, backend=None) -> tuple[float, list[tuple[str, str]]]:
    """``(score, events)``: what a run of ``script`` adds to the score of the session that ran it, and the events and details it reported."""
    node = sentinel.Sentinel(tmp_path / name)
    seen: list[tuple[str, str]] = []

    def hear(event: str, fields: dict) -> None:
        seen.append((event, str(fields.get("detail", ""))))
        sandbox_listener(node)(event, fields)

    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    with node.running("s-failing"):
        run(["/bin/sh", "-c", script], policy(read=[project]) if backend is None else policy(), via=backend, on_event=hear)
    return node.score.value("session", "s-failing"), seen


@pytest.mark.parametrize(("odd", "weight"), [(False, UNREPORTED_FAILURE_WEIGHT), (True, WEIGHTS["sandbox.denied"])],
                         ids=["cut-inside-a-character", "cut-between-characters"])
def test_a_refusal_the_log_cannot_carry_with_the_output_hidden_and_a_failing_exit_adds_a_little(tmp_path, seatbelt_alone, odd, weight):
    target = accented_file(tmp_path / str(odd), odd)
    script = f"/bin/cat {shlex.quote(str(target))} 2>/dev/null; exit 1"
    score, events = failing_score(tmp_path, f"node-{odd}", script)
    assert score == pytest.approx(weight), events


def test_a_failed_run_with_no_report_in_the_log_is_the_failure_it_says_it_is():
    result = run(["/bin/sh", "-c", "exit 1"], policy(), via=QuietBackend(), on_event=lambda *_: None)
    assert result.denials == [] and not result.denials_unavailable and result.failure_unreported
    assert result.check() is result


@pytest.mark.parametrize(("script", "kwargs"), [("exit 0", {}), ("exit 0", {"diagnose": "always"}), ("exit 1", {"diagnose": "never"})],
                         ids=["succeeded", "succeeded-and-diagnosed", "not-diagnosed"])
def test_a_run_that_succeeded_or_was_not_diagnosed_adds_nothing(script, kwargs):
    events: list[str] = []
    result = run(["/bin/sh", "-c", script], policy(), via=QuietBackend(), on_event=lambda name, fields: events.append(name), **kwargs)
    assert not result.failure_unreported and not events


def test_a_failed_run_with_no_report_is_attributable_by_its_detail_and_weighs_little(tmp_path):
    events: list[tuple[str, dict]] = []
    run(["/bin/sh", "-c", "exit 1"], policy(), via=QuietBackend(), on_event=lambda name, fields: events.append((name, fields)))
    assert [fields["detail"] for name, fields in events if name == "sandbox.failure-unreported"] == [UNREPORTED_FAILURE_LOG]
    assert failing_score(tmp_path, "quiet", "exit 1", QuietBackend())[0] == pytest.approx(UNREPORTED_FAILURE_WEIGHT)
    assert 0 < UNREPORTED_FAILURE_WEIGHT < 0.2


def test_sixty_failed_runs_with_no_report_put_a_session_on_watch_and_fifty_nine_do_not(tmp_path):
    node = sentinel.Sentinel(tmp_path / "sentinel")
    listener = sandbox_listener(node)
    with node.running("s-failing"):
        for count in range(1, 61):
            run(["/bin/sh", "-c", "exit 1"], policy(), via=QuietBackend(), on_event=listener)
            assert bool(node.bus.recent(kind="score.watch")) == (count == 60)
    assert node.score.value("session", "s-failing") == pytest.approx(60 * UNREPORTED_FAILURE_WEIGHT) == pytest.approx(WATCH_AT)


def stream_with_two_runs(processes):
    """A stream with a run that forked (``forker``) and a run of one process (``other``)."""
    stream = DenialLog()
    stream.watch("forker")
    stream.watch("other")
    forker = subprocess.Popen(["/bin/sh", "-c", "sleep 0.3; exec sleep 30"], start_new_session=True)
    stream.track("forker", forker.pid)
    other = processes()
    stream.track("other", other.pid)
    time.sleep(0.8)
    return stream, forker, other


@pytest.mark.parametrize("ended", [False, True], ids=["running", "ended"])
def test_a_process_that_took_the_pid_of_a_cut_report_after_it_was_heard_is_not_the_one_it_named(seatbelt, processes, ended):
    stream, forker, other = stream_with_two_runs(processes)
    try:
        heard = time.time() - 60
        stream._wall = lambda: heard
        stream._hear(cut_report(other.pid))
        if ended:
            stream.finish("other")
        assert stream.why_unreadable("forker") == CUT_UNTRACED_LOG
    finally:
        os.killpg(forker.pid, 9)
        forker.wait()
        stream.close()


def test_a_process_followed_since_before_a_cut_report_was_heard_is_the_one_it_named(seatbelt, processes):
    stream, forker, other = stream_with_two_runs(processes)
    try:
        stream._hear(cut_report(other.pid))
        assert stream.why_unreadable("forker") == "" and stream.why_unreadable("other") == CUT_REFUSAL_LOG
        stream.finish("other")
        assert stream.why_unreadable("forker") == ""
    finally:
        os.killpg(forker.pid, 9)
        forker.wait()
        stream.close()


def test_a_tree_knows_when_each_of_its_processes_began(processes):
    before = time.time()
    proc = processes()
    after = time.time()
    tree = process_tree.follow(proc.pid)
    try:
        assert tree.began(proc.pid) is not None and before - 0.05 <= tree.began(proc.pid) <= after + 0.05
        assert tree.began(1) is None
    finally:
        process_tree.release(tree)

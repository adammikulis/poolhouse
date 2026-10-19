"""A refusal report the kernel cut off, or never delivered, is not a clean run, and it is charged to the run that made it."""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from poolhouse import sandbox, sentinel
from poolhouse.sandbox import denial_log, run
from poolhouse.sandbox.backend import (
    CUT_REFUSAL_LOG,
    CUT_UNTRACED_LOG,
    SILENT_REFUSAL_LOG,
    UNPARSED_REFUSAL_LOG,
)
from poolhouse.sandbox.denial_log import DenialLog
from poolhouse.sentinel.adapters import sandbox_listener
from poolhouse.sentinel.score import WEIGHTS
from tests.sandbox_kit import QuietBackend, RecordingBackend, policy

pytest_plugins = ["tests.sandbox_kit"]

WHOLE = 900
"""A path the kernel's report carries whole, tag line included."""


def long_file(root: Path, size: int, wide: int = 0) -> Path:
    """A file outside every policy whose path is about ``size`` bytes long; its first ``wide`` directories are named with 80 euro signs."""
    path = root / "outside"
    for _ in range(wide):
        path = path / ("\u20ac" * 80)
    while (room := size - len(str(path).encode()) - 1) > 0:
        path = path / ("z" * min(200, room))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("the-secret-contents")
    return path


def accented_file(root: Path, odd: bool) -> Path:
    """A file outside every policy whose path is a run of ``z`` up to byte 900 (901 when ``odd``) and then 50 two-byte
    characters, so the kernel's cut of a report, which falls near byte 965, lands inside one of them in one of the two cases."""
    path = root / "outside"
    start = 900 + odd
    while (gap := start - 1 - len(str(path).encode())) > 201:
        path = path / ("z" * 100)
    path = path / ("z" * (gap - 1)) / ("\u00e9" * 50)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("the-secret-contents")
    return path


def refused_run(tmp_path: Path, target: Path, *listeners):
    """``(result, events)`` of a ``cat`` of ``target`` under a policy that reads only the project; each of ``listeners`` hears the events too."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    events: list[tuple[str, dict]] = []

    def hear(name: str, fields: dict) -> None:
        events.append((name, fields))
        for listener in listeners:
            listener(name, fields)

    return run(["/bin/cat", str(target)], policy(read=[project]), on_event=hear), events


@pytest.mark.parametrize("size", [945, 960, 1000, 1020])
def test_a_refused_read_of_a_path_the_kernel_cuts_off_is_unreadable_and_not_clean(tmp_path, seatbelt_alone, size):
    result, events = refused_run(tmp_path, long_file(tmp_path, size))
    assert result.returncode != 0 and "Operation not permitted" in result.stderr
    assert result.denials_unavailable and result.denials_detail == CUT_REFUSAL_LOG
    with pytest.raises(sandbox.SandboxViolation, match="cut off"):
        result.check()
    assert [fields["detail"] for name, fields in events if name == "sandbox.denials-unavailable"] == [CUT_REFUSAL_LOG]


def test_a_refused_read_of_a_cut_path_with_fewer_characters_than_bytes_is_unreadable(tmp_path, seatbelt_alone):
    target = long_file(tmp_path, 1000, wide=2)
    assert len(str(target)) < denial_log.CUT_BYTES < len(str(target).encode())
    result, _ = refused_run(tmp_path, target)
    assert result.returncode != 0 and result.denials_unavailable and result.denials_detail == CUT_REFUSAL_LOG


def test_a_refused_read_of_a_long_path_the_kernel_keeps_whole_is_still_reported(tmp_path, seatbelt_alone):
    target = long_file(tmp_path, WHOLE)
    result, events = refused_run(tmp_path, target)
    assert [d["target"] for d in result.denials] == [str(target)] and not result.denials_unavailable
    assert [name for name, _ in events] == ["sandbox.denied"]


def cut_report(pid: int = 4242) -> str:
    """The message the kernel logs for a refusal of a path too long to report whole: the tag line is gone."""
    return f"Sandbox: cat({pid}) deny(1) file-read-data /private/tmp/{'z' * 900}{denial_log.CUT_MARK}"


@pytest.fixture
def processes():
    """Starts a process that lives until the test ends; the test gets its ``Popen``."""
    started: list[subprocess.Popen] = []

    def start() -> subprocess.Popen:
        started.append(subprocess.Popen(["/bin/sleep", "60"]))
        return started[-1]

    yield start
    for proc in started:
        proc.kill()
        proc.wait()


def gone_pid() -> int:
    """The pid of a process that has ended and been reaped."""
    proc = subprocess.Popen(["/usr/bin/true"])
    proc.wait()
    return proc.pid


def test_a_refused_read_of_a_path_the_log_cannot_carry_is_unreadable_and_not_clean(tmp_path, seatbelt_alone):
    """The kernel cuts a report at a fixed byte, and the system log drops one cut inside a character: of two paths
    whose two-byte characters start a byte apart, one is cut between characters and one inside one."""
    details = set()
    for odd in (False, True):
        result, _ = refused_run(tmp_path / str(odd), accented_file(tmp_path / str(odd), odd))
        assert result.returncode != 0 and "Operation not permitted" in result.stderr
        assert result.denials_unavailable
        with pytest.raises(sandbox.SandboxViolation):
            result.check()
        details.add(result.denials_detail)
    assert details == {CUT_REFUSAL_LOG, SILENT_REFUSAL_LOG}


def test_a_run_refused_with_nothing_in_the_log_for_it_is_unreadable_and_not_clean(tmp_path):
    result = run(["/bin/sh", "-c", "echo 'cat: x: Operation not permitted' >&2; exit 1"], policy(), via=QuietBackend())
    assert result.denials_unavailable and result.denials_detail == SILENT_REFUSAL_LOG
    with pytest.raises(sandbox.SandboxViolation, match="no refusal report"):
        result.check()


@pytest.mark.parametrize("script", ["echo 'cat: x: No such file or directory' >&2; exit 1", "echo 'Operation not permitted' >&2"],
                         ids=["another-error", "run-that-succeeded"])
def test_a_run_that_was_not_refused_stays_clean_when_the_log_has_nothing_for_it(tmp_path, script):
    result = run(["/bin/sh", "-c", script], policy(), via=QuietBackend(), diagnose="always")
    assert not result.denials_unavailable and result.denials == []


def test_a_report_of_a_run_that_cannot_be_read_makes_that_run_unreadable(seatbelt):
    stream = DenialLog()
    try:
        stream.watch("running")
        stream.watch("beside")
        stream._hear(f"Sandbox: a report in no shape of a refusal\n{denial_log.PREFIX}running")
        assert stream.denials("running") is None and stream.why_unreadable("running") == UNPARSED_REFUSAL_LOG
        assert stream.denials("beside") == [] and stream.why_unreadable("beside") == ""
    finally:
        stream.close()


def test_a_note_of_repeated_refusals_is_not_a_report_that_cannot_be_read(seatbelt):
    stream = DenialLog()
    try:
        stream.watch("running")
        stream._hear(f"1 duplicate report for Sandbox: bash(4242) deny(1) mach-lookup com.apple.diagnosticd\n{denial_log.PREFIX}running")
        assert stream.denials("running") == [] and stream.why_unreadable("running") == ""
    finally:
        stream.close()


@pytest.mark.parametrize("name", ["a(1) b", "cat(7)", "(x)"])
def test_a_process_name_that_holds_parentheses_is_read_as_the_process(name):
    message = f"Sandbox: {name}(4242) deny(1) file-read-data /x y\n{denial_log.PREFIX}tag"
    assert denial_log.refusal(message) == {"process": name, "operation": "file-read-data", "target": "/x y"}


def test_a_refusal_in_a_program_whose_name_holds_parentheses_is_reported(tmp_path, seatbelt):
    program = tmp_path / "bin" / "a(1) b"
    program.parent.mkdir()
    shutil.copy("/bin/cat", program)
    subprocess.run(["/usr/bin/codesign", "--force", "-s", "-", str(program)], capture_output=True, check=True)
    secret = tmp_path / "secret"
    secret.write_text("the-secret-contents")
    (tmp_path / "project").mkdir()
    result = run([str(program), str(secret)], policy(read=[tmp_path / "project", program.parent], exec_=[str(program)]))
    assert result.returncode != 0 and not result.denials_unavailable
    assert ("a(1) b", str(secret)) in [(d["process"], d["target"]) for d in result.denials]


def test_a_cut_refusal_makes_every_run_whose_processes_are_not_followed_unreadable(seatbelt):
    stream = DenialLog()
    try:
        stream.watch("hostile")
        stream.watch("bystander")
        stream._hear(cut_report())
        assert stream.denials("hostile") is None and stream.denials("bystander") is None
        assert stream.why_unreadable("hostile") == stream.why_unreadable("bystander") == CUT_UNTRACED_LOG
    finally:
        stream.close()


@pytest.mark.parametrize("repeat", ["", "3 duplicate reports for ", "1 duplicate report for "], ids=["report", "repeats", "one-repeat"])
def test_a_cut_refusal_is_charged_to_the_run_the_process_belongs_to_and_to_no_other(seatbelt, processes, repeat):
    stream = DenialLog()
    try:
        hostile, bystander = processes(), processes()
        for tag, proc in (("hostile", hostile), ("bystander", bystander)):
            stream.watch(tag)
            stream.track(tag, proc.pid)
        stream._hear(repeat + cut_report(hostile.pid))
        assert stream.denials("hostile") is None and stream.why_unreadable("hostile") == CUT_REFUSAL_LOG
        assert stream.denials("bystander") == [] and stream.why_unreadable("bystander") == ""
    finally:
        stream.close()


def test_a_cut_refusal_of_a_process_no_run_started_leaves_the_runs_that_are_followed_readable(seatbelt, processes):
    stream = DenialLog()
    try:
        for tag in ("one", "two"):
            stream.watch(tag)
            stream.track(tag, processes().pid)
        stream._hear(cut_report(gone_pid()))
        assert stream.denials("one") == [] and stream.denials("two") == []
    finally:
        stream.close()


def test_a_cut_refusal_of_a_process_nobody_followed_makes_a_run_that_was_not_followed_from_its_start_unreadable(seatbelt, processes):
    stream = DenialLog()
    try:
        stream.watch("late")
        stream.track("late", gone_pid())
        stream.watch("followed")
        stream.track("followed", processes().pid)
        stream._hear(cut_report(gone_pid()))
        assert stream.denials("late") is None and stream.why_unreadable("late") == CUT_UNTRACED_LOG
        assert stream.denials("followed") == []
    finally:
        stream.close()


def test_a_cut_report_that_names_no_process_makes_every_run_unreadable_even_when_its_processes_are_followed(seatbelt, processes):
    stream = DenialLog()
    try:
        stream.watch("followed")
        stream.track("followed", processes().pid)
        stream._hear("Sandbox: " + "z" * 1000)
        assert stream.denials("followed") is None and stream.why_unreadable("followed") == CUT_UNTRACED_LOG
    finally:
        stream.close()


def test_a_cut_refusal_heard_before_the_run_was_followed_is_charged_to_it_once_it_is(seatbelt, processes):
    stream = DenialLog()
    try:
        proc = processes()
        stream.watch("early")
        stream._hear(cut_report(proc.pid))
        stream.track("early", proc.pid)
        assert stream.denials("early") is None and stream.why_unreadable("early") == CUT_REFUSAL_LOG
    finally:
        stream.close()


def test_a_run_that_had_ended_stays_readable_when_a_refusal_is_cut_off_later(seatbelt):
    stream = DenialLog()
    try:
        stream.watch("earlier")
        assert stream.denials("earlier") == []
        stream.finish("earlier")
        stream.watch("later")
        stream._hear(cut_report())
        assert stream.denials("later") is None
        assert stream.denials("earlier") == [] and stream.why_unreadable("earlier") == ""
    finally:
        stream.close()


@pytest.mark.parametrize("message", [
    f"Sandbox: cat(4242) deny(1) file-read-data /x\n{denial_log.PREFIX}someone-else",
    f"Sandbox: cat(4242) deny(1) file-read-data /private/tmp/{'z' * 400}",
    f"{denial_log.PROBE}cut-0123456789abcdef{denial_log.LONG_PAD}",
], ids=["short-foreign-tag", "long-but-whole", "foreign-probe"])
def test_a_report_the_kernel_did_not_cut_leaves_the_runs_readable(message, seatbelt):
    stream = DenialLog()
    try:
        stream.watch("running")
        stream._hear(message)
        assert stream.denials("running") == [] and stream.why_unreadable("running") == ""
    finally:
        stream.close()


@pytest.mark.parametrize("message", [
    f"Sandbox: cat(4242) deny(1) file-read-data /x{'z' * 900}\n{denial_log.PREFIX}running"[:-3],
    f"Sandbox: cat(4242) deny(1) file-read-data /x\n{denial_log.PREFIX}running{denial_log.CUT_MARK}",
    f"Sandbox: cat(4242) deny(1) file-read-data /x{'z' * 960}\n{denial_log.PREFIX}run",
    "Sandbox: cat(4242) deny(1) file-read-data /x" + "\ud800" * 400,
], ids=["tag-cut-in-the-middle", "mark-after-the-tag", "tag-prefix-only", "lone-surrogates"])
def test_a_report_cut_inside_or_after_the_tag_is_a_cut_report(message, seatbelt):
    stream = DenialLog()
    try:
        stream.watch("running")
        stream._hear(message)
        assert stream.denials("running") is None and stream.why_unreadable("running") == CUT_UNTRACED_LOG
    finally:
        stream.close()


def test_a_stream_that_does_not_carry_a_long_report_is_not_ready(monkeypatch, caplog, seatbelt):
    monkeypatch.setattr(denial_log, "PREDICATE", f'eventMessage CONTAINS "{denial_log.PREFIX}"')
    monkeypatch.setattr(denial_log, "START_SECONDS", 1.0)
    stream = DenialLog()
    try:
        with caplog.at_level(logging.WARNING, logger="poolhouse.sandbox"):
            assert stream.watch("abc123") is False
        assert stream.denials("abc123") is None and "long test refusal" in caplog.text
    finally:
        stream.close()


def test_the_run_tells_its_backend_which_process_it_started_as():
    backend = RecordingBackend()
    result = run(["/bin/sh", "-c", "exit 0"], policy(), via=backend)
    assert result.ok and list(backend.started_as) == backend.watched == backend.finished
    assert all(isinstance(pid, int) and pid > 1 for pid in backend.started_as.values())


def forge_cut_report(message: str) -> None:
    """What any local process can do: have the kernel log a report whose message is ``message``."""
    profile = f'(version 1)(deny default (with message "{message}"))'
    subprocess.run([denial_log.SANDBOX_EXEC, "-p", profile, "--", denial_log.PROBE_PROGRAM],
                   stdin=subprocess.DEVNULL, capture_output=True, check=False)


def beside(tmp_path: Path, hostile, *, script: str = "sleep 2; exit 1"):
    """``(hostile's value, the result of a run that was going meanwhile)``: ``hostile`` runs half a second into it."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    found = []
    thread = threading.Thread(target=lambda: found.append(run(["/bin/sh", "-c", script], policy(read=[project]), diagnose="always")))
    thread.start()
    try:
        time.sleep(0.5)
        value = hostile()
    finally:
        thread.join()
    return value, found[0]


@pytest.mark.parametrize("message", ["A" * 1000, "B" * 205 + denial_log.CUT_MARK], ids=["long", "short-with-the-cut-mark"])
def test_a_cut_report_that_an_unrelated_process_caused_leaves_a_run_beside_it_readable(tmp_path, seatbelt_alone, message):
    _, bystander = beside(tmp_path, lambda: forge_cut_report(message))
    assert not bystander.denials_unavailable and bystander.denials == []


def test_a_refusal_cut_off_in_one_run_leaves_a_run_beside_it_readable(tmp_path, seatbelt_alone):
    hostile, bystander = beside(tmp_path, lambda: refused_run(tmp_path, long_file(tmp_path, 1000))[0])
    assert hostile.denials_unavailable and hostile.denials_detail == CUT_REFUSAL_LOG
    assert not bystander.denials_unavailable and bystander.denials == []


def test_a_refusal_repeated_in_one_process_and_cut_off_is_the_runs_and_only_the_runs(tmp_path, seatbelt_alone):
    project = tmp_path / "project"
    project.mkdir()
    code = f"for _ in range(4):\n    try:\n        open({str(long_file(tmp_path, 1000))!r})\n    except OSError:\n        pass\n"
    hostile, bystander = beside(tmp_path, lambda: run([sys.executable, "-c", code], policy(read=[project], python=True),
                                                      diagnose="always"))
    assert hostile.denials_unavailable and hostile.denials_detail == CUT_REFUSAL_LOG
    assert not bystander.denials_unavailable and bystander.denials == []


def test_a_refusal_cut_off_in_a_process_the_command_started_is_the_commands(tmp_path, seatbelt_alone):
    project = tmp_path / "project"
    project.mkdir()
    target = long_file(tmp_path, 1000)
    result = run(["/bin/sh", "-c", f"/bin/cat {shlex.quote(str(target))}; exit 1"], policy(read=[project]))
    assert result.denials_unavailable and result.denials_detail == CUT_REFUSAL_LOG


def score_of(tmp_path: Path, name: str, target: Path) -> float:
    """What a refused read of ``target`` adds to the score of the session that ran it."""
    node = sentinel.Sentinel(tmp_path / name)
    with node.running("s-probe"):
        refused_run(tmp_path, target, sandbox_listener(node))
    return node.score.value("session", "s-probe")


@pytest.mark.parametrize("size", [1000, 960])
def test_probing_with_a_path_the_kernel_cuts_off_scores_like_any_other_refusal(tmp_path, seatbelt_alone, size):
    ordinary = score_of(tmp_path, "whole", long_file(tmp_path / "whole", WHOLE))
    assert ordinary == pytest.approx(WEIGHTS["sandbox.denied"])
    assert score_of(tmp_path, "cut", long_file(tmp_path / "cut", size)) == pytest.approx(ordinary)

"""The stream that reads back what Seatbelt refused: what it keeps, what it drops and how it fails."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess

import pytest

from poolhouse import sandbox, sentinel
from poolhouse.sandbox import denial_log, policies, run
from poolhouse.sandbox.backend import NO_REFUSAL_LOG
from poolhouse.sandbox.bubblewrap import Bubblewrap
from poolhouse.sandbox.container import Container
from poolhouse.sandbox.denial_log import DenialLog, parse
from poolhouse.sentinel.adapters import sandbox_listener
from poolhouse.sentinel.score import UNATTRIBUTED_WEIGHT, WEIGHTS
from tests.sandbox_kit import (
    CutBackend,
    RecordingBackend,
    SilentBackend,
    UnloggedBackend,
    UnparsedBackend,
    UnreadableBackend,
    UntracedBackend,
)

pytest_plugins = ["tests.sandbox_kit"]

REFUSAL = "Sandbox: cat(4242) deny(1) file-read-data /private/tmp/x"


KERNEL = {"processImagePath": "/kernel", "senderImagePath": "/System/Library/Extensions/Sandbox.kext/Contents/MacOS/Sandbox"}
LOGGER = {"processImagePath": "/usr/bin/logger", "senderImagePath": "/usr/bin/logger"}


def event(message: object, sender: dict[str, str] = KERNEL) -> str:
    return json.dumps({"eventMessage": message, "messageType": "Error", **sender})


def test_an_event_gives_the_tag_the_process_the_operation_and_the_target():
    assert parse(event(f"{REFUSAL}\npoolhouse-sandbox:abc123")) == (
        "abc123", {"process": "cat", "operation": "file-read-data", "target": "/private/tmp/x"})


def test_a_target_holding_a_newline_cannot_name_the_tag_of_another_run():
    tag, entry = parse(event(f"{REFUSAL}\npoolhouse-sandbox:other\npoolhouse-sandbox:abc123"))
    assert tag == "abc123" and entry["target"] == "/private/tmp/x\npoolhouse-sandbox:other"


@pytest.mark.parametrize("line", [
    'Filtering the log data using "composedMessage CONTAINS "poolhouse-sandbox:""',
    "", "[]", "{not json", json.dumps({"messageType": "Error"}), event(["a", "b"]), event(None),
    event(REFUSAL),
    event("hello\npoolhouse-sandbox:abc123"),
    event("Sandbox: cat(1) deny(1) file-read-data /x\nnot-a-tag:abc123"),
    event(f"{REFUSAL}\npoolhouse-sandbox:abc123", LOGGER),
    event(f"{REFUSAL}\npoolhouse-sandbox:abc123", {**KERNEL, "processImagePath": "/usr/bin/logger"}),
    event(f"{REFUSAL}\npoolhouse-sandbox:abc123", {**KERNEL, "senderImagePath": "/tmp/Sandbox"}),
    event(f"{REFUSAL}\npoolhouse-sandbox:abc123", {"processImagePath": "/kernel"}),
    event(f"Sandbox: true(1) deny(1) process-exec* /usr/bin/true\npoolhouse-sandbox:{denial_log.PROBE}ready-00", LOGGER),
    event("2 duplicate reports for Sandbox: cat(4242) deny(1) file-read-metadata /var\npoolhouse-sandbox:abc123"),
])
def test_a_line_that_is_not_a_refusal_with_a_tag_is_not_read(line):
    assert parse(line) is None


def test_a_refusal_is_readable_again_after_other_runs(tmp_path, seatbelt, held):
    outside = tmp_path / "outside.txt"
    outside.write_text("the-secret-contents")
    argv, tag, backend = sandbox.wrapped(["/bin/cat", str(outside)], held())
    subprocess.run(argv, capture_output=True, check=False)
    first = backend.denials(tag, 0.0)
    run(["/bin/cat", str(outside)], held())
    assert [d["target"] for d in first] == [str(outside)] == [d["target"] for d in backend.denials(tag, 0.0)]


def test_the_start_up_probes_of_a_shell_are_not_reported_beside_its_refusal(tmp_path, seatbelt):
    project = tmp_path / "project"
    scratch = tmp_path / "scratch"
    project.mkdir()
    scratch.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("the-secret-contents")
    result = run([os.path.realpath("/bin/bash"), "-c", f"cat {outside}"],
                 policies.bash(str(project), str(scratch)), diagnose="always")
    assert [d["target"] for d in result.denials] == [str(outside)]


def test_a_stream_that_cannot_start_says_it_cannot_report(monkeypatch, caplog):
    monkeypatch.setattr(denial_log, "LOG", "/nonexistent/log")
    stream = DenialLog()
    stream.watch("abc123")
    with caplog.at_level(logging.WARNING, logger="poolhouse.sandbox"):
        assert stream.denials("abc123") is None
    assert "sandbox refusals cannot be reported" in caplog.text
    assert not stream.running() and stream.start() is False


def test_closing_the_stream_ends_its_process_and_a_later_start_begins_another(seatbelt):
    stream = DenialLog()
    try:
        assert stream.start() and stream.running()
        first = stream._proc
        stream.close()
        assert first.poll() is not None and not stream.running()
        assert stream.start() and stream._proc is not first
    finally:
        stream.close()


def test_the_probes_that_wait_for_the_stream_leave_no_tag_behind(seatbelt):
    stream = DenialLog()
    try:
        assert stream.settle() and stream.settle()
        assert not [tag for tag in stream._runs if tag.startswith(denial_log.PROBE)] and not stream._probes
    finally:
        stream.close()


def test_a_stream_that_stops_answering_says_it_cannot_report(monkeypatch, caplog, seatbelt):
    monkeypatch.setattr(denial_log, "SETTLE_SECONDS", 0.5)
    stream = DenialLog()
    try:
        stream.watch("abc123")
        os.kill(stream._proc.pid, signal.SIGSTOP)
        with caplog.at_level(logging.WARNING, logger="poolhouse.sandbox"):
            assert stream.denials("abc123") is None
        assert "did not answer" in caplog.text
    finally:
        if stream._proc is not None:
            os.kill(stream._proc.pid, signal.SIGCONT)
        stream.close()


def refused(held, tmp_path, name):
    """``(argv, tag, backend)`` of a run that reads a file outside its policy, started and finished."""
    outside = tmp_path / name
    outside.write_text("the-secret-contents")
    argv, tag, backend = sandbox.wrapped(["/bin/cat", str(outside)], held())
    subprocess.run(argv, capture_output=True, check=False)
    return outside, tag, backend


def test_a_stream_restarted_after_a_refusal_is_unavailable_and_not_clean(tmp_path, seatbelt, held):
    outside, tag, backend = refused(held, tmp_path, "outside.txt")
    assert [d["target"] for d in backend.denials(tag, 0.0)] == [str(outside)]
    os.kill(denial_log.shared()._proc.pid, signal.SIGKILL)
    assert backend.denials(tag, 0.0) is None


def test_a_run_started_after_the_stream_restarted_is_read_from_the_new_stream(tmp_path, seatbelt, held):
    refused(held, tmp_path, "first.txt")
    os.kill(denial_log.shared()._proc.pid, signal.SIGKILL)
    outside, tag, backend = refused(held, tmp_path, "second.txt")
    assert [d["target"] for d in backend.denials(tag, 0.0)] == [str(outside)]


def test_a_tag_of_a_running_command_is_kept_whatever_other_runs_are_refused(tmp_path, seatbelt, held, monkeypatch):
    monkeypatch.setattr(denial_log, "KEEP_TAGS", 2)
    outside, tag, backend = refused(held, tmp_path, "live.txt")
    for number in range(4):
        refused(held, tmp_path, f"other{number}.txt")
    assert [d["target"] for d in backend.denials(tag, 0.0)] == [str(outside)]


def test_refusals_under_tags_nobody_watches_cannot_push_out_a_running_command(tmp_path, seatbelt, held, monkeypatch):
    monkeypatch.setattr(denial_log, "KEEP_TAGS", 2)
    outside, tag, backend = refused(held, tmp_path, "live.txt")
    for number in range(4):
        profile = f'(version 1)(deny default (with message "{denial_log.PREFIX}foreign{number}"))'
        subprocess.run([denial_log.SANDBOX_EXEC, "-p", profile, "--", denial_log.PROBE_PROGRAM], capture_output=True, check=False)
    assert [d["target"] for d in backend.denials(tag, 0.0)] == [str(outside)]


def test_runs_that_have_ended_are_the_ones_dropped_over_the_cap(tmp_path, seatbelt, held, monkeypatch):
    monkeypatch.setattr(denial_log, "KEEP_TAGS", 2)
    stream = DenialLog()
    try:
        tags = []
        for number in range(5):
            outside = tmp_path / f"f{number}.txt"
            outside.write_text("x")
            argv, tag, _ = sandbox.wrapped(["/bin/cat", str(outside)], held())
            stream.watch(tag)
            subprocess.run(argv, capture_output=True, check=False)
            assert len(stream.denials(tag)) == 1
            stream.finish(tag)
            tags.append(tag)
        assert len(stream._runs) == 2 and list(stream._runs) == tags[-2:]
    finally:
        stream.close()


def test_a_run_whose_refusals_cannot_be_read_is_reported_and_is_not_clean(tmp_path):
    events: list[tuple[str, dict]] = []
    result = run(["/bin/sh", "-c", "exit 3"], policies.bash(str(tmp_path), str(tmp_path)), via=UnreadableBackend(), diagnose="always",
                 on_event=lambda name, fields: events.append((name, fields)))
    unavailable = [fields for name, fields in events if name == "sandbox.denials-unavailable"]
    assert len(unavailable) == 1 and unavailable[0]["severity"] == "warning" and unavailable[0]["backend"] == "unreadable"
    assert result.denials == [] and result.denials_unavailable
    with pytest.raises(sandbox.SandboxViolation, match="could not be read"):
        result.check()


def test_a_run_that_was_not_diagnosed_does_not_claim_its_refusals_were_unreadable(tmp_path):
    events: list[tuple[str, dict]] = []
    result = run(["/bin/sh", "-c", "exit 0"], policies.bash(str(tmp_path), str(tmp_path)), via=UnreadableBackend(),
                 on_event=lambda name, fields: events.append((name, fields)))
    assert not result.denials_unavailable and result.check() is result and not events


def test_a_backend_that_keeps_no_refusal_log_says_so_instead_of_reporting_a_clean_run():
    assert Bubblewrap().denials("tag", 0.0) is None and Container().denials("tag", 0.0) is None
    assert Bubblewrap().unreadable("tag") == Container().unreadable("tag") == NO_REFUSAL_LOG


def test_a_failed_run_on_a_backend_without_a_refusal_log_cannot_pass_check(tmp_path):
    events: list[tuple[str, dict]] = []
    result = run(["/bin/sh", "-c", "exit 3"], policies.bash(str(tmp_path), str(tmp_path)), via=UnloggedBackend(),
                 on_event=lambda name, fields: events.append((name, fields)))
    [fields] = [fields for name, fields in events if name == "sandbox.denials-unavailable"]
    assert fields["detail"] == "this backend keeps no refusal log"
    with pytest.raises(sandbox.SandboxViolation, match="this backend keeps no refusal log"):
        result.check()


def test_a_lost_refusal_log_is_reported_with_its_own_detail(tmp_path):
    events: list[tuple[str, dict]] = []
    run(["/bin/sh", "-c", "exit 3"], policies.bash(str(tmp_path), str(tmp_path)), via=UnreadableBackend(),
        on_event=lambda name, fields: events.append((name, fields)))
    [fields] = [fields for name, fields in events if name == "sandbox.denials-unavailable"]
    assert fields["detail"] == "the refusal log could not be read"


def test_a_run_that_cannot_start_ends_the_run_it_began_watching(tmp_path):
    backend = RecordingBackend()
    with pytest.raises(sandbox.SandboxUnavailable, match="could not start"):
        run(["/bin/sh", "-c", "exit 0"], policies.bash(str(tmp_path), str(tmp_path)), via=backend,
            cwd=str(tmp_path / "missing"))
    assert backend.watched == ["unreadable"] == backend.finished


@pytest.mark.parametrize(("backend", "weight"), [
    (UnloggedBackend(), 0.2), (UnreadableBackend(), 1.0), (UntracedBackend(), 0.2),
    (CutBackend(), WEIGHTS["sandbox.denied"]), (SilentBackend(), UNATTRIBUTED_WEIGHT),
    (UnparsedBackend(), WEIGHTS["sandbox.denied"]),
], ids=["no-log-by-construction", "log-lost", "cut-report-of-nobody-in-particular", "cut-report-of-the-run",
        "failed-and-no-report", "report-the-run-made-unreadable"])
def test_a_failed_run_whose_refusals_cannot_be_known_adds_to_the_score_by_the_reason(backend, weight, tmp_path):
    node = sentinel.Sentinel(tmp_path / "sentinel")
    with node.running("s-blind"):
        run(["/bin/sh", "-c", "exit 1"], policies.bash(str(tmp_path), str(tmp_path)), via=backend,
            on_event=sandbox_listener(node))
    assert node.score.value("session", "s-blind") == pytest.approx(weight)


PROBE_PROCESS_AND_TARGET = f"sandbox-exec(4242) deny(1) process-exec* {denial_log.PROBE_PROGRAM}"


def asked(kind: str = "settle") -> tuple[DenialLog, str]:
    """``(stream, token)`` of a stream with a probe of ``kind`` in flight, which no message has answered."""
    stream, token = DenialLog(), f"{denial_log.PROBE}{kind}-0123456789abcdef"
    stream._probes[token] = False
    return stream, token


@pytest.mark.parametrize(("kind", "last_line"), [
    ("ready", "{prefix}{token}"), ("settle", "{prefix}{token}"), ("cut", "{token}{pad}"),
])
def test_a_probe_is_answered_by_the_report_it_provokes(kind, last_line):
    stream, token = asked(kind)
    stream._hear(f"Sandbox: {PROBE_PROCESS_AND_TARGET}\n" + last_line.format(prefix=denial_log.PREFIX, token=token, pad=denial_log.LONG_PAD))
    assert stream._probes[token]


@pytest.mark.parametrize("message", [
    "{token}",
    "hello {token}",
    "Sandbox: {probe}\n{prefix}probe~settle-ffffffffffffffff {token}",
    "Sandbox: sandbox-exec(4242) deny(1) process-exec* /usr/bin/true {token}\n{prefix}other",
    "Sandbox: {probe}\n{prefix}{token}x",
    "Sandbox: sandbox-exec(4242) deny process-exec* /usr/bin/true\n{prefix}{token}",
    "Sandbox: sandbox-exec(4242) deny(1) process-exec* /etc/passwd\n{prefix}{token}",
    "Sandbox: cat(4242) deny(1) process-exec* /usr/bin/true\n{prefix}{token}",
    "Sandbox: {probe}\n{token}",
    "Sandbox: {probe}\n{prefix}{token}\nelse",
], ids=["token-alone", "token-in-prose", "token-in-the-target", "token-in-the-target-of-another-tag", "token-with-more", "head-that-does-not-parse",
        "other-target", "other-process", "no-prefix-without-the-padding", "token-not-on-the-last-line"])
def test_a_message_that_only_holds_the_token_of_a_probe_does_not_answer_it(message):
    stream, token = asked()
    stream._hear(message.format(token=token, probe=PROBE_PROCESS_AND_TARGET, prefix=denial_log.PREFIX))
    assert not stream._probes[token]

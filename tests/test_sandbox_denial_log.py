"""The stream that reads back what Seatbelt refused: what it keeps, what it drops and how it fails."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess

import pytest

from poolhouse import sandbox
from poolhouse.sandbox import denial_log, policies, run
from poolhouse.sandbox.denial_log import DenialLog, parse

pytest_plugins = ["tests.sandbox_kit"]

REFUSAL = "Sandbox: cat(4242) deny(1) file-read-data /private/tmp/x"


def event(message: object) -> str:
    return json.dumps({"eventMessage": message, "messageType": "Error"})


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


def test_a_stream_that_cannot_start_reports_nothing_and_says_so(monkeypatch, caplog):
    monkeypatch.setattr(denial_log, "LOG", "/nonexistent/log")
    stream = DenialLog()
    with caplog.at_level(logging.WARNING, logger="poolhouse.sandbox"):
        assert stream.denials("abc123") == []
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
        assert not [tag for tag in stream._entries if tag.startswith(denial_log.PROBE)] and not stream._probes
    finally:
        stream.close()


def test_a_stream_that_stops_answering_says_so_and_reports_nothing(monkeypatch, caplog, seatbelt):
    monkeypatch.setattr(denial_log, "SETTLE_SECONDS", 0.5)
    stream = DenialLog()
    try:
        assert stream.start()
        os.kill(stream._proc.pid, signal.SIGSTOP)
        with caplog.at_level(logging.WARNING, logger="poolhouse.sandbox"):
            assert stream.denials("abc123") == []
        assert "did not answer" in caplog.text
    finally:
        if stream._proc is not None:
            os.kill(stream._proc.pid, signal.SIGCONT)
        stream.close()

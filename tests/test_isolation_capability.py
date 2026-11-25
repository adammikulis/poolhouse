"""The capability report: guarantee by guarantee on this computer, and the sentences a launch is refused with.

The macOS half runs the real engine. The Linux and Windows halves are PREDICTED: no device of either ran them, so what they check is the pure
mapping from what the engine reports to the sentence, and the entries the design's table gives, never that a namespace holds.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from poolhouse import isolation
from poolhouse.isolation.capability import WATCHABLE, cause_of, drop_cache
from poolhouse.isolation.refusals import (
    DECISION_LINES,
    SENTENCES,
    IsolationRefused,
    refusal_line,
    sentence,
)
from poolhouse.sandbox.seatbelt import EXPOSURES
from tests.sandbox_kit import AbsentBackend

SRC = Path(__file__).resolve().parent.parent / "src"
FACTS = {"free": 12, "need": 345, "url": "http://example.test:1", "name": "model bridge", "attempt": "P07"}
COMMANDS = re.compile(r"sudo|\bapt\b|brew |`|--|poolhouse-|\bchmod\b|\bpip\b|type |\byes\b|y/n|password|approve|\bask\b", re.I)


def test_the_report_has_one_entry_per_guarantee_and_names_each_exposure_of_the_platform():
    report = isolation.capability()
    assert [each.id for each in report.guarantees] == [f"G{n}" for n in range(1, 14)]
    assert {each.state for each in report.guarantees} <= {"enforced", "watched", "unenforceable"}
    assert {each.id for each in report.guarantees if each.state == "watched"} <= set(WATCHABLE)
    assert {each.id for each in report.exposures} == {each.id for each in EXPOSURES} == {"X1", "X2"}
    assert report.to_dict()["can_start"] is report.can_start and len(report.to_dict()["guarantees"]) == 13


def test_a_watched_resource_is_named_and_the_process_count_is_the_kernels_on_macos():
    report = isolation.capability()
    resources = next(each for each in report.guarantees if each.id == "G11").resources
    assert resources["processes"] == "enforced" and resources["memory"] == "watched" and resources["disk"] == "watched"
    assert next(each for each in report.guarantees if each.id == "G10").state == "watched"
    assert {each.evidence for each in report.guarantees} == {"VERIFIED"}


def test_a_backend_that_is_not_on_this_computer_refuses_every_guarantee_with_the_sentence_that_names_the_fact():
    report = isolation.capability(via=AbsentBackend())
    expected = sentence("no_sandbox_tool")
    assert not report.can_start and report.enforcement_error == expected
    assert {each.state for each in report.guarantees} == {"unenforceable"} and {each.reason for each in report.guarantees} == {expected}
    with pytest.raises(IsolationRefused, match="built-in sandbox tool is missing"):
        isolation.require_capable(via=AbsentBackend())


@pytest.mark.parametrize(("family", "reason", "key"), [
    ("linux", "bwrap is not installed (apt install bubblewrap)", "no_bubblewrap"),
    ("linux", "the installed bubblewrap cannot stop a confined process from starting sandboxes of its own (it lacks --disable-userns)", "no_disable_userns"),
    ("linux", "bubblewrap namespace probe failed: setting up uid map: Permission denied", "userns_off"),
    ("win32", "this Windows computer cannot confine the network of a coding agent to the model", "windows_network"),
    ("darwin", "/usr/bin/sandbox-exec is missing", "no_sandbox_tool"),
])
def test_what_the_engine_says_is_mapped_to_one_sentence_per_platform(family, reason, key):
    assert cause_of(reason, family)[0] == key


def test_no_sentence_names_a_command_an_argument_that_widens_or_a_step_that_waits():
    for key, text in SENTENCES.items():
        shown = text.format(**FACTS)
        assert not COMMANDS.search(shown), (key, shown)
        assert shown.endswith("."), (key, shown)
    for kind, text in DECISION_LINES.items():
        assert not COMMANDS.search(text.format(subject="/some/folder")), kind
        assert "Refused:" in refusal_line(kind) and "/" not in refusal_line(kind)


def test_a_spec_that_asks_for_what_this_launch_cannot_give_adds_the_sentence_of_that_fact(tmp_path, monkeypatch):
    spec = isolation.Spec("claude", str(tmp_path), str(tmp_path), str(tmp_path), 9, interactive=True)
    report = isolation.capability(spec, stage_free=12 << 20, need=345 << 20)
    assert not report.can_start and sentence("interactive") in report.launch
    assert sentence("stage_too_small", free=12, need=345) in report.launch
    monkeypatch.setenv("PATH", str(tmp_path))
    assert sentence("no_git") in isolation.capability(spec).launch
    assert isolation.capability().can_start, "the report without a spec is the platform's alone"


def test_the_report_is_kept_until_the_machine_changes_or_a_proof_drops_it():
    first = isolation.capability()
    assert isolation.capability() is first
    drop_cache()
    assert isolation.capability() is not first


def test_a_report_is_made_at_once_by_a_process_with_no_terminal_and_runs_nothing_that_installs_or_asks():
    child = subprocess.run([sys.executable, "-c", "from poolhouse import isolation; print(isolation.capability().can_start)"],
                           env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60, check=False)
    assert child.returncode == 0 and child.stdout.strip() == "True", child.stderr
    began = time.monotonic()
    isolation.capability(via=AbsentBackend())
    assert time.monotonic() - began < 1


def test_nothing_in_the_library_asks_prompts_or_waits_for_anyone():
    banned = re.compile(r"getpass|\binput\(|sudo|osascript|tcgetattr|termios|\.confirm\(|\.typed\(|require_person|AllowUnsandboxed|\bsleep\(")
    found = {path.name: [n for n, line in enumerate(path.read_text().splitlines(), 1) if banned.search(line.split("#", 1)[0])]
             for path in (SRC / "poolhouse" / "isolation").glob("*.py")}
    assert {name: lines for name, lines in found.items() if lines} == {}

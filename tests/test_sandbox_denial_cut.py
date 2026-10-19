"""A refusal report the kernel cut off is not a clean run."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from poolhouse import sandbox
from poolhouse.sandbox import denial_log, run
from poolhouse.sandbox.backend import CUT_REFUSAL_LOG
from poolhouse.sandbox.denial_log import DenialLog
from tests.sandbox_kit import policy

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


def refused_run(tmp_path: Path, target: Path):
    """``(result, events)`` of a ``cat`` of ``target`` under a policy that reads only the project."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    events: list[tuple[str, dict]] = []
    result = run(["/bin/cat", str(target)], policy(read=[project]),
                 on_event=lambda name, fields: events.append((name, fields)))
    return result, events


@pytest.mark.parametrize("size", [935, 960, 1000, 1020])
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


def test_a_cut_refusal_makes_every_run_watched_meanwhile_unreadable(seatbelt):
    stream = DenialLog()
    try:
        stream.watch("hostile")
        stream.watch("bystander")
        stream._hear(cut_report())
        assert stream.denials("hostile") is None and stream.denials("bystander") is None
        assert stream.cut("hostile") and stream.cut("bystander")
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
        assert stream.denials("earlier") == [] and not stream.cut("earlier")
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
        assert stream.denials("running") == [] and not stream.cut("running")
    finally:
        stream.close()


@pytest.mark.parametrize("message", [
    f"Sandbox: cat(4242) deny(1) file-read-data /x{'z' * 900}\n{denial_log.PREFIX}running"[:-3],
    f"Sandbox: cat(4242) deny(1) file-read-data /x\n{denial_log.PREFIX}running{denial_log.CUT_MARK}",
    f"Sandbox: cat(4242) deny(1) file-read-data /x{'z' * 960}\n{denial_log.PREFIX}run",
], ids=["tag-cut-in-the-middle", "mark-after-the-tag", "tag-prefix-only"])
def test_a_report_cut_inside_or_after_the_tag_is_a_cut_report(message, seatbelt):
    stream = DenialLog()
    try:
        stream.watch("running")
        stream._hear(message)
        assert stream.denials("running") is None and stream.cut("running")
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

"""The pipes between a new process and the host are never leaked when the host cannot make them."""

from __future__ import annotations

import errno
import fcntl
import os

import pytest

from poolhouse.sandbox import SandboxUnavailable, hold


@pytest.mark.parametrize("failing_call", [1, 2], ids=["first-pipe", "second-pipe"])
def test_a_hold_that_cannot_make_its_pipes_closes_the_ones_it_made_and_says_the_sandbox_is_unavailable(monkeypatch, failing_call):
    made: list[int] = []
    real, calls = os.pipe, []

    def pipe() -> tuple[int, int]:
        calls.append(None)
        if len(calls) == failing_call:
            raise OSError(errno.EMFILE, "Too many open files")
        ends = real()
        made.extend(ends)
        return ends

    monkeypatch.setattr(os, "pipe", pipe)
    with pytest.raises(SandboxUnavailable, match="Too many open files"):
        hold.Hold(lambda pid: None)
    monkeypatch.undo()
    assert len(made) == 2 * (failing_call - 1)
    for fd in made:
        with pytest.raises(OSError) as closed:
            fcntl.fcntl(fd, fcntl.F_GETFD)
        assert closed.value.errno == errno.EBADF

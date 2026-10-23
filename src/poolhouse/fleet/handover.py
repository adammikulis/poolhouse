"""Replacing the daemon on a port so that something always answers there afterwards.

Asking a daemon to stop is the one step that cannot be taken back: it drains, checkpoints and closes its stores. So the process that
asked is not the successor; it stays behind as the one that proves the successor. It starts the new daemon as a process of its own and
waits until that process answers, and a successor that ends first or never answers is answered by starting the predecessor again from
the command its control record kept. The process ends only when a daemon answers on the port or it has said, in its log, that none
could be started. One launcher at a time does this for a port (`claim`), so a second restart waits for the first to be proved and
then looks again at what is running.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from poolhouse import jobs, runtime, spawned
from poolhouse.lock import only_one
from poolhouse.log import say, warn

from . import daemon_control

START_WAIT_S = 120.0
"""How long a started daemon is given to answer before it is reported as still starting; the wait `restart-host` offers by default."""

Probe = Callable[[], dict | None]


@contextmanager
def claim(root: Path, port: int) -> Iterator[None]:
    """Hold the right to replace or start the daemon on ``port``; a holder that is still proving its successor is waited for, aloud."""
    with only_one(daemon_control.claim_path(root, port), note="replacing the daemon", announce=say):
        yield


def command(arguments: Sequence[str]) -> list[str]:
    """The daemon started the way the selected runtime starts one: its interpreter, isolated, running the daemon module."""
    chosen = runtime.available()
    python = str(chosen.python) if chosen is not None else sys.executable
    return [python, *(["-I"] if chosen is not None or sys.flags.isolated else []), "-m", "poolhouse.fleet.daemon", *arguments]


def _answering(words: Sequence[str], probe: Probe, what: str) -> dict:
    """Start ``words`` with this process's own output as its output, and the daemon's health once it answers."""
    child = spawned.child_of(jobs.start(words, out=sys.stdout))
    return spawned.wait(child, probe, what=what, timeout_s=START_WAIT_S, watch=spawned.Watch(poll_s=0.15))


def finish(arguments: Sequence[str], previous: list[str] | None, probe: Probe, release: Callable[[], None]) -> int:
    """Start the successor with the daemon ``arguments`` and prove it; exit status 0 only when it answers.

    A successor that cannot start is replaced by the predecessor's own command (``previous``), so the port is answered either way;
    that command is a launcher, which asks for the claim, so ``release`` gives it up first.
    One that is still starting when the wait ends is left running, because stopping it would make the wait a decision to leave nothing.
    """
    say("starting the replacement daemon")
    try:
        health = _answering(command(arguments), probe, "the replacement daemon")
    except spawned.StartFailed as exc:
        warn(f"{exc}")
        if exc.kind == "timeout":
            return 1
        release()
        return _restore(previous, probe)
    say(f"the replacement daemon answers (commit {str(health.get('commit') or '?')[:7]})")
    return 0


def _restore(previous: list[str] | None, probe: Probe) -> int:
    """Start the predecessor again; always a failure, since the restart did not happen, but never silent about what answers."""
    if previous is None:
        warn("NOTHING ANSWERS on the port: the replaced daemon kept no record of how it was started, so it cannot be started again; start Poolhouse by hand")
        return 1
    warn(f"starting the previous daemon again: {' '.join(previous)}")
    try:
        _answering(previous, probe, "the previous daemon")
    except spawned.StartFailed as exc:
        warn(f"NOTHING ANSWERS on the port: {exc}")
        return 1
    warn("the previous daemon answers again; the restart did not happen")
    return 1

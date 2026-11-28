"""Continuing a delegate's pass that something outside its task broke off: its model's server gone or restarted, a crash of the harness, a
connection lost while the device slept.

The pass's changes stay in the held work and its conversation is carried into the next attempt, which starts once the model answers again; the model
is leased again when its server is gone. Only a harness that itself cannot take the conversation up starts a new one, told what the last one said.
The attempts are bounded by the run's `resumes` and share the pass's limit of turns, and each is kept in the run's record with what broke it. A
pass that cannot be continued failed and says why: nothing of an interrupted run is published.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import stat
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from poolhouse import claude, client, delegate_review, delegate_store as store
from poolhouse.errors import Denied
from poolhouse.workspace import nudge_fence

__all__ = ["ANSWER_WAIT_S", "RESUMES", "Model", "answers", "broke", "carried_on", "carry_conversation", "waited"]

RESUMES = 3
"""The attempts to continue a pass that was broken off from outside, unless the lead names another number."""
MOST_RESUMES = 20
ANSWER_WAIT_S = 900.0
"""Seconds an attempt waits for the model to answer again before the pass is given up: as long as a launch waits for a server to start."""
STALLED = "showed no progress"
"""The words of the launcher's own end of a run that went silent (`delegate_confined.follow`)."""
HARNESS_FAILED = "the harness"
"""How the words of a break the harness itself reported begin (`broke`)."""
CONTINUE = """\
Your last step was interrupted from outside the task: {why}. The files hold your changes so far. Continue from where you were and finish as you
were told to.
"""
RESTART = """\
{message}
An earlier session on this was interrupted from outside the task: {why}. The files hold its changes so far. Its last words, as data:
{said}
Check what is there and finish the work.
"""


def answers(url: str) -> bool:
    """Whether the server at ``url`` answers now."""
    return client.is_healthy(url, timeout=5.0)


def broke(error: str, status: int, ended: str, passed: str, url: str) -> str:
    """Why a run that failed was broken off from outside its task, empty when it was not: ``error`` is what the harness reported, ``status`` its
    exit, ``ended`` why the launcher ended or could not run it, ``passed`` the resource bound it passed and ``url`` its model's server. A stop, a
    limit of turns or of resources, a missing plugin and an answer cut at its length are the task's own ends; a silent run counts only while the
    server does not answer."""
    if passed:
        return ""
    if ended:
        return f"{ended}, and the model server does not answer" if STALLED in ended and not answers(url) else ""
    if error:
        return f"{HARNESS_FAILED} reported an error: {error}"
    return f"{HARNESS_FAILED} ended with status {status} and no final answer" if status else ""


class Model:
    """The model leased for a run: ``url``, ``alias`` and ``window`` of the server serving it now. `again` leases it anew."""

    def __init__(self, take: Callable[[], AbstractContextManager[tuple[str, Any, str]]], told: Callable[[Model], None]) -> None:
        self.take, self.told = take, told
        self.url, self.alias, self.window, self.found = "", "", 0, ""
        self.serving: Any = None
        self._block: AbstractContextManager[Any] | None = None

    def __enter__(self) -> Model:
        block = self.take()
        self.url, config, self.found = block.__enter__()
        self._block = block
        self.serving, self.window = config.serving, config.serving.slot_context
        self.alias = claude.alias_of(self.url, self.found)
        self.told(self)
        return self

    def __exit__(self, *exc: object) -> bool | None:
        block, self._block = self._block, None
        return None if block is None else block.__exit__(*exc)

    def again(self) -> None:
        """Give the lease up and take the model again: a server that is up is taken as it stands, else one is started, which a session card's
        delegate never does (`delegate_confined.leased`)."""
        self.__exit__(None, None, None)
        self.__enter__()


def waited(model: Model, say: Callable[[str], None]) -> str:
    """Wait until the model answers, leasing it again while it does not; empty once it does, else why it was given up."""
    deadline, pause, last = time.monotonic() + ANSWER_WAIT_S, 1.0, "its server does not answer"
    while True:
        if answers(model.url):
            return ""
        try:
            model.again()
        except (Denied, OSError, RuntimeError, ValueError) as why:
            last = str(why)
            say(f"the model cannot be taken yet: {why}")
        else:
            if answers(model.url):
                return ""
        if time.monotonic() >= deadline:
            return f"the model did not answer within {ANSWER_WAIT_S:.0f}s ({last})"
        time.sleep(min(pause, max(0.0, deadline - time.monotonic())))
        pause = min(pause * 2, 15.0)


def carry_conversation(source: Path, into: Path) -> bool:
    """Copy the harness's data folder ``source``, which its own run could write, into ``into``: plain files of one name and folders, and no link,
    so nothing outside it is read or reached. Whether anything was copied."""
    if source.is_symlink() or not source.is_dir():
        return False
    copied = False
    for folder, _dirs, files in os.walk(source):   # a link to a folder is listed and never entered
        made = into / Path(folder).relative_to(source)
        for name in files:
            held = Path(folder) / name
            found = held.lstat()
            if stat.S_ISREG(found.st_mode) and found.st_nlink == 1:
                made.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(held, made / name, follow_symlinks=False)
                copied = True
    return copied


def _not_taken_up(ran: delegate_review.Ran) -> bool:
    """Whether a continuation that ran no step shows that the harness cannot take the conversation up, so the next starts a new one: the harness
    itself failed (`broke`). An error of the model's (the harness's `provider.` errors: busy, gone) and a box that could not be made say nothing
    of the conversation, which the next attempt takes up again."""
    return ran.broke.startswith(HARNESS_FAILED) and not ran.answer.spent.subtype.startswith("provider.")


def _given_up(ran: delegate_review.Ran, why: str) -> delegate_review.Ran:
    said = f"interrupted: {ran.broke}; not continued: {why}"
    return delegate_review.Ran(dataclasses.replace(ran.answer, is_error=True, why=said), ran.calls)


def carried_on(rec: store.Delegate, ask: delegate_review.Pass, ran: delegate_review.Ran,
               attempt: Callable[[delegate_review.Pass], delegate_review.Ran], wait: Callable[[], str]) -> delegate_review.Ran:
    """What the pass ``ask``, whose first run came to ``ran``, comes to: while it was broken off from outside, ``wait`` for the model and run
    ``attempt`` on its continuation, up to the run's `resumes`."""
    if not ran.broke or not rec.resumes:
        return ran
    session, said, turns, calls = ran.answer.spent.session_id, ran.answer.text, ran.answer.spent.turns, ran.calls
    for number in range(1, min(rec.resumes, MOST_RESUMES) + 1):
        if ask.max_turns is not None and turns >= ask.max_turns:
            return _given_up(ran, f"its limit of {ask.max_turns} turn(s) is used up")
        store.set_status(rec.name, "loading", f"interrupted: {ran.broke}; attempt {number} of {rec.resumes} to continue")
        if why := wait():
            return _given_up(ran, why)
        message = (CONTINUE.format(why=ran.broke) if session else
                   RESTART.format(message=ask.message, why=ran.broke, said=nudge_fence.fenced([nudge_fence.clean(said[-8000:] or "nothing")])))
        if session and ask.token:
            message += f'Your verdict token for this review is "{ask.token}".\n'
        left = None if ask.max_turns is None else ask.max_turns - turns
        before = ran.broke
        ran = attempt(dataclasses.replace(ask, attempt=number, session=session, message=message, max_turns=left))
        turns, calls = turns + ran.answer.spent.turns, calls + ran.calls
        said = ran.answer.text or said
        if session and ran.answer.is_error and not ran.answer.spent.turns:   # no step ran: the pass is still broken off
            if _not_taken_up(ran):
                session = ""
            ran = dataclasses.replace(ran, broke=ran.broke or before)
        else:
            session = ran.answer.spent.session_id or session
        if not ran.broke:
            return delegate_review.Ran(ran.answer, calls)
    return _given_up(delegate_review.Ran(ran.answer, calls, ran.broke), f"all {rec.resumes} attempt(s) are used")

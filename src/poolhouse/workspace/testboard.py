"""The test runner's board side: the acting session, and the two notices a run writes: a canary mismatch and a job's end for the watchers of its task."""

from __future__ import annotations

import os
import pickle
import sqlite3
import struct
import subprocess
from collections.abc import Callable
from http.client import HTTPException
from typing import Any

from poolhouse import errors
from poolhouse.board import session as sessions
from poolhouse.log import warn
from poolhouse.redact.secrets import env_secrets, redact

__all__ = ["BoardEvents", "Events", "acting", "agent_process", "identity"]

CHANNEL = "#general"
BOARD_ERRORS = (errors.Error, ArithmeticError, AssertionError, AttributeError, EOFError,
                ImportError, LookupError, NotImplementedError, OSError, RuntimeError, StopIteration, TypeError,
                ValueError, sqlite3.Error, subprocess.SubprocessError, HTTPException, struct.error,
                pickle.PickleError)


def guarded(what: str, call: Callable[[], Any]) -> Any:
    """``call()``, or None after a warning: every board action goes through here, so none can fail a run."""
    try:
        return call()
    except BOARD_ERRORS as error:
        reason, _ = redact(str(error), env_secrets(os.environ))
        warn(f"test: board notice ({what}) not sent: {reason}")
        return None


AGENT_MARKERS = (sessions.AGENT_ENV, sessions.TOKEN_ENV, sessions.SUBAGENT_ENV, "CLAUDECODE", "POOLHOUSE_AGENT",
                 "POOLHOUSE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID")


def agent_process() -> bool:
    """Whether an agent harness started this process, so a person is not the one at the keyboard."""
    return any(os.environ.get(name) for name in AGENT_MARKERS)


def acting() -> sessions.Session | None:
    """The session of the identity the harness assigned this process or of the native session that started it, or None when it has neither."""
    if not agent_process():
        return None
    opened = guarded("session", sessions.connect)
    return opened if opened is not None and guarded("session", lambda: opened.name) else None


def identity(s: sessions.Session) -> dict[str, str]:
    """The session as the runner records it: its name, its parent and where the name came from."""
    parent = guarded("identity", lambda: next((a.parent for a in s.agents() if a.name == s.name), "")) or ""
    return {"id": s.name, "parent": parent, "source": "workspace-session"}


class Events:
    """What a test run raises: nothing reaches the board by default. A run's files and jobs are local state."""

    def __init__(self) -> None:
        self.agent: dict = {"id": "", "source": "none"}

    def claimed(self, file: str, key: str) -> None:
        """This run took the key for ``file``."""

    def waiting(self, file: str, owner: dict) -> None:
        """A request found its key in flight under ``owner``."""

    def wait_failed(self, file: str, owner: dict) -> None:
        """The run a request waited on did not leave a passing result."""

    def canary_mismatch(self, file: str, detail: str) -> None:
        """A re-executed hit failed."""

    def finished(self, file: str, key: str, outcome: str, entry: str) -> None:
        """This run finished the key for ``file``."""

    def job_done(self, job: str, spec: dict, status: dict) -> None:
        """A background job ended."""


class BoardEvents(Events):
    """The two things a run writes to the board: a canary mismatch, and the end of a job that other sessions follow.

    ``watchers`` lists the sessions that follow the task a job is attached to.
    """

    def __init__(self, s: sessions.Session, watchers: Callable[[], list[str]] | None = None) -> None:
        super().__init__()
        self.session, self.watchers = s, watchers
        self.agent = guarded("identity", lambda: identity(s)) or self.agent

    def canary_mismatch(self, file: str, detail: str) -> None:
        """Announce a cached pass that failed when re-executed."""
        line = f"test reuse canary mismatch: {file}: {detail}"[:190]
        guarded("canary", lambda: self.session.post("#announcements", "blocked", line))

    def job_done(self, job: str, spec: dict, status: dict) -> None:
        """Tell each watcher of the task the job was attached to, in one message; nothing when no one but the submitter follows."""
        guarded("job done", lambda: self._job_done(job, spec, status))

    def _job_done(self, job: str, spec: dict, status: dict) -> None:
        named = [name for name in dict.fromkeys((self.watchers() if self.watchers else None) or []) if name != self.session.name]
        if not named:
            return
        state = status.get("state")
        outcome = "pass" if status.get("exit") == 0 else state if state in ("cancelled", "failed") else "fail"
        summary = status.get("summary", {})
        body = (f"test-job job={job} tier={(spec.get('argv') or ['?'])[0]} outcome={outcome} "
                f"exit={status.get('exit')} ran={summary.get('ran', '?')} reused={summary.get('reused', '?')} "
                f"result=scripts/test result {job}")
        self.session.post(CHANNEL, "status", body + "".join(f" @{name}" for name in named), subject=f"test job {job} {outcome}")

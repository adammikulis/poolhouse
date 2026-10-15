"""The test runner's board side: the acting session, and the thread and inbox notices of runs and jobs."""

from __future__ import annotations

import os
import pickle
import sqlite3
import struct
import subprocess
import time
from collections.abc import Callable
from http.client import HTTPException
from pathlib import Path
from typing import Any

from poolhouse import errors
from poolhouse.board import session as sessions
from poolhouse.board.client import Quota
from poolhouse.log import warn

__all__ = ["BoardEvents", "acting", "follow_thread", "identity", "thread_for_key"]

CHANNEL = "#general"
CLAIM_NOTICE_COOLDOWN_S = 60.0
"""Optional notice retry delay, matching the node's default write-rate window."""
BOARD_ERRORS = (errors.Error, ArithmeticError, AssertionError, AttributeError, EOFError,
                ImportError, LookupError, NotImplementedError, OSError, RuntimeError, StopIteration, TypeError,
                ValueError, sqlite3.Error, subprocess.SubprocessError, HTTPException, struct.error,
                pickle.PickleError)


def guarded(what: str, call: Callable[[], Any]) -> Any:
    """``call()``, or None after a warning: every board action goes through here, so none can fail a run."""
    try:
        return call()
    except BOARD_ERRORS as error:
        warn(f"test: board notice ({what}) not sent: {error}")
        return None


def acting() -> sessions.Session | None:
    """The session of the identity the harness assigned this process, or None when it assigned none."""
    if not os.environ.get(sessions.AGENT_ENV) and not os.environ.get(sessions.TOKEN_ENV):
        return None
    opened = guarded("session", sessions.connect)
    return opened if opened is not None and guarded("session", lambda: opened.name) else None


def identity(s: sessions.Session) -> dict[str, str]:
    """The session as the runner records it: its name, its parent and where the name came from."""
    parent = guarded("identity", lambda: next((a.parent for a in s.agents() if a.name == s.name), "")) or ""
    return {"id": s.name, "parent": parent, "source": "workspace-session"}


def thread_for_key(folder: Path, prefix: str) -> int:
    """The board thread of the newest run whose reuse key starts with ``prefix``, or 0."""
    found = sorted((folder / "threads").glob(f"{prefix}*"), key=lambda p: p.stat().st_mtime) \
        if (folder / "threads").is_dir() and prefix.isalnum() else []
    try:
        return int(found[-1].read_text(encoding="utf-8")) if found else 0
    except (OSError, ValueError):
        return 0


def follow_thread(s: sessions.Session, thread: int) -> dict[str, Any]:
    """Subscribe the session to a run's board thread (one inbox message per update)."""
    return s.subscribe("thread", str(thread))


class BoardEvents:
    """Posts a run's milestones to the board and the inboxes that follow them.

    ``watchers`` lists the sessions that follow the task a job is attached to.
    """

    def __init__(self, s: sessions.Session, watchers: Callable[[], list[str]] | None = None) -> None:
        self.session, self.watchers = s, watchers
        self.threads: dict[str, int] = {}
        self._claim_after = 0.0

    @property
    def agent(self) -> dict[str, str]:
        """The session as the runner records it on an entry; empty when the board does not answer."""
        return guarded("identity", lambda: identity(self.session)) or {"id": "", "source": "none"}

    @staticmethod
    def _try(what: str, call: Callable[[], Any]) -> Any:
        return guarded(what, call)

    def _post(self, subject: str, body: str, reply_to: int = 0) -> int:
        sent = self.session.post(CHANNEL, "status", body, subject=subject, reply_id=str(reply_to) if reply_to else "")
        return int(sent["seq"])

    def claimed(self, file: str, key: str) -> int:
        """Open the key's thread on the board."""
        seq = self._try("claim", lambda: self._claim_notice(file, key)) or 0
        self.threads[key] = seq
        return seq

    def _claim_notice(self, file: str, key: str) -> int:
        if time.monotonic() < self._claim_after:
            return 0
        try:
            seq = self._post(f"test {Path(file).name} {key[:8]}",
                             f"test-run key={key[:12]} file={file} state=running agent={self.session.name}")
        except Quota:
            self._claim_after = time.monotonic() + CLAIM_NOTICE_COOLDOWN_S
            warn(f"test: board notice (claim) rate limited; optional claim notices paused for {CLAIM_NOTICE_COOLDOWN_S:g} s; local test evidence is unchanged")
            return 0
        self._claim_after = 0.0
        return seq

    def finished(self, file: str, key: str, outcome: str, entry: str) -> None:
        """Reply in the key's thread with how the run ended."""
        if self.threads.get(key):
            tail = "" if outcome == "pass" else " waiters: run it yourself"
            self._try("finish", lambda: self._post(
                f"test {Path(file).name} {outcome}",
                f"test-result key={key[:12]} file={file} outcome={outcome} entry={entry}{tail}", self.threads[key]))

    def waiting(self, file: str, owner: dict) -> None:
        """Follow the thread of the run this request waits on."""
        if owner.get("thread"):
            self._try("subscribe", lambda: follow_thread(self.session, int(owner["thread"])))

    def wait_failed(self, file: str, owner: dict) -> None:
        """Nothing beyond the thread reply, which the owner posts."""

    def canary_mismatch(self, file: str, detail: str) -> None:
        """Announce a cached pass that failed when re-executed."""
        line = f"test reuse canary mismatch: {file}: {detail}"[:190]
        self._try("canary", lambda: self.session.post("#announcements", "blocked", line))

    def job_started(self, job: str, spec: dict) -> int:
        """Open the job's thread on the board."""
        return self._try("job start", lambda: self._post(
            f"test job {job}", f"test-job job={job} tier={(spec.get('argv') or ['?'])[0]} owner={self.session.name} state=running")) or 0

    def job_done(self, job: str, spec: dict, status: dict, thread: int = 0) -> None:
        """Reply in the job's thread, naming each task watcher: a follower of the thread and a watcher get the reply once."""
        self._try("job done", lambda: self._job_done(job, spec, status, thread))

    def _job_done(self, job: str, spec: dict, status: dict, thread: int) -> None:
        state = status.get("state")
        outcome = "pass" if status.get("exit") == 0 else state if state in ("cancelled", "failed") else "fail"
        summary = status.get("summary", {})
        body = (f"test-job job={job} tier={(spec.get('argv') or ['?'])[0]} outcome={outcome} "
                f"exit={status.get('exit')} ran={summary.get('ran', '?')} reused={summary.get('reused', '?')} "
                f"result=scripts/test result {job}")
        named = [name for name in dict.fromkeys((self.watchers() if self.watchers else None) or []) if name != self.session.name]
        mention = "".join(f" @{name}" for name in named)
        self._post(f"test job {job} {outcome}", body + mention, thread)

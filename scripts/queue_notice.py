"""Board messages about the test jobs the queue doctor ends or admits: one to the job's owner and one `blocked` announcement, once per job."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable

import testjobs

from poolhouse.workspace import testboard

ANNOUNCEMENTS = "#announcements"
LINE_LIMIT = 200
NOTE_ONLY = ("quiet", "old", "estimate")


def claim(jobs: testjobs.Jobs, job: str, kind: str) -> bool:
    """Take the job's notice of ``kind``; False when it was taken already."""
    try:
        os.close(os.open(jobs.path(job) / f"notice-{kind}", os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return False
    return True


def post(jobs: testjobs.Jobs, job: str, kind: str, what: str, say: Callable[[str], None] = print) -> bool:
    """Tell the owner of ``job`` and, unless ``kind`` is in `NOTE_ONLY`, the announcements what happened (``what``, naming the cause), each once per job and kind; False when one was not delivered.

    A process with no board identity sends nothing and leaves the notice for a later pass, saying so.
    """
    spec = jobs.read(job, "spec.json")
    owner = spec.get("owner", {}).get("id", "") if isinstance(spec, dict) else ""
    if not owner or spec.get("owner", {}).get("source") != "workspace-session":
        return False
    session = testboard.acting()
    if session is None:
        say(f"queue doctor: no board identity here, so the {kind} notice for job {job} is not sent")
        return False
    line = f"test job {job} (owner {owner}) {what}"
    delivered = True
    for part, to, type_, body in (("note", owner, "note", line), ("announce", ANNOUNCEMENTS, "blocked", line[:LINE_LIMIT])):
        if (part == "announce" and kind in NOTE_ONLY) or (part == "note" and to == session.name) or not claim(jobs, job, f"{kind}-{part}"):
            continue
        if testboard.guarded(f"{kind} {part}", lambda to=to, type_=type_, body=body: session.post(to, type_, body)) is None:
            with contextlib.suppress(OSError):
                (jobs.path(job) / f"notice-{kind}-{part}").unlink()
            say(f"queue doctor: the {kind} {part} for job {job} was not delivered; it is retried on the next pass")
            delivered = False
    return delivered

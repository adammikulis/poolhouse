"""What one pool member may see of this daemon: its own jobs, files and transfers, and the files the owner shares.

A ``requester`` is the fingerprint of the member's compute certificate, or "" for the owner of this daemon, who sees everything. A member's files live in a
folder of its own under the daemon's files root; it reads the outputs of the jobs it asked for and the ``shared`` folder, and nothing else.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .files import safe_relpath
from .jobs import DaemonError

__all__ = ["JOBS", "MEMBERS", "SHARED", "file_path", "home", "owns", "slots"]

JOBS = "jobs"
MEMBERS = "members"
SHARED = "shared"
"""The folder of the files root the owner puts what every member may read; no member writes it."""
SLOT_FIELDS = ("id", "id_task", "n_ctx", "is_processing", "speculative")
"""What a model server's slot says about capacity. Every other field may hold somebody's prompt or reply."""


def owns(by: str, requester: str) -> bool:
    """Whether what ``by`` asked for (a job's or a transfer's label) is ``requester``'s to see."""
    return not requester or by == requester or by.startswith(requester + "/")


def home(files_root: Path, requester: str) -> Path:
    """The folder ``requester`` reads, writes and runs its jobs in: the whole files root for the owner."""
    if not requester:
        return files_root
    mine = files_root / MEMBERS / requester
    mine.mkdir(parents=True, exist_ok=True)
    return mine


def file_path(files_root: Path, jobs: Mapping[str, Any], relpath: str, requester: str, *, write: bool = False) -> Path:
    """The file ``relpath`` names for ``requester``; `DaemonError` for a path that is not theirs to read, or to write."""
    if not requester:
        return safe_relpath(files_root, relpath)
    first, _, rest = urllib.parse.unquote(relpath).partition("/")
    if first == JOBS:
        job_id, _, inside = rest.partition("/")
        job = jobs.get(job_id)
        if write or job is None or not owns(job.by, requester) or not inside:
            raise DaemonError("a member reads the files of its own jobs, and writes none of them")
        return safe_relpath(files_root / JOBS / job_id, urllib.parse.quote(inside))
    if first == SHARED:
        if write or not rest:
            raise DaemonError("the shared folder is read by members and written by the owner")
        return safe_relpath(files_root / SHARED, urllib.parse.quote(rest))
    return safe_relpath(home(files_root, requester), relpath)


def slots(raw: bytes) -> bytes:
    """A model server's ``/slots`` answer with only each slot's capacity numbers; `ValueError` for any other shape."""
    rows = json.loads(raw)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("not a list of slots")
    return json.dumps([{name: row[name] for name in SLOT_FIELDS if isinstance(row.get(name), (bool, int))}
                       for row in rows]).encode()

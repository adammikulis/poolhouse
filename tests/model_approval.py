"""A process with no agent marker answering the requests a model fetch or a first load raises, as a test needs one."""

from __future__ import annotations

from pathlib import Path

from poolhouse import requests, sentinel

PERSON = requests.Context(env={})
"""A process with no agent marker, which answers a request directly: no terminal, no typed yes."""


def approved(path: Path) -> Path:
    """``path`` pinned the way a person's approval pins a model file, for a test that is about serving it."""
    sentinel.default().manifest.pin(path, "model", source="approved")
    return path


def approve(kind: str) -> int:
    """Approve every pending request of ``kind`` the way a process with no agent marker does; how many."""
    done = 0
    for one in requests.list_requests(state="pending", kind=kind):
        requests.answer(one.id, "approve", one.fingerprint, "terminal", PERSON)
        done += 1
    return done

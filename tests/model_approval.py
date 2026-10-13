"""A person answering the requests a model fetch or a first load raises, as a test needs one."""

from __future__ import annotations

from pathlib import Path

from poolhouse import requests, sentinel

PERSON = requests.Context(terminal=(True, True), env={})
"""A terminal with a person at it and no agent marker in the process."""


def approved(path: Path) -> Path:
    """``path`` pinned the way a person's approval pins a model file, for a test that is about serving it."""
    sentinel.default().manifest.pin(path, "model", source="approved")
    return path


def approve(kind: str) -> int:
    """Approve every pending request of ``kind`` the way a person at a terminal does; how many."""
    done = 0
    for one in requests.list_requests(state="pending", kind=kind):
        requests.answer(one.id, "approve", one.fingerprint, "terminal", PERSON)
        done += 1
    return done

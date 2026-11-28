"""What the card runner keeps under the state root: for each project, the hold, its release and what became of each card's runs, and the last
listing, which the web page shows.

A card's file says what the work is (`poolhouse.cards`); this says where a run of it stands. Nothing here is a second backlog: only cards the
project holds are listed, and what is kept of accepted work is dropped once its card has landed.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Callable, Collection, Iterator
from pathlib import Path
from typing import Any

from poolhouse import files, home, lock

__all__ = ["VERSION", "edit", "folder", "graded", "landed", "publish", "published", "read", "watching"]

VERSION = 1


def folder() -> Path:
    path = home.state("cards")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def _file(project: Path) -> Path:
    return folder() / f"{hashlib.sha256(str(project.resolve()).encode()).hexdigest()[:16]}.json"


def read(project: Path) -> dict[str, Any]:
    """The ledger of ``project``: ``hold`` (a lead's reason, or empty), ``release`` and ``cards``, each card's entry by id."""
    found = files.read_json(_file(project), {})
    found = found if isinstance(found, dict) and found.get("version") == VERSION else {}
    cards = found.get("cards")
    return {"version": VERSION, "project": str(project.resolve()), "hold": str(found.get("hold") or ""),
            "release": found.get("release") if isinstance(found.get("release"), dict) else {},
            "cards": cards if isinstance(cards, dict) else {}}


def watching(project: Path) -> Path:
    """The lock held while the running card of ``project`` is looked at, so one caller at a time settles or stops it."""
    return _file(project).with_suffix(".watch.lock")


@contextlib.contextmanager
def _held(project: Path) -> Iterator[None]:
    with lock.held(_file(project).with_suffix(".lock")):
        yield


def edit(project: Path, change: Callable[[dict[str, Any]], Any]) -> Any:
    """What ``change`` returns after it edited the ledger of ``project`` under its lock, which is then written."""
    with _held(project):
        found = read(project)
        result = change(found)
        files.write_json(_file(project), found)
        _file(project).chmod(0o600)
        return result


def graded(project: Path, card: str, name: str, grade: dict[str, Any]) -> str:
    """What a lead's ``grade`` of delegate ``name`` does to ``card``, as a sentence; empty when the grade is of no run that waits for it. A checked
    `correct` accepts work that waits for review, which the lead then publishes; work that was sent back is accepted by no grade. A lower verdict
    rejects either, and the next run is given the reasons."""
    def change(ledger: dict[str, Any]) -> str:
        entry = ledger["cards"].get(card)
        if not entry or entry.get("delegate") != name or entry.get("state") not in ("review", "returned"):
            return ""
        if grade["verdict"] == "correct":
            if entry["state"] != "review":
                return f"card {card} was sent back, which no grade accepts: split it, correct it, or `poolhouse-delegate cards retry {card}`"
            if not grade.get("verified"):
                return f"card {card} is accepted by a grade with a check (--check)"
            entry.update(state="accepted", why="")
            return f"card {card} accepted: open or update the pull request of {entry.get('branch')} and merge it; landing deletes the card"
        entry.update(state="", why="", reasons=[*entry.get("reasons", []), f"{grade['verdict']}: {grade['critique']}"[:2000]])
        return f"card {card} rejected: it is ready again, and its next run is told why"

    return edit(project, change)


def landed(project: Path, present: Collection[str]) -> None:
    """Drop what is kept of accepted work whose card is not in ``present``: its change has landed, and a later card of that name starts fresh."""
    def change(ledger: dict[str, Any]) -> None:
        for ident in [ident for ident, entry in ledger["cards"].items() if entry.get("state") == "accepted" and ident not in present]:
            del ledger["cards"][ident]

    edit(project, change)


def publish(listing: dict[str, Any]) -> None:
    """Keep ``listing``, the cards of one project as they stand now, for the web page."""
    path = folder() / "view.json"
    files.write_json(path, files.versioned(listing, VERSION))
    path.chmod(0o600)


def published() -> dict[str, Any]:
    """The last listing `publish` kept, or ``{}`` when there is none."""
    found = files.read_json(home.state("cards", "view.json"), {})
    return found if isinstance(found, dict) and found.get("version") == VERSION else {}

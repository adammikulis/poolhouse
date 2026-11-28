"""What the card runner settles for itself once a card's delegate has ended: that its commit changes only what the card allows (`outside`), and
that the card's check passes on that commit, run by this process in a confinement of its own (`rerun`). Nothing the delegate's record says counts
for either.

The runner of the check is the base commit's: a commit that changes it, or anything else that judges a card (`cards.guarded`), is outside what
any card allows and is never run.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from poolhouse import (
    cards,
    coding_policy,
    delegate_candidate,
    delegate_confined,
    delegate_record,
    harnessing,
    isolation,
)
from poolhouse.errors import Denied

__all__ = ["outside", "rerun"]

NO_MODEL = "http://127.0.0.1:1"
"""The upstream of the confinement's model bridge: a privileged port nothing serves, so a check reaches no model."""
SAID = 300
"""The characters of a failed check's own last words that are repeated."""
FAILED = (Denied, OSError, RuntimeError, ValueError)


def _text(root: Path, commit: str, path: str) -> str:
    return delegate_candidate.git(root, "show", f"{commit}:{path}").decode("utf-8")


def _held(root: Path, base: str) -> dict[str, cards.Card]:
    """The cards the commit ``base`` holds, by id."""
    names = delegate_candidate.git(root, "ls-tree", "-z", "--name-only", base, f"{cards.FOLDER}/").split(b"\0")
    paths = [os.fsdecode(name) for name in names if name.endswith(b".toml")]
    return {Path(path).stem: cards.card_from(Path(path).stem, _text(root, base, path)) for path in paths}


def _lands(root: Path, card: cards.Card, change: tuple[str, str], commits: tuple[str, str]) -> bool:
    """Whether ``change`` (git's status letter and the path) between ``commits`` (base, work) is one of the edits that land ``card``: its own file
    deleted, its id taken out of the `after` of a card that waited for it, or, when it closes a row no other card names, that row deleted."""
    (status, path), (base, commit) = change, commits
    if path == f"{cards.FOLDER}/{card.id}.toml":
        return status == "D"
    if status != "M":
        return False
    if path.startswith(f"{cards.FOLDER}/") and path.endswith(".toml"):
        old = _held(root, base).get(Path(path).stem)
        if old is None or path != f"{cards.FOLDER}/{old.id}.toml" or card.id not in old.after:
            return False
        wanted = dataclasses.replace(old, after=tuple(each for each in old.after if each != card.id))
        return cards.card_from(old.id, _text(root, commit, path)) == wanted
    if path != cards.BACKLOG or not card.closes or any(each.row == card.row for ident, each in _held(root, base).items() if ident != card.id):
        return False
    before, after = _text(root, base, path).splitlines(), _text(root, commit, path).splitlines()
    return any(any(cell.startswith(card.row) for cell in cards.cells(line)) and before[:at] + before[at + 1:] == after
               for at, line in enumerate(before))


def outside(root: Path, card: cards.Card, base: str, commit: str) -> str:
    """Why the work ``commit``, made on ``base``, is not the card's to hand over: it is no single commit on that base, or it changes a path that is
    neither one the card writes or creates nor an edit that lands it. Empty when it is."""
    try:
        if delegate_candidate.git(root, "rev-list", "--parents", "-n", "1", commit).decode().split()[1:] != [base]:
            return "its work is not one commit on the commit it started from"
        words = delegate_candidate.git(root, "diff-tree", "-r", "-z", "--no-renames", "--name-status", base, commit).split(b"\0")
        allowed = {*card.write, *(each for each in card.creates if "::" not in each)}
        extra = []
        for status, name in zip(words[::2], words[1::2], strict=False):
            path = os.fsdecode(name)
            if path in allowed and not cards.guarded(path):
                continue
            if not _lands(root, card, (status.decode(), path), (base, commit)):
                extra.append(path)
    except FAILED as why:
        return f"what it changed could not be read: {why}"
    return f"it changed what its card does not allow: {delegate_record.cleaned(', '.join(extra), SAID)}" if extra else ""


def _refused(_name: str, _arguments: object) -> str:
    return "a card's check has no way to anyone"


def _boxed(tree: Path, check: str, name: str, log: Path) -> str:
    """Why ``check`` does not pass in a confinement over ``tree``, as a delegate's shell would run it, publishing nothing."""
    grants = isolation.decide_launch(isolation.Arguments(), project=str(tree)).grants
    shell = delegate_confined.SHELL
    plan = isolation.Plan("opencode", shell, str(tree), coding_policy.Policy(coding_policy.GUARDED), NO_MODEL,
                          isolation.Lease.of("no-model", harnessing.MIN_WINDOW, 1), "", f"{name}.check",
                          lambda _stage: lambda _event, _payload: {}, _refused,
                          lambda _context: isolation.Rendering((shell, "-c", check)), isolation.Notice(None, "", 0), grants)
    box = isolation.prepare(plan)
    try:
        delegate_confined.held(box)   # never released: nothing of the check's run is published
        with log.open("wb") as out:
            # a pipe, as a delegate's shell has: a command may ask what its output is, which the box refuses of a file outside it
            process = box.popen(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            copying = threading.Thread(target=shutil.copyfileobj, args=(process.stdout, out), daemon=True)
            copying.start()
            try:
                status = process.wait(timeout=delegate_confined.SHELL_MAX_S)
            except subprocess.TimeoutExpired:
                return f"its check did not end within {delegate_confined.SHELL_MAX_S:.0f}s on the committed tree"
            finally:
                box.end_processes()   # nothing the check left running holds the pipe, so the copy ends
                copying.join(delegate_confined.NO_PROGRESS_S)
        passed = box.passed()
    finally:
        box.close(publish=False)
    if not status and not passed:
        return ""
    lines = [line.strip() for line in log.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    said = delegate_record.cleaned(passed or " | ".join(lines[-3:])[-SAID:], SAID)
    return f"its check does not pass on the committed tree (exit {status}): {said}"


def rerun(root: Path, card: cards.Card, commit: str, name: str, log: Path) -> str:
    """Why ``card``'s check does not pass on ``commit``, the work of delegate ``name``: it is checked out in a folder of its own and the check is
    run there by this process, in a confinement, with what it prints kept in ``log``. Empty when it exits 0."""
    try:
        with tempfile.TemporaryDirectory(prefix="poolhouse-card-check-") as held:
            tree = Path(os.path.realpath(held)) / "tree"
            delegate_candidate.git(root, "worktree", "add", "--quiet", "--detach", str(tree), commit)
            try:
                return _boxed(tree, card.check, name, log)
            finally:
                with contextlib.suppress(OSError, ValueError):
                    delegate_candidate.git(root, "worktree", "remove", "--force", str(tree))
    except FAILED as why:
        return f"its check could not be rerun: {delegate_record.cleaned(why, SAID)}"

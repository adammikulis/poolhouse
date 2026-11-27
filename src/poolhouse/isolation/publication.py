"""The publication decision (docs/design-isolated-launcher.md, 4.4): which changes of a stage become edits of the person's checkout and which are refused.

The whole change the agent made is applied, so the person reads it in their diff. Refused is only what would take effect on this computer
without being read there, or leave the project: anything in `.git`, a coding tool's own settings and control paths (a tool reads hooks and
permissions from them), a credential name, a special file, a link that leads out of the project or to one of those, and a name this
filesystem cannot hold apart from another. A refused file is kept as a patch the session can read and reported with words the parent wrote.
An applied file of the kind that runs later (`guard.runs_later`) is listed beside the edits, as information.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import stat
import unicodedata
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

from poolhouse import secretpaths
from poolhouse.activity.source_snapshot import Capture, CaptureEntry
from poolhouse.coding_apply import Edit
from poolhouse.coding_workspace import Change
from poolhouse.coding_workspace_state import CONTROL_PREFIXES
from poolhouse.guard import harnesses, runs_later

__all__ = ["REASONS", "Basis", "Decision", "Refusal", "colliding", "decide", "fold", "junk", "patch_text", "token"]

REASONS = {
    "git": "a path inside `.git`, whose hooks and settings git runs on its own, which no change applies",
    "control": "a control path of a coding tool, which no change applies",
    "settings": "a coding tool's own settings, which can give it hooks or permissions, which no change applies",
    "credential": "a file named like a credential, which no change applies",
    "special": "a file the working copy cannot carry: a special file, a link out of the tree, a submodule or a hard-linked file",
    "environment": "a link to an environment folder of the project that the session removed or replaced, of which nothing is applied",
    "link": "a link that leads out of the project, or to a path no change applies",
    "path": "a path of the checkout that goes through a link, or holds something else than the session started from, so it is not written",
    "collision": "a name that folds to the same name as another on this filesystem",
    "changed": "a file of the stage that was no longer the one read, so it was not applied",
    "restore": "a file git does not hold whose previous version the launcher could not keep first, so it was not replaced or deleted",
    "withheld": "a change of a run that failed, which nothing publishes",
    "limit": "a resource bound of the session that was passed, which ended it",
}
"""One sentence the parent writes for each cause of a refusal."""
NO_CONTENT = frozenset({"git", "control", "credential", "special", "environment"})
"""Causes whose file is never read into a patch."""
NAME = re.compile(r"[A-Za-z0-9._/-]{1,80}")
LINK = b"120000"
PATCH_LIMIT = 1 << 20
"""The largest file the parent copies into a patch."""
HOPS = 40
"""The most links one path is followed through, as the kernels bound it; past it the link leads nowhere the parent can name."""
UNSEEN = re.compile("[\u200c-\u200f\u202a-\u202e\u206a-\u206f\ufeff]")
"""The code points HFS+ ignores in a name, which git ignores too when it protects `.git`."""


@dataclass(frozen=True, slots=True)
class Refusal:
    """One change that was not applied: its path, why (a key of `REASONS`) and the size of the file in the stage."""

    path: str
    klass: str
    size: int = 0


@dataclass(frozen=True, slots=True)
class Decision:
    """What one publication does: the edits to apply, the changes refused, each with its cause, and for each edit of a file of the kind that
    runs later its path and why."""

    edits: tuple[Edit, ...] = ()
    refused: tuple[Refusal, ...] = ()
    later: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Basis:
    """What a decision reads: the person's checkout, the stage as captured now and the paths git holds (the version an edit displaces of any
    other path is kept first, so the launcher itself can give it back)."""

    project: Path
    result: Capture
    tracked: frozenset[str]


def fold(path: str) -> str:
    """A path as the filesystem sees it when it ignores case and Unicode form (APFS and NTFS do)."""
    return "/".join(unicodedata.normalize("NFC", part).casefold() for part in path.split("/"))


def token(refusal: Refusal, index: int, held: Collection[str]) -> str:
    """How a refused file is named in a report: by its path when the whole path is plain characters and the master already held it (a name of the
    person's own project), otherwise by a number, its size and a digest of its name; a name the session chose is never copied."""
    if NAME.fullmatch(refusal.path) and refusal.path in held:
        return f'file "{refusal.path}"'
    return f"<file {index}, {refusal.size} bytes, digest {hashlib.sha256(refusal.path.encode(errors='surrogateescape')).hexdigest()[:8]}..>"


def colliding(paths: Collection[str]) -> set[str]:
    """The paths that share a folded name, or a folded directory, with a path spelled differently."""
    spellings: dict[str, set[str]] = {}
    for path in paths:
        parts = path.split("/")
        for count in range(1, len(parts) + 1):
            prefix = "/".join(parts[:count])
            spellings.setdefault(fold(prefix), set()).add(prefix)
    bad = {key for key, spelled in spellings.items() if len(spelled) > 1}
    return {path for path in paths if any(fold("/".join(path.split("/")[:count])) in bad for count in range(1, len(path.split("/")) + 1))}


def junk(path: str) -> bool:
    """Whether a path is build junk no one wants published or reported: a bytecode file, a macOS folder note, or anything under a cache or dependency
    folder the project already skips (`secretpaths.SKIP_DIRS`). A control or credential name, and anything in `.git`, is never junk."""
    parts = path.split("/")
    return ".git" not in parts and not _never(path) and (parts[-1] == ".DS_Store" or parts[-1].endswith((".pyc", ".pyo")) or any(part in secretpaths.SKIP_DIRS for part in parts[:-1]))


def _never(path: str) -> str:
    """The cause when no change to this path is ever applied (``git``, ``control``, ``credential`` or ``settings``), or empty. A name is
    compared as any filesystem the checkout may sit on reads it: case and Unicode form folded, and for `.git` the spellings git guards."""
    parts = [fold(part) for part in path.split("/")]
    if any(UNSEEN.sub("", part).rstrip(". ") in (".git", "git~1") for part in parts):
        return "git"
    if {fold(name) for name in CONTROL_PREFIXES} & set(parts):
        return "control"
    if secretpaths.name_hit(path):
        return "credential"
    return "settings" if {fold(name) for name in harnesses.settings()} & set(parts) else ""


def _open(root: Path, path: str, limit: int | None) -> tuple[bytes, os.stat_result] | None:
    """The first ``limit`` bytes (all of them for ``None``) and the facts of a regular file, opened without following a link."""
    try:
        descriptor = os.open(root / path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        return (stream.read() if limit is None else stream.read(limit), info) if stat.S_ISREG(info.st_mode) else None


def _target(root: Path, path: str) -> str | None:
    """What the link at ``path`` names, or ``None`` when there is no link there."""
    try:
        return str((root / path).readlink())
    except OSError:
        return None


def _follow(path: str, link: Callable[[str], tuple[str, str] | None]) -> tuple[str | None, set[str]]:
    """Where ``path`` leads inside the project, every link on the way followed as the filesystem follows it (a `..` after a link is taken from
    where the link leads, not from its name), and the links followed. ``link`` gives the spelling and target of the link at a path, or
    ``None``. The place is ``None`` when the path leaves the project or never ends."""
    held: list[str] = []
    left, followed, hops = path.split("/"), set(), 0
    while left:
        part = left.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not held:
                return None, followed
            held.pop()
            continue
        held.append(part)
        if found := link("/".join(held)):
            followed.add(found[0])
            hops += 1
            if hops > HOPS or Path(found[1]).is_absolute():
                return None, followed
            held.pop()
            left = [*found[1].split("/"), *left]
    return "/".join(held), followed


def _leading_out(basis: Basis, entries: dict[str, CaptureEntry], gone: set[str]) -> set[str]:
    """The links of the result tree that lead out of the project or to a path no change applies, and every link of the tree such a link goes
    through. A link the stage does not hold is read from the checkout, so a link of the person's own is followed too."""
    staged = {fold(path): (path, target) for path, entry in entries.items()
              if entry.mode == LINK and (target := _target(basis.result.directory, path)) is not None}
    replaced = {fold(path) for path in (*entries, *gone)}

    def link(here: str) -> tuple[str, str] | None:
        key = fold(here)
        if key in staged or key in replaced:
            return staged.get(key)
        target = _target(basis.project, here)
        return None if target is None else (here, target)

    bad: set[str] = set()
    for path, _ in staged.values():
        place, followed = _follow(path, link)
        if place is None or _never(place):
            bad |= followed
    return bad


def _edit(basis: Basis, change: Change, path: str, entry: CaptureEntry | None) -> Edit | None:
    """The edit for a change whose bytes (or link target) are read once more and must still be the ones the capture recorded, or ``None``. An
    overwrite or a deletion of a path git does not hold keeps the version it displaces first; the base digest is the one the master (or the
    last publication) holds, and the apply refuses the path unless the live file is still that version."""
    base = None if change.before is None else change.before.hex()
    first = change.before is not None and path not in basis.tracked
    was_link = change.before_mode == LINK
    if change.after is None or entry is None:
        return Edit(path, base, None, kept_first=first, was_link=was_link)
    if entry.mode == LINK:
        target = _target(basis.result.directory, path)
        if target is None or hashlib.sha256(os.fsencode(target)).digest() != entry.sha256:
            return None
        return Edit(path, base, None, link=target, kept_first=first, was_link=was_link)
    found = _open(basis.result.directory, path, None)
    if found is None or hashlib.sha256(found[0]).digest() != entry.sha256:
        return None
    return Edit(path, base, found[0], 0o755 if entry.mode == b"100755" else 0o644, kept_first=first, was_link=was_link)


def _one(basis: Basis, change: Change, entries: dict[str, CaptureEntry], out: set[str], collide: set[str]) -> Edit | Refusal:
    path = os.fsdecode(change.path)
    entry = entries.get(path)
    size = 0 if entry is None else entry.size
    if cause := _never(path):
        return Refusal(path, cause, size)
    if path in collide:
        return Refusal(path, "collision", size)
    if change.after_mode == LINK and path in out:
        return Refusal(path, "link", size)
    return _edit(basis, change, path, entry) or Refusal(path, "changed", size)


def _later(edit: Edit) -> str:
    """Why the file an edit leaves behind (or deletes) is of the kind that runs later, or empty."""
    return runs_later.why(edit.path, (edit.data or b"")[:runs_later.HEAD_BYTES], edit.mode if edit.data is not None else 0)


def decide(basis: Basis, changes: Sequence[Change], protected: Sequence[tuple[bytes, str]] = ()) -> Decision:
    """The decision for ``changes`` of the stage in ``basis``; ``protected`` are the names the capture left out (a control path, a credential, a
    special file) with their causes, which are refused by name and never read. Build junk (`junk`) is dropped without a word."""
    entries = {os.fsdecode(each.path): each for each in basis.result.entries}
    ordered = sorted((change for change in changes if not junk(os.fsdecode(change.path))), key=lambda change: change.path)
    left = [Refusal(os.fsdecode(name), why) for name, why in protected]
    gone = {os.fsdecode(change.path) for change in ordered if change.after is None}
    out = _leading_out(basis, entries, gone)
    collide = colliding(set(entries) | gone)
    decided = [_one(basis, change, entries, out, collide) for change in ordered]
    edits = tuple(each for each in decided if isinstance(each, Edit))
    later = tuple((edit.path, why) for edit in edits if (why := _later(edit)))
    return Decision(edits, (*left, *(each for each in decided if isinstance(each, Refusal))), later)


def _lines(data: bytes | None) -> list[str] | None:
    """The lines of a text file, or ``None`` when it is not text."""
    if data is None:
        return []
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return None


def patch_text(basis: Basis, refused: Sequence[Refusal]) -> bytes:
    """The patch of the refused files for the session to read: what it wrote itself, kept as data, each file as an addition. Nothing of the
    person's checkout is copied into it (a file the session named may be one the checkout holds and the stage does not), so the reader diffs it
    against the checkout itself. A link is named with its target. A file that is not text, or larger than what the parent reads, is named with
    its size and digest and not copied; the files of `NO_CONTENT` are never read."""
    out: list[str] = []
    for each in refused:
        shown = each.path.encode("unicode_escape").decode()
        if each.klass in NO_CONTENT:
            out.append(f"# {shown}: not read ({REASONS[each.klass]})\n")
            continue
        if (target := _target(basis.result.directory, each.path)) is not None:
            out.append(f"# {shown}: a link to {target.encode('unicode_escape').decode()}\n")
            continue
        new = _open(basis.result.directory, each.path, PATCH_LIMIT + 1)
        if new is None:
            out.append(f"# {shown}: deleted by the session\n")
            continue
        after = _lines(new[0])
        if after is None or len(new[0]) > PATCH_LIMIT:
            out.append(f"# {shown}: {each.size} bytes, sha256 {hashlib.sha256(new[0]).hexdigest()}, not copied (not text, or larger than the parent reads)\n")
            continue
        out.extend(difflib.unified_diff([], after, "/dev/null", f"b/{shown}"))
    return "".join(out).encode()

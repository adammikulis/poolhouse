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
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
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
    "link": "a change of a link after which a link would lead out of the project, or to a path no change applies",
    "path": "a path of the checkout that goes through a link, or holds something else than the session started from, so it is not written",
    "spelling": "a change of a link whose name or target is not plain ASCII, which the launcher does not compare as this filesystem may",
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
STEPS = 4096
"""The most steps one link is followed over every outcome; past it the link leads nowhere the parent can name."""
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
    return "/".join(secretpaths.fold(part) for part in path.split("/"))


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


def _live(root: Path, parts: Sequence[str]) -> str | None:
    """What the link at ``parts`` of the checkout names now, or ``None`` when there is none there: nothing is read through a link above it."""
    if any(root.joinpath(*parts[:count]).is_symlink() for count in range(1, len(parts))):
        return None
    return _target(root, "/".join(parts))


def _ends(root: Path, path: str, target: str, changed: Mapping[str, tuple[str, str | None]], taken: frozenset[str]) -> Iterator[tuple[str | None, frozenset[str]]]:
    """Every place the link at ``path`` to ``target`` can lead in the checkout this publication leaves, each with the changes the way there took
    as made. A path of ``changed`` (by its folded name: its spelling and the link it becomes, or ``None`` for no link) holds what it holds now
    or what the change makes it, since any change can end in a conflict; every other path holds what it holds now. A `..` after a link is taken
    from where the link leads. The place is ``None`` when it is outside the project, or the way never ends."""
    if Path(target).is_absolute():
        yield None, taken
        return
    todo = [(tuple(path.split("/")[:-1]), tuple(target.split("/")), 0, taken)]
    seen: set[tuple] = set()
    while todo:
        state = todo.pop()
        if state in seen:
            continue
        seen.add(state)
        held, left, hops, taken = state
        if len(seen) > STEPS:
            yield None, taken
            return
        if not left:
            yield "/".join(held), taken
        elif left[0] in ("", "."):
            todo.append((held, left[1:], hops, taken))
        elif left[0] == "..":
            if held:
                todo.append((held[:-1], left[1:], hops, taken))
            else:
                yield None, taken
        else:
            here = (*held, left[0])
            ways = [(_live(root, here), taken)]
            if found := changed.get(fold("/".join(here))):
                ways.append((found[1], taken | {found[0]}))
            for link, took in ways:
                if link is None:
                    todo.append((here, left[1:], hops, took))
                elif hops >= HOPS or Path(link).is_absolute():
                    yield None, took
                else:
                    todo.append((held, (*link.split("/"), *left[1:]), hops + 1, took))


def _links(root: Path) -> Iterator[tuple[str, str]]:
    """Every link of the checkout outside `.git`, with its target; no link is followed."""
    for here, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if fold(name) != ".git"]
        for name in (*dirs, *names):
            path = Path(here, name).relative_to(root).as_posix()
            if (target := _target(root, path)) is not None:
                yield path, target


def _leading_out(basis: Basis, changes: Sequence[Change]) -> dict[str, str]:
    """The paths of ``changes`` that add, change or remove a link and must not be made, each with its cause. ``spelling``: the path or the new
    target is not plain ASCII, so whether another name reaches it is the filesystem's own answer. ``link``: in some outcome of this publication
    the change would leave a link that leads out of the project or to a path no change applies, one of the new links or a link the checkout
    holds that it reroutes. A change refused here is taken as not made and everything is followed again, until no further change is refused."""
    candidates: dict[str, str | None] = {}
    for change in changes:
        path = os.fsdecode(change.path)
        if LINK in (change.before_mode, change.after_mode) or (basis.project / path).is_symlink():
            candidates[path] = _target(basis.result.directory, path) if change.after_mode == LINK else None
    if not candidates:
        return {}
    checkout = tuple(_links(basis.project))
    refused = {path: "spelling" for path, target in candidates.items() if not (path.isascii() and (target or "").isascii())}
    while True:
        changed = {fold(path): (path, target) for path, target in candidates.items() if path not in refused}
        bad: set[str] = set()
        for path, target in changed.values():
            if target is not None and any(place is None or _never(place) for place, _ in _ends(basis.project, path, target, changed, frozenset({path}))):
                bad.add(path)
        for path, target in checkout:
            for place, taken in _ends(basis.project, path, target, changed, frozenset()):
                if taken and (place is None or _never(place)):
                    bad |= taken
        if not bad:
            return refused
        refused.update(dict.fromkeys(bad, "link"))


def _edit(basis: Basis, change: Change, path: str, entry: CaptureEntry | None) -> Edit | None:
    """The edit for a change whose bytes (or link target) are read once more and must still be the ones the capture recorded, or ``None``. An
    overwrite or a deletion of a path git does not hold keeps the version it displaces first; the base digest is the one the master (or the
    last publication) holds, and the apply refuses the path unless the live file is still that version."""
    base = None if change.before is None else change.before.hex()
    was_link = change.before_mode == LINK
    ran = None if base is None or was_link else 0o755 if change.before_mode == b"100755" else 0o644
    made = partial(Edit, path, base, kept_first=base is not None and path not in basis.tracked, was_link=was_link, base_mode=ran)
    if change.after is None or entry is None:
        return made(None)
    if entry.mode == LINK:
        target = _target(basis.result.directory, path)
        if target is None or hashlib.sha256(os.fsencode(target)).digest() != entry.sha256:
            return None
        return made(None, link=target)
    found = _open(basis.result.directory, path, None)
    if found is None or hashlib.sha256(found[0]).digest() != entry.sha256:
        return None
    return made(found[0], 0o755 if entry.mode == b"100755" else 0o644)


def _one(basis: Basis, change: Change, entries: dict[str, CaptureEntry], out: dict[str, str], collide: set[str]) -> Edit | Refusal:
    path = os.fsdecode(change.path)
    entry = entries.get(path)
    size = 0 if entry is None else entry.size
    if cause := _never(path):
        return Refusal(path, cause, size)
    if path in collide:
        return Refusal(path, "collision", size)
    if cause := out.get(path):
        return Refusal(path, cause, size)
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
    collide = colliding(set(entries) | gone)
    out = _leading_out(basis, [change for change in ordered if not _never(path := os.fsdecode(change.path)) and path not in collide])
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

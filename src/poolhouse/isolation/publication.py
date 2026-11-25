"""The publication decision (docs/design-isolated-launcher.md, 4.4): which changes of a stage become edits of the person's checkout and which are refused.

Every change is classified from the bytes and the mode of the file as the stage holds it, never from what produced it, with `guard.runs_later.tree`
over the result tree: the stage as captured now, the files a deleted path had, and the files inside the person's environment folders that run by
their path alone. A change is applied only when it is plain source, or when a `publish` rule names that exact file and the file is not a governing
file (a rule never overrides a governing class, a control name, a credential name, a special file, a link or a name collision). Whatever else is
refused at once, kept as a patch the session can read, and reported with words the parent wrote.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import posixpath
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
from poolhouse.guard import runs_later
from poolhouse.isolation import publish
from poolhouse.isolation.publish import PublishRule

__all__ = ["REASONS", "Basis", "Decision", "Refusal", "colliding", "decide", "fold", "patch_text", "token"]

REASONS = {
    **runs_later.CLASSES,
    "untracked": "a file git does not hold, so what an overwrite or a deletion destroys could not be restored",
    "control": "a control path of a coding tool, which no change applies",
    "credential": "a file named like a credential, which no change applies",
    "special": "a file the working copy cannot carry: a special file, a link out of the tree, a submodule or a hard-linked file",
    "link": "a link that is not new, or does not lead to plain source of this tree",
    "path": "a path of the checkout that goes through a link, so it is not written",
    "collision": "a name that folds to the same name as another on this filesystem",
    "unread": "a project too large to read what names what, so nothing of it is applied",
}
"""One sentence the parent writes for each cause of a refusal: the classes of the classifier, and the causes that are not about a file's class."""
RULED = frozenset({"program", "data", "untracked"})
"""The only causes a `publish` rule can lift: a file that is not governing and is not a link, a control name or a credential."""
NO_CONTENT = frozenset({"control", "credential", "special", "unread"})
"""Causes whose file is never read into a patch."""
NAME = re.compile(r"[A-Za-z0-9._/-]{1,80}")
LINK = b"120000"


@dataclass(frozen=True, slots=True)
class Refusal:
    """One change that was not applied: its path, why (a key of `REASONS`) and the size of the file in the stage."""

    path: str
    klass: str
    size: int = 0


@dataclass(frozen=True, slots=True)
class Decision:
    """What one publication does: the edits to apply and the changes refused, each with its cause."""

    edits: tuple[Edit, ...] = ()
    refused: tuple[Refusal, ...] = ()


@dataclass(frozen=True, slots=True)
class Basis:
    """What a decision reads: the person's checkout, the stage as captured now, the paths git holds and the `publish` rules in force."""

    project: Path
    result: Capture
    tracked: frozenset[str]
    rules: tuple[PublishRule, ...] = ()


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


def _never(path: str) -> str:
    """``control`` or ``credential`` when no change to this path is ever applied, whatever rule names it."""
    controls = {fold(name) for name in CONTROL_PREFIXES}
    if any(fold(part) in controls for part in path.split("/")):
        return "control"
    return "credential" if secretpaths.name_hit(path) else ""


def _open(root: Path, path: str, limit: int | None) -> tuple[bytes, os.stat_result] | None:
    """The first ``limit`` bytes (all of them for ``None``) and the facts of a regular file, opened without following a link."""
    try:
        descriptor = os.open(root / path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        return (stream.read() if limit is None else stream.read(limit), info) if stat.S_ISREG(info.st_mode) else None


def _reader(basis: Basis, entries: dict[str, CaptureEntry]) -> Callable[[str], tuple[bytes, int, int] | None]:
    """The facts `runs_later.tree` asks for: a file of the stage by its captured bytes and mode, anything else (a file the stage deleted, a file of
    an environment folder) by the person's checkout."""
    live = publish.readable(basis.project)

    def read(path: str) -> tuple[bytes, int, int] | None:
        entry = entries.get(path)
        if entry is None:
            return live(path)
        found = _open(basis.result.directory, path, runs_later.READ_LIMIT)
        return None if found is None else (found[0], 0o755 if entry.mode == b"100755" else 0o644, found[1].st_nlink)

    return read


def _ruled(basis: Basis, path: str) -> bool:
    return any(rule.relpath == path and publish.matches(rule, basis.project, path) for rule in basis.rules)


def _edit(basis: Basis, change: Change, path: str, entry: CaptureEntry | None) -> Edit | None:
    """The edit for a change whose bytes are read once more and must still be the ones the capture recorded, or ``None``."""
    base = None if change.before is None else change.before.hex()
    if change.after is None or entry is None:
        return Edit(path, base, None)
    found = _open(basis.result.directory, path, None)
    if found is None or hashlib.sha256(found[0]).digest() != entry.sha256:
        return None
    return Edit(path, base, found[0], 0o755 if entry.mode == b"100755" else 0o644)


def _link(basis: Basis, change: Change, path: str, entries: dict[str, CaptureEntry], verdicts: dict[str, runs_later.Verdict]) -> Edit | None:
    """The edit for a new link: its target is a file of the result tree that is plain source, the link is named as that file would be, and neither
    path has a dot component. Any other link, and any change to a link there already, is not applied."""
    if change.before is not None or change.after_mode != LINK:
        return None
    try:
        target = str((basis.result.directory / path).readlink())
    except OSError:
        return None
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    aim = entries.get(joined)
    if aim is None or aim.mode == LINK or not verdicts[joined].plain or Path(target).is_absolute():
        return None
    held = _open(basis.result.directory, joined, runs_later.READ_LIMIT)
    named = runs_later.classify(path, held[0], 0o644, 1) if held else None
    dotted = any(part.startswith(".") for part in (*path.split("/"), *joined.split("/")))
    return Edit(path, None, None, link=target) if named and named.plain and not dotted else None


def _one(basis: Basis, change: Change, entries: dict[str, CaptureEntry], verdicts: dict[str, runs_later.Verdict], collide: set[str]) -> Edit | Refusal:
    path = os.fsdecode(change.path)
    entry = entries.get(path)
    size = 0 if entry is None else entry.size
    if cause := _never(path):
        return Refusal(path, cause, size)
    if path in collide:
        return Refusal(path, "collision", size)
    if LINK in (change.before_mode, change.after_mode):
        return _link(basis, change, path, entries, verdicts) or Refusal(path, "link", size)
    klass = verdicts[path].klass
    if klass == runs_later.PLAIN and change.before is not None and path not in basis.tracked:
        klass = "untracked"
    if klass != runs_later.PLAIN and not (klass in RULED and _ruled(basis, path)):
        return Refusal(path, klass, size)
    return _edit(basis, change, path, entry) or Refusal(path, "data", size)


def decide(basis: Basis, changes: Sequence[Change], protected: Sequence[tuple[bytes, str]] = ()) -> Decision:
    """The decision for ``changes`` of the stage in ``basis``; ``protected`` are the names the capture left out (a control path, a credential, a
    special file) with their causes, which are refused by name and never read. A fact that cannot be read refuses the file."""
    entries = {os.fsdecode(each.path): each for each in basis.result.entries}
    ordered = sorted(changes, key=lambda change: change.path)
    left = [Refusal(os.fsdecode(name), why) for name, why in protected]
    environment = publish.environment_files(basis.project)
    if environment is None:
        return Decision((), (*left, *(Refusal(os.fsdecode(change.path), "unread") for change in ordered)))
    gone = {os.fsdecode(change.path) for change in ordered if change.after is None}
    files = {path for path, each in entries.items() if each.mode != LINK} | gone
    verdicts = runs_later.tree(files | environment, _reader(basis, entries))
    collide = colliding(files | {path for path, each in entries.items() if each.mode == LINK})
    decided = [_one(basis, change, entries, verdicts, collide) for change in ordered]
    return Decision(tuple(each for each in decided if isinstance(each, Edit)), (*left, *(each for each in decided if isinstance(each, Refusal))))


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
    """The patch of the refused files against the person's checkout, for the session to read: what it wrote itself, kept as data. A file that is
    not text, or larger than what the parent reads, is named with its size and digest and not copied; control, credential and special files
    are never read."""
    out: list[str] = []
    for each in refused:
        shown = each.path.encode("unicode_escape").decode()
        if each.klass in NO_CONTENT:
            out.append(f"# {shown}: not read ({REASONS[each.klass]})\n")
            continue
        new = _open(basis.result.directory, each.path, runs_later.READ_LIMIT + 1)
        old = _open(basis.project, each.path, runs_later.READ_LIMIT + 1)
        data = None if new is None else new[0]
        before, after = _lines(None if old is None else old[0]), _lines(data)
        if before is None or after is None or any(found and len(found[0]) > runs_later.READ_LIMIT for found in (new, old)):
            digest = "none" if data is None else hashlib.sha256(data).hexdigest()
            out.append(f"# {shown}: {each.size} bytes, sha256 {digest}, not copied (not text, or larger than the parent reads)\n")
            continue
        out.extend(difflib.unified_diff(before, after, f"a/{shown}", "/dev/null" if new is None else f"b/{shown}"))
    return "".join(out).encode()

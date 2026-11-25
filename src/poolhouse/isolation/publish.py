"""The `publish` rule: one exact file of the project that publication may apply as the agent made it.

A rule names an exact file and never a directory, a glob, the project root, a path with a dot component, a governing file, a control
name, a credential name, a special file, a link or a name that folds with another (docs/design-isolated-launcher.md, 8.2, AF4). It is
bound to the file's identity and not to a string: the parent directory is held by its device and inode, and so is the file (for a file
not yet in the checkout, its parent and the exact final name), so a replaced file, a case-folded name or a directory the person swapped
matches nothing.
"""

from __future__ import annotations

import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from poolhouse import secretpaths
from poolhouse.coding_workspace_state import CONTROL_PREFIXES
from poolhouse.guard import runs_later
from poolhouse.isolation.toolchain import Identity

__all__ = ["NotPublishable", "PublishRule", "bind", "governing", "matches"]

GLOB = re.compile(r"[*?\[\]{}]")


class NotPublishable(ValueError):
    """The subject cannot be a `publish` rule for any caller; the message is the cause, recorded and never shown to the session."""


@dataclass(frozen=True, slots=True)
class PublishRule:
    """One file of the project, as the launcher found it when it decided the grant."""

    relpath: str
    parent: Identity
    file: Identity | None


def _facts(root: Path):
    """The reader `runs_later.tree` takes: the first bytes, the mode bits and the link count of a regular file, opened without following a link."""

    def read(rel: str) -> tuple[bytes, int, int] | None:
        try:
            descriptor = os.open(root / rel, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return None
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            return (stream.read(runs_later.READ_LIMIT), info.st_mode, info.st_nlink) if stat.S_ISREG(info.st_mode) else None

    return read


def _files(root: Path) -> set[str] | None:
    """Every file of the project the walk reaches, or ``None`` when the project is too large to read what names what."""
    found: set[str] = set()
    for count, (here, dirs, names) in enumerate(os.walk(root)):
        dirs[:] = [d for d in dirs if d not in secretpaths.SKIP_DIRS]
        found.update(Path(here, name).relative_to(root).as_posix() for name in names)
        if count > secretpaths.MAX_WALK:
            return None
    return found


def governing(project: str | os.PathLike[str], relpath: str) -> str:
    """Why ``relpath`` governs what runs or who reviews (a path, a name or a record says it runs by itself later, or a non-plain file of the
    project names it), or empty. A project too large to read for what names what is refused."""
    root = Path(project)
    paths = _files(root)
    if paths is None:
        return "the project is too large to check what names this file"
    verdict = runs_later.tree(paths | {relpath}, _facts(root))[relpath]
    return verdict.sentence if verdict.governing else ""


def _folded(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _held(project: Path, parts: tuple[str, ...]) -> tuple[Identity, os.stat_result | None]:
    """The identity of the directory above the last part, each directory opened without following a link, and the file's own facts."""
    descriptor = os.open(project, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        above = os.fstat(descriptor)
        try:
            return Identity(above.st_dev, above.st_ino), os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return Identity(above.st_dev, above.st_ino), None
    finally:
        os.close(descriptor)


def bind(project: str | os.PathLike[str], subject: str) -> PublishRule:
    """The rule for the exact file ``subject`` of ``project``, or `NotPublishable` with the cause."""
    if not subject or not isinstance(subject, str) or re.search(r"[\x00-\x1f\x7f]", subject):
        raise NotPublishable("empty")
    if GLOB.search(subject):
        raise NotPublishable("glob")
    root = Path(os.path.realpath(project))
    inside = Path(subject)
    relative = inside.relative_to(root) if inside.is_absolute() and root in inside.parents else Path(subject)
    if inside.is_absolute() and root not in inside.parents:
        raise NotPublishable("outside the project")
    parts = relative.parts
    if not parts or parts == (".",) or relative.as_posix() in ("", "."):
        raise NotPublishable("project root")
    if any(part.startswith(".") for part in parts) or relative.is_absolute():
        raise NotPublishable("dot component")
    if parts[0] in CONTROL_PREFIXES:
        raise NotPublishable("control name")
    if secretpaths.path_hit(str(root / relative)) is not None:
        raise NotPublishable("credential name")
    if reason := governing(root, relative.as_posix()):
        raise NotPublishable("governing file: " + reason)
    try:
        parent, facts = _held(root, parts)
    except OSError:
        raise NotPublishable("a link or a missing directory above the file") from None
    if facts is not None:
        if stat.S_ISDIR(facts.st_mode):
            raise NotPublishable("directory")
        if not stat.S_ISREG(facts.st_mode) or facts.st_mode & (stat.S_ISUID | stat.S_ISGID):
            raise NotPublishable("link or special file")
    beside = [other.name for other in root.joinpath(*parts[:-1]).iterdir() if other.name != parts[-1] and _folded(other.name) == _folded(parts[-1])]
    if beside:
        raise NotPublishable("name collision")
    return PublishRule(relative.as_posix(), parent, None if facts is None else Identity(facts.st_dev, facts.st_ino))


def matches(rule: PublishRule, project: str | os.PathLike[str], relpath: str) -> bool:
    """Whether ``relpath`` of ``project`` is the object the rule was bound to: the same spelling, the same directory above it and the same
    file (or, for a file that was not there, still no file there). A replaced file, a case-folded name or a swapped directory is not."""
    if relpath != rule.relpath:
        return False
    try:
        parent, facts = _held(Path(os.path.realpath(project)), PurePosixPath(relpath).parts)
    except OSError:
        return False
    if parent != rule.parent:
        return False
    if rule.file is None:
        return facts is None
    return facts is not None and Identity(facts.st_dev, facts.st_ino) == rule.file

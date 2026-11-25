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
from poolhouse.guard import autoexec
from poolhouse.isolation.toolchain import Identity

__all__ = ["GOVERNING_NAMES", "NotPublishable", "PublishRule", "bind", "governing", "matches"]

GLOB = re.compile(r"[*?\[\]{}]")
PATH_RUN = re.compile(r"[A-Za-z0-9._/@+-]+")
GOVERNING_NAMES = frozenset({"codeowners", "gradlew", "gradlew.bat", "mvnw", "mvnw.cmd", "lefthook.yml", "lefthook.yaml", "dockerfile",
                             "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.lock", "poetry.lock", "uv.lock", "go.mod",
                             "go.sum", "pipfile", "pipfile.lock", "requirements.txt", "workspace", "build.bazel"})
"""Names of governing files beyond the ones `guard.autoexec` knows: review governance, wrapper scripts, hook managers and the records that
decide what an install runs. `guard.runs_later` computes the whole set from structure, the language registry and the project's own wiring
and replaces this and the tables of `guard.autoexec` (design 4.4.2, slice L4)."""
GOVERNING_SUFFIXES = (".jar", ".bzl", ".cmake", ".gradle", ".kts", ".sln", ".csproj", ".props")


class NotPublishable(ValueError):
    """The subject cannot be a `publish` rule for any caller; the message is the cause, recorded and never shown to the session."""


@dataclass(frozen=True, slots=True)
class PublishRule:
    """One file of the project, as the launcher found it when it decided the grant."""

    relpath: str
    parent: Identity
    file: Identity | None


def governing(project: str | os.PathLike[str], relpath: str) -> str:
    """Why ``relpath`` governs what runs or who reviews (it runs by itself later, or is named by a file that does), or empty."""
    name = PurePosixPath(relpath).name.lower()
    if reason := autoexec.path_reason(relpath, [str(project)]):
        return reason
    if name in GOVERNING_NAMES or name.endswith(GOVERNING_SUFFIXES):
        return "decides what runs or who reviews"
    return "named by a file that runs it" if _named_by_a_governing_file(Path(project), relpath) else ""


def _is_governing(root: Path, found: Path) -> bool:
    rel = found.relative_to(root).as_posix()
    low = found.name.lower()
    return bool(autoexec.path_reason(rel, [str(root)])) or low in GOVERNING_NAMES or low.endswith(GOVERNING_SUFFIXES)


def _named_by_a_governing_file(root: Path, relpath: str) -> bool:
    """Whether a governing file of the project, or a file one of them names in turn, contains the path ``relpath`` as a maximal run of path
    characters that resolves to it from the project root or from that file's directory."""
    target, seen, queue = (root / relpath).resolve(), set(), []
    for count, (here, dirs, names) in enumerate(os.walk(root)):
        dirs[:] = [d for d in dirs if d not in secretpaths.SKIP_DIRS]
        queue += [Path(here) / n for n in names if _is_governing(root, Path(here) / n)]
        if count > secretpaths.MAX_WALK:
            return True
    while queue:
        current = queue.pop()
        if current in seen or not current.is_file():
            continue
        seen.add(current)
        try:
            text = current.read_bytes()[:autoexec.MAX_FILE].decode("utf-8", errors="replace")
        except OSError:
            continue
        for token in PATH_RUN.findall(text):
            for base in (root, current.parent):
                reached = (base / token).resolve()
                if reached == target:
                    return True
                if reached.is_file() and root in reached.parents and reached not in seen:
                    queue.append(reached)
    return False


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

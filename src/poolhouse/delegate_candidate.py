"""What a delegate's passes changed: the change as a reviewer reads it, carried from one confinement's stage into another's and, with no sandbox,
found by comparing a folder with the copy taken before the work."""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import safelaunch, secretpaths
from poolhouse.activity import source_snapshot
from poolhouse.activity.source_snapshot import Capture
from poolhouse.coding_workspace import Change, between
from poolhouse.guard import harnesses
from poolhouse.isolation import publication

__all__ = ["Edit", "Folder", "carry", "changed", "diff", "edits", "git", "instructs", "listed", "staged"]

SHOWN_BYTES = 512 << 10
"""The largest file whose lines a change shows; a larger one, or one that is not text, is only named."""
FOLDER_BYTES = 512 << 20
"""The most a folder with no sandbox may hold for a copy of it to be taken."""
CUT = "\n[the change is cut here: read the files themselves]"
_NAMED = frozenset(publication.fold(each) for each in harnesses.names())


def instructs(path: str) -> bool:
    """Whether ``path`` is a name a coding tool reads instructions or settings from (`guard.harnesses`), at any depth."""
    return any(part in _NAMED for part in publication.fold(path).split("/"))


@dataclass(frozen=True, slots=True)
class Edit:
    """One changed file: its path and where its bytes were before and are after, None on the side that has none."""

    path: str
    before: Path | None
    after: Path | None


def staged(box: Any) -> tuple[Capture, tuple[bytes, ...]]:
    """What the confinement ``box`` holds (`isolation.workspace.Session.captured`): its stage as it is now, or the capture it was fixed at, and
    the control and credential names that are never carried."""
    with box.session.workspace.lock:
        return box.session.captured()


def changed(before: Capture, after: Capture) -> tuple[Change, ...]:
    """The changes from ``before`` to ``after`` that a publication would look at: build junk left out."""
    return tuple(each for each in between(before, after) if not publication.junk(os.fsdecode(each.path)))


def edits(before: Path, after: Path, changes: Sequence[Change]) -> list[Edit]:
    """``changes`` as files under the two captured folders."""
    return [Edit(name, None if each.before is None else before / name, None if each.after is None else after / name)
            for each, name in ((each, os.fsdecode(each.path)) for each in changes)]


def _inside(root: Path, name: str) -> Path | None:
    """Where ``name`` is under ``root``, or None for a name that would be written through a link or a file, or into `.git`."""
    parts = name.split("/")
    if any(part in ("", ".", "..") or part.casefold() == ".git" for part in parts):
        return None
    here = root
    for part in parts[:-1]:
        here = here / part
        if here.is_symlink() or (here.exists() and not here.is_dir()):
            return None
    return root.joinpath(*parts)


def _cleared(target: Path) -> None:
    """Remove the file or link at ``target``, or the empty folders left where a file is to be; OSError when something is still in them."""
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        for folder, _dirs, _files in os.walk(target, topdown=False):
            Path(folder).rmdir()


def carry(changes: Sequence[Change], source: Path, stage: Path) -> list[str]:
    """Write ``changes``, whose new bytes are under ``source``, into the folder ``stage``, which no process writes meanwhile; the paths that could
    not be written inside it. A link is made as a link and nothing is written through one."""
    root, left = Path(os.path.realpath(stage)), []
    for change in sorted(changes, key=lambda each: (each.after is not None, each.path)):   # removals first: a file may take a folder's name
        name = os.fsdecode(change.path)
        target = _inside(root, name)
        if target is None:
            left.append(name)
            continue
        try:
            _cleared(target)
            if change.after is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if change.after_mode == b"120000":
                target.symlink_to((source / name).readlink())
            else:
                shutil.copyfile(source / name, target, follow_symlinks=False)
                target.chmod(0o755 if change.after_mode == b"100755" else 0o644)
        except OSError:
            left.append(name)
    return left


def _lines(path: Path | None) -> list[str] | None:
    """The lines of a side of a change; None for a file this does not show."""
    if path is None:
        return []
    try:
        if path.is_symlink():
            return [f"a link to {path.readlink()}\n"]
        if path.stat().st_size > SHOWN_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return None if b"\0" in data else data.decode("utf-8", "replace").splitlines(keepends=True)


def diff(changes: Sequence[Edit], limit: int = 0) -> str:
    """``changes`` as text: one line a file with the lines it gained and lost, then a unified diff of each; cut at ``limit`` characters."""
    if not changes:
        return "no file was changed"
    names, bodies = [], []
    for each in changes:
        mark = "added" if each.before is None else "deleted" if each.after is None else "changed"
        old, new = _lines(each.before), _lines(each.after)
        if old is None or new is None:
            names.append(f"{mark} {each.path} (not shown: too large or not text)")
            continue
        made = list(difflib.unified_diff(old, new, f"a/{each.path}" if each.before else "/dev/null",
                                         f"b/{each.path}" if each.after else "/dev/null"))
        added = sum(line.startswith("+") for line in made[2:])
        removed = sum(line.startswith("-") for line in made[2:])
        names.append(f"{mark} {each.path} (+{added} -{removed})")
        bodies.append("".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in made))
    text = "\n".join(names) + "\n\n" + "".join(bodies)
    return text[:limit] + CUT if limit and len(text) > limit else text


def git(folder: str | Path, *words: str, env: Mapping[str, str] | None = None) -> bytes:
    """What git says of ``words`` in ``folder``, with ``env`` added to a closed environment: no hook, file monitor, global configuration or
    optional lock. ValueError when there is no git or it fails."""
    program = safelaunch.which("git")
    if not program:
        raise ValueError("git is not installed")
    argv = [program, "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}", "--no-optional-locks", "-C", str(folder), *words]
    given = {**source_snapshot.git_environment(), **(env or {})}
    try:
        done = subprocess.run(argv, env=given, stdin=subprocess.DEVNULL, capture_output=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as why:
        raise ValueError(f"git did not end within {why.timeout:.0f}s") from why
    if done.returncode:
        said = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ValueError(f"git failed with status {done.returncode}" + (f": {said[-1][:200]}" if said else ""))
    return done.stdout


def listed(root: Path) -> list[str]:
    """The files of ``root``: those git lists (held and new ones it does not ignore), else every file; never a credential name or build junk."""
    if os.path.lexists(root / ".git"):
        found = [os.fsdecode(each) for each in git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0") if each]
    else:
        found = [(Path(folder) / name).relative_to(root).as_posix()
                 for folder, dirs, names in os.walk(root) if not dirs.__setitem__(slice(None), [d for d in dirs if d != ".git"])
                 for name in names]
    return sorted({name for name in found if not secretpaths.name_hit(name) and not publication.junk(name)
                   and ((root / name).is_file() or (root / name).is_symlink())})


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        target.symlink_to(source.readlink())
    else:
        shutil.copyfile(source, target)


class Folder:
    """The folder ``root`` as it was when this was made, copied into ``held``: what a run with no sandbox is compared with. ValueError for a folder
    larger than `FOLDER_BYTES`."""

    def __init__(self, root: Path, held: Path) -> None:
        self.root, self.held = root, held
        self.names = listed(root)
        size = sum((root / name).lstat().st_size for name in self.names)
        if size > FOLDER_BYTES:
            raise ValueError(f"the folder holds {size} bytes, more than the {FOLDER_BYTES} a copy of it may take")
        for name in self.names:
            _copy(root / name, held / name)

    def edits(self) -> list[Edit]:
        """What differs from the copy now."""
        now = set(listed(self.root))
        found = []
        for name in sorted(now | set(self.names)):
            old = self.held / name if name in self.names else None
            new = self.root / name if name in now else None
            if old is None or new is None or old.is_symlink() != new.is_symlink() or _lines_differ(old, new):
                found.append(Edit(name, old, new))
        return found

    def copy(self, into: Path) -> None:
        """The folder as it is now, copied into ``into`` for a reviewer to read, but each instruction or settings name as it was before the work."""
        now = {name for name in listed(self.root) if not instructs(name)}
        for name in sorted(now | {name for name in self.names if instructs(name)}):
            _copy((self.held if instructs(name) else self.root) / name, into / name)


def _lines_differ(old: Path, new: Path) -> bool:
    if old.is_symlink():
        return old.readlink() != new.readlink()
    return old.read_bytes() != new.read_bytes()

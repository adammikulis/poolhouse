"""Applying one verified change set to the person's checkout without ever overwriting a version it has not verified.

The parent acts with the person's authority on paths the agent chose, and the person's editors write the same files meanwhile, so each path
is applied in five steps that pin the read of the live file to the write (docs/design-isolated-launcher.md, 4.4.3): hold the live file and
record it, prepare the result beside it, exchange the two names in one operation, verify that the displaced version is the one held, and
either unlink it (it equals the base) or exchange back and keep the agent's version. A file that nothing else can give back (an `Edit` with
`kept_first`) has the bytes it displaces copied, through the held descriptor, into a read-only file named by their digest before anything is
changed. A record of the intended writes is written first, so a crash leaves a state `recover` completes or rolls back by the same
verification. Nothing here classifies a file: what may be applied is decided before, by `guard.runs_later`, and arrives as `Edit`s.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import stat
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from poolhouse import files

__all__ = ["Edit", "Outcome", "Places", "apply", "recover"]

TEMP = ".poolhouse-apply-"
CONFLICT = ".poolhouse-conflict-"
NO_EXCHANGE = (errno.ENOTSUP, errno.EINVAL, errno.ENOSYS)


@dataclass(frozen=True, slots=True)
class Edit:
    """One path of the change set: the digest of the base (``None`` when the path was absent), the result bytes (``None`` to delete) and its mode.
    ``link`` makes the edit a new relative symbolic link to that target, which is only ever installed where nothing is. ``kept_first`` says that
    nothing but this call can give back the version an overwrite or a deletion displaces, so it is kept first, and the change stops where it cannot be."""

    path: str
    base: str | None
    data: bytes | None
    mode: int = 0o644
    link: str | None = None
    kept_first: bool = False


@dataclass(frozen=True, slots=True)
class Outcome:
    """What became of one path: ``applied``, ``unchanged`` (the live file already equalled the result), ``conflict`` (the person's version stays
    and the agent's is kept at ``kept``, and ``beside`` names the person's displaced version when two saves raced), ``refused`` (a link in the
    live path) or ``unkept`` (the displaced version could not be kept first, so nothing was changed); ``method`` says which of the exchange, the
    claim, the creation or the deletion was used, and ``restored`` the name an applied path's displaced version was kept under."""

    path: str
    state: str
    method: str = ""
    kept: Path | None = None
    beside: Path | None = None
    restored: str = ""


@dataclass(frozen=True, slots=True)
class Places:
    """Where an application writes besides the project: ``log`` receives the intent record and a line for each step; ``keep`` receives the agent's
    version of every path that was not applied; ``restore`` is the descriptor of the directory that receives the version every `kept_first` path
    displaces, before it is displaced; ``after`` is called with the path and the step reached after each step (a seam for a test to be the person
    saving at that moment)."""

    log: Path
    keep: Path
    restore: int
    after: Callable[[str, str], None] = lambda path, step: None


@dataclass(frozen=True, slots=True)
class Target:
    """The directory descriptor and name of one path, with what a step needs besides: the edit, where the log and the kept versions go."""

    parent: int
    name: str
    edit: Edit
    log: Path
    keep: Path
    restore: int
    after: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class Held:
    """The live file as step 1 held it: the descriptor kept to the end, the digest read through it and the identity recorded."""

    descriptor: int
    digest: str
    identity: tuple[int, ...]


def digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _read(descriptor: int, sink: Callable[[bytes], object] | None = None) -> tuple[str, tuple[int, ...]]:
    """The digest of what the descriptor holds from its start and the identity of the file: device, inode, size, modification time and mode. The
    change time is not in it: the exchange itself renames the held file, which moves its change time, and the digest read again through the
    held descriptor is what shows a write that came after step 1. ``sink`` is given every block read."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    hasher = hashlib.sha256()
    while block := os.read(descriptor, 1 << 20):
        hasher.update(block)
        if sink is not None:
            sink(block)
    info = os.fstat(descriptor)
    return hasher.hexdigest(), (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)


def _directory(project: Path, parts: Sequence[str], *, create: bool = False) -> int:
    """The directory of a path, each component opened without following a link, so a link in the live path is an error and never a way out.
    ``create`` makes a missing component (for a new file), by a make-directory relative to the one held, which never follows a link either."""
    descriptor = os.open(project, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o755, dir_fd=descriptor)
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


def _keep(target: Target) -> Path:
    """Keep the agent's version of a path for the session to read, under a counter name the parent chooses and never the agent's own."""
    target.keep.mkdir(parents=True, exist_ok=True)
    kept = target.keep / f"v{len(list(target.keep.iterdir()))}-{uuid.uuid4().hex[:8]}.kept"
    kept.write_bytes(target.edit.data if target.edit.data is not None else (target.edit.link or "").encode())
    return kept


def _note(log: Path, record: dict) -> None:
    with log.open("a") as stream:
        stream.write(json.dumps(record) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _prepare(target: Target) -> tuple[str, tuple[int, int]]:
    name = f"{TEMP}{uuid.uuid4().hex}"
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target.parent)
    try:
        os.write(descriptor, target.edit.data or b"")
        os.fchmod(descriptor, target.edit.mode & 0o755)
        os.fsync(descriptor)
        info = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    return name, (info.st_dev, info.st_ino)


def _exchange(parent: int, one: str, other: str) -> str:
    """Put the names ``one`` and ``other`` in each other's place: by one exchange, or by claiming ``other`` and installing ``one`` over the
    gap with a rename that never replaces (the path is absent between the two)."""
    try:
        files.swap(one, other, directory_fd=parent)
        return "exchange"
    except OSError as error:
        if error.errno not in NO_EXCHANGE:
            raise
    claimed = f"{TEMP}{uuid.uuid4().hex}"
    files.promote_new(other, claimed, source_fd=parent, target_fd=parent)
    try:
        files.promote_new(one, other, source_fd=parent, target_fd=parent)
    except FileExistsError:
        files.promote_new(claimed, other, source_fd=parent, target_fd=parent)
        raise
    files.promote_new(claimed, one, source_fd=parent, target_fd=parent)
    return "claim"


def _save(target: Target, held: Held) -> Outcome | None:
    """Keep the bytes the held file holds before anything displaces them, as a read-only file named by their digest in the restore directory,
    written through the held descriptor and made durable. ``None`` when they are kept; ``unkept`` when that cannot be written; a conflict when the
    file was saved while it was read, so what was read is not the version held."""
    directory, temp = target.restore, f"{TEMP}{uuid.uuid4().hex}"
    try:
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=directory), "wb") as stream:
            same = _read(held.descriptor, stream.write) == (held.digest, held.identity)
            stream.flush()
            os.fsync(stream.fileno())
        if same:
            with contextlib.suppress(FileExistsError):
                files.promote_new(temp, held.digest, source_fd=directory, target_fd=directory)
            os.fsync(directory)
    except OSError:
        return Outcome(target.edit.path, "unkept")
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temp, dir_fd=directory)
    if not same:
        return Outcome(target.edit.path, "conflict", kept=_keep(target))
    target.after(target.edit.path, "restored")
    return None


def _one(project: Path, edit: Edit, places: Places) -> Outcome:
    parts = Path(edit.path).parts
    try:
        parent = _directory(project, parts[:-1], create=edit.base is None and (edit.data is not None or edit.link is not None))
    except OSError:
        return Outcome(edit.path, "refused")
    try:
        return _apply(Target(parent, parts[-1], edit, places.log, places.keep, places.restore, places.after))
    finally:
        os.close(parent)


def _apply(target: Target) -> Outcome:
    edit, result = target.edit, digest(target.edit.data)
    try:
        descriptor = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=target.parent)
    except FileNotFoundError:
        descriptor = None
    except OSError:
        return Outcome(edit.path, "refused")
    try:
        held = Held(descriptor, *_read(descriptor)) if descriptor is not None else None
        target.after(edit.path, "held")
        if edit.link is not None:
            return _link(target, held)
        live = held.digest if held else None
        if live == result:
            return Outcome(edit.path, "unchanged")
        if live != edit.base or (held and not stat.S_ISREG(held.identity[4])):
            return Outcome(edit.path, "conflict", kept=_keep(target))
        if held is not None and edit.kept_first and (stopped := _save(target, held)):
            return stopped
        if edit.data is None:
            return _delete(target, held)
        temp, ours = _prepare(target)
        _note(target.log, {"path": edit.path, "step": "prepared", "temp": temp, "base": edit.base, "result": result})
        target.after(edit.path, "prepared")
        if held is None:
            return _create(target, temp)
        try:
            method = _exchange(target.parent, temp, target.name)
        except FileNotFoundError:
            os.unlink(temp, dir_fd=target.parent)
            return Outcome(edit.path, "conflict", kept=_keep(target))
        _note(target.log, {"path": edit.path, "step": "swapped", "temp": temp, "base": edit.base, "result": result})
        target.after(edit.path, "swapped")
        return _finish(target, held, temp, ours, method)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _finish(target: Target, held: Held, temp: str, ours: tuple[int, int], method: str) -> Outcome:
    """Steps 4 and 5: the displaced version, read through the descriptor held since step 1, must be what step 1 recorded."""
    edit, parent = target.edit, target.parent
    now = _read(held.descriptor)
    at_temp = os.stat(temp, dir_fd=parent, follow_symlinks=False)
    if now == (held.digest, held.identity) and (at_temp.st_dev, at_temp.st_ino) == held.identity[:2]:
        os.unlink(temp, dir_fd=parent)
        _note(target.log, {"path": edit.path, "step": "done"})
        return Outcome(edit.path, "applied", method, restored=held.digest if edit.kept_first else "")
    here = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
    if (here.st_dev, here.st_ino) == ours:
        _exchange(parent, temp, target.name)
        os.unlink(temp, dir_fd=parent)
        _note(target.log, {"path": edit.path, "step": "undone"})
        return Outcome(edit.path, "conflict", method, kept=_keep(target))
    aside = f"{target.name}{CONFLICT}{uuid.uuid4().hex[:8]}"
    files.promote_new(temp, aside, source_fd=parent, target_fd=parent)
    _note(target.log, {"path": edit.path, "step": "conflict", "beside": aside})
    return Outcome(edit.path, "conflict", method, kept=_keep(target), beside=Path(aside))


def _link(target: Target, held: Held | None) -> Outcome:
    """Install a new symbolic link with a rename that never replaces: anything the person put at the name stays, and the link is kept instead."""
    edit, parent = target.edit, target.parent
    if held is not None or edit.base is not None:
        return Outcome(edit.path, "conflict", "link", kept=_keep(target))
    temp = f"{TEMP}{uuid.uuid4().hex}"
    os.symlink(edit.link, temp, dir_fd=parent)
    _note(target.log, {"path": edit.path, "step": "prepared", "temp": temp, "base": None, "result": None})
    target.after(edit.path, "prepared")
    try:
        files.promote_new(temp, target.name, source_fd=parent, target_fd=parent)
    except FileExistsError:
        os.unlink(temp, dir_fd=parent)
        return Outcome(edit.path, "conflict", "link", kept=_keep(target))
    _note(target.log, {"path": edit.path, "step": "done"})
    return Outcome(edit.path, "applied", "link")


def _create(target: Target, temp: str) -> Outcome:
    try:
        files.promote_new(temp, target.name, source_fd=target.parent, target_fd=target.parent)
    except FileExistsError:
        os.unlink(temp, dir_fd=target.parent)
        return Outcome(target.edit.path, "conflict", "create", kept=_keep(target))
    _note(target.log, {"path": target.edit.path, "step": "done"})
    return Outcome(target.edit.path, "applied", "create")


def _delete(target: Target, held: Held | None) -> Outcome:
    edit, parent = target.edit, target.parent
    if held is None:
        return Outcome(edit.path, "unchanged")
    claimed = f"{TEMP}{uuid.uuid4().hex}"
    files.promote_new(target.name, claimed, source_fd=parent, target_fd=parent)
    _note(target.log, {"path": edit.path, "step": "claimed", "temp": claimed, "base": edit.base, "result": None})
    target.after(edit.path, "claimed")
    if _read(held.descriptor) == (held.digest, held.identity):
        os.unlink(claimed, dir_fd=parent)
        _note(target.log, {"path": edit.path, "step": "done"})
        return Outcome(edit.path, "applied", "delete", restored=held.digest if edit.kept_first else "")
    try:
        files.promote_new(claimed, target.name, source_fd=parent, target_fd=parent)
        return Outcome(edit.path, "conflict", "delete", kept=_keep(target))
    except FileExistsError:
        aside = f"{target.name}{CONFLICT}{uuid.uuid4().hex[:8]}"
        files.promote_new(claimed, aside, source_fd=parent, target_fd=parent)
        return Outcome(edit.path, "conflict", "delete", kept=_keep(target), beside=Path(aside))


def apply(project: str | os.PathLike[str], edits: Sequence[Edit], places: Places) -> list[Outcome]:
    """Apply ``edits`` to ``project`` one path at a time and say what became of each; the intent record is written before the first write."""
    root, log = Path(os.path.realpath(project)), places.log
    log.parent.mkdir(parents=True, exist_ok=True)
    _note(log, {"step": "intent", "paths": [{"path": e.path, "base": e.base, "result": digest(e.data)} for e in edits]})
    outcomes = [_one(root, edit, places) for edit in edits]
    _note(log, {"step": "finished"})
    return outcomes


def recover(project: str | os.PathLike[str], log: Path) -> list[Outcome]:
    """Complete or roll back what a crash left, by the verification of the five steps: a temporary name that holds the displaced version equal to
    the base is unlinked (the swap stands); one that holds anything else is exchanged back, so the person's newest version is live again."""
    root, last = Path(os.path.realpath(project)), {}
    for line in log.read_text().splitlines() if log.exists() else ():
        record = json.loads(line)
        if "path" in record:
            last[record["path"]] = record
    outcomes = []
    for path, record in last.items():
        if record["step"] in ("done", "undone", "conflict"):
            continue
        parts = Path(path).parts
        try:
            parent = _directory(root, parts[:-1])
        except OSError:
            continue
        try:
            outcomes.append(_recover_one(parent, parts[-1], record, path))
        finally:
            os.close(parent)
    if last:
        _note(log, {"step": "recovered"})
    return outcomes


def _recover_one(parent: int, name: str, record: dict, path: str) -> Outcome:
    temp = record.get("temp", "")
    try:
        if stat.S_ISLNK(os.stat(temp, dir_fd=parent, follow_symlinks=False).st_mode):
            os.unlink(temp, dir_fd=parent)
            return Outcome(path, "unchanged", "recovered")
        descriptor = os.open(temp, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except OSError:
        return Outcome(path, "unchanged")
    try:
        held = _read(descriptor)[0]
    finally:
        os.close(descriptor)
    if record["step"] == "prepared":
        os.unlink(temp, dir_fd=parent)
        return Outcome(path, "unchanged", "recovered")
    if held == record["base"]:
        os.unlink(temp, dir_fd=parent)
        return Outcome(path, "applied", "recovered")
    if record["step"] == "swapped":
        _exchange(parent, temp, name)
        os.unlink(temp, dir_fd=parent)
    else:
        files.promote_new(temp, name, source_fd=parent, target_fd=parent)
    return Outcome(path, "conflict", "recovered")

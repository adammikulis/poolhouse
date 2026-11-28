"""Removing a model's files from the Hugging Face cache: ``poolhouse-models remove hf:owner/repo/file.gguf``.

One reference names one build: the file, or every shard of a sharded one, in every revision the cache holds of that repository.
A file's bytes go only when no other file in the cache still links to them, nothing outside the cache is touched, and a build
a running process names is refused.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from poolhouse import activity, hub, procscan
from poolhouse.hub import remote
from poolhouse.hub.naming import _SHARD
from poolhouse.units import human_bytes

__all__ = ["Removal", "in_use", "plan", "remove"]


@dataclass(frozen=True)
class Removal:
    """What removing ``ref`` takes away: the files of the build, the stored bytes nothing else links to, and their size."""

    ref: str
    files: tuple[Path, ...]
    blobs: tuple[Path, ...]
    bytes: int

    def said(self, *, done: bool = True) -> str:
        word = "removed" if done else "would remove"
        return f"{word} {self.ref}: {len(self.files)} file(s), freeing {human_bytes(self.bytes)}"


def _chain(path: Path) -> list[Path]:
    """The stored files ``path`` leads to, one link at a time."""
    out: list[Path] = []
    while path.is_symlink() and len(out) < 8:
        path = Path(os.path.normpath(path.parent / path.readlink()))
        out.append(path)
    return out


def _links(cache: Path) -> Iterable[Path]:
    """Every link of the cache, whichever repository it is in."""
    for folder, folders, files in os.walk(cache):
        for name in [*files, *folders]:
            path = Path(folder) / name
            if path.is_symlink():
                yield path


def plan(ref: str) -> Removal:
    """What `remove` would take away; `ValueError` when ``ref`` names no file the cache holds."""
    asked = remote.parse(ref)
    if not asked.file or asked.quant or ".." in asked.file.split("/") or not asked.file.endswith(".gguf"):
        raise ValueError(f"name one file of the build: hf:owner/repo/file.gguf, not {ref!r}")
    cache = Path(hub.hub_cache()).absolute()
    snapshots = cache / f"models--{asked.repo.replace('/', '--')}" / "snapshots"
    wanted = Path(asked.file)
    shard = _SHARD.search(wanted.name)
    pattern = _SHARD.sub(lambda found: "-?????" + found[0][6:], wanted.name) if shard else wanted.name
    files = []
    for snapshot in sorted(snapshots.iterdir()) if snapshots.is_dir() and not snapshots.is_symlink() else []:
        folder = snapshot / wanted.parent
        if folder.is_dir() and folder.resolve() == snapshots.resolve() / snapshot.name / wanted.parent:
            files += [path for path in sorted(folder.glob(pattern) if shard else [folder / pattern])
                      if path.is_symlink() or path.is_file()]
    if not files:
        raise ValueError(f"the cache at {cache} holds no {asked.repo}/{asked.file}")
    gone = set(files)
    stored: list[Path] = []
    links = {path: Path(os.path.normpath(path.parent / path.readlink())) for path in _links(cache)}
    queue = list(files)
    while queue:
        for target in _chain(queue.pop())[:1]:
            kept_by = [path for path, to in links.items() if to == target and path not in gone]
            if target in gone or kept_by or cache not in target.parents or not (target.is_symlink() or target.is_file()):
                continue
            gone.add(target)
            stored.append(target)
            queue.append(target)
    size = sum(path.lstat().st_size for path in [*files, *stored] if not path.is_symlink())
    return Removal(ref, tuple(files), tuple(stored), size)


def in_use(paths: Iterable[Path]) -> str:
    """Which running process names one of ``paths``, or the file it leads to, on its command line; "" when none does."""
    names = {str(each) for path in paths for each in (path, os.path.realpath(path))}
    for _process, info in procscan.readable(["pid", "cmdline"]):
        line = " ".join(info.get("cmdline") or ())
        if info.get("pid") != os.getpid() and any(name in line for name in names):
            return f"pid {info.get('pid')} is running with it"
    return ""


def remove(ref: str, *, dry_run: bool = False, used: Callable[[Iterable[Path]], str] = in_use) -> Removal:
    """Take the build ``ref`` names out of the cache, recorded in the activity log. `ValueError` when it is not there or in use."""
    found = plan(ref)
    if why := used([*found.files, *found.blobs]):
        raise ValueError(f"{ref} is in use and stays: {why}")
    if dry_run:
        return found
    for path in (*found.files, *found.blobs):
        path.unlink(missing_ok=True)
    for folder in {path.parent for path in found.files}:
        while folder.parent.name != "snapshots" and folder.name != "snapshots":
            with contextlib.suppress(OSError):
                folder.rmdir()
            folder = folder.parent
    activity.record("model.remove", subject=found.ref, outcome="removed",
                    meta={"files": len(found.files), "bytes": found.bytes})
    return found

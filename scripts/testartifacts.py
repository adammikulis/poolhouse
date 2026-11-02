"""Remove the build products of old source snapshots from the shared cargo target folder.

Every staged source snapshot has its own path, so its workspace crates and test executables get names no later snapshot
reuses, while the folder is shared and, being used every day, is never idle as a whole: it grew past 80 GB. A product of a
workspace crate or of a test target of ``app/`` that nothing has used for ``IDLE_S`` seconds goes; registry dependencies stay.
Cargo rebuilds a product it finds missing, so a pruned file that is wanted again only costs its compile.
"""

from __future__ import annotations

import re
import shutil
import time
import tomllib
from pathlib import Path

NAMED = re.compile(r"^(?P<lib>lib)?(?P<name>\w+?)-(?P<hash>[0-9a-z]{9,16})(?:\..+)?$")
"""``libpoolhouse_node-<hash>.rlib``, ``shard-<hash>``, ``shard-<hash>.d`` and the incremental folder ``shard-<hash>``."""
FOLDERS = (("debug", "deps"), ("debug", "incremental"))
STAMP = "artifacts-pruned"
"""A file of the target folder whose time says when the last walk began."""
EVERY_S = 24 * 3600


def names(app: Path, files: list[str]) -> tuple[set[str], set[str]]:
    """The crate names and the integration-test target names of the workspace files ``files`` under ``app``."""
    crates, targets = set(), set()
    for name in files:
        path = Path(name)
        if path.name == "Cargo.toml" and len(path.parts) > 1:
            crates.add(path.parent.name.replace("-", "_"))
            try:
                crates.add(tomllib.loads((app / name).read_text())["package"]["name"].replace("-", "_"))
            except (OSError, KeyError, ValueError):
                continue
        elif path.suffix == ".rs" and path.parent.name == "tests":
            targets.add(path.stem)
    return crates, targets


def idle(entry: Path, now: float, idle_s: float) -> bool:
    """Whether nothing touched ``entry`` in ``idle_s`` seconds; one that vanishes while this looks is not idle."""
    try:
        return now - entry.stat().st_mtime > idle_s
    except FileNotFoundError:
        return False


def prune_when_due(target: Path, app: Path, files: list[str], idle_s: float, every_s: float = EVERY_S) -> int:
    """``prune`` for the workspace ``files`` of ``app``, at most once in ``every_s`` seconds (the walk reads every entry of the folder)."""
    stamp = target / STAMP
    if not idle(stamp, time.time(), every_s) and stamp.exists():
        return 0
    stamp.touch()
    crates, tests = names(app, files)
    return prune(target, crates, tests, idle_s)


def prune(target: Path, crates: set[str], tests: set[str], idle_s: float, now: float | None = None) -> int:
    """Remove the idle products of ``crates`` and ``tests`` from ``target``; how many entries went."""
    now = time.time() if now is None else now
    removed = 0
    for parts in FOLDERS:
        folder = target.joinpath(*parts)
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            found = NAMED.match(entry.name)
            if not found:
                continue
            name = found["name"]
            ours = name in crates if found["lib"] else name in tests or name in crates
            if ours and idle(entry, now, idle_s):
                shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink(missing_ok=True)
                removed += 1
    return removed

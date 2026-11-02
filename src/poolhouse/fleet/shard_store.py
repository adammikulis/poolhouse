"""Where a device keeps the shards sent to it: one folder for each, owned by the device that first uploaded to it.

A folder holds the packed tree while it arrives (``tree.tgz.part``), the tree, the spec the daemon wrote and the executor's result.
Only the owner writes to it, a device holds a bounded number of folders, and a folder nobody finished or used is dropped.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from poolhouse.files import promote

from .shard_tree import MOST_PACKED

__all__ = ["append", "claim", "folder_of", "owner", "prune", "room"]

OWNER, TREE = "owner", "tree.tgz"
MOST_KEPT = 8
MOST_OWNED = 4
IDLE_S = 600.0
KEPT_S = 24 * 3600.0


def folder_of(files_root: Path, shard: str) -> Path:
    """Where the shard ``shard`` keeps its tree, its spec and its result."""
    return files_root / "shards" / shard


def owner(folder: Path) -> str:
    """The fingerprint of the device that owns ``folder``, or the empty string when nobody does."""
    try:
        return (folder / OWNER).read_text(encoding="utf-8")
    except OSError:
        return ""


def claim(folder: Path, member: str) -> bool:
    """Make ``member`` the owner of ``folder`` when it has none; whether ``member`` owns it."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with (folder / OWNER).open("x", encoding="utf-8") as handle:
            handle.write(member)
        return True
    except FileExistsError:
        return owner(folder) == member


def touched(folder: Path) -> float:
    """When anything in ``folder`` last changed."""
    return max((p.stat().st_mtime for p in (folder, *folder.glob("*"))), default=0.0)


def folders(files_root: Path) -> list[Path]:
    """The shard folders this device holds."""
    return [p for p in (files_root / "shards").glob("*") if p.is_dir()]


def prune(files_root: Path, live: set[str], held: set[str], keep: int = MOST_KEPT) -> None:
    """Drop the folders no job needs: one nobody finished after `IDLE_S`, one a job used after `KEPT_S`, and the oldest beyond ``keep`` in all.

    ``live`` are the shards with a job queued or running; ``held`` those with any job."""
    now, idle = time.time(), sorted((p for p in folders(files_root) if p.name not in live), key=touched, reverse=True)
    for index, folder in enumerate(idle):
        if index >= keep - len(live) or now - touched(folder) > (KEPT_S if folder.name in held else IDLE_S):
            shutil.rmtree(folder, ignore_errors=True)


def room(files_root: Path, member: str, live: set[str], held: set[str]) -> str:
    """Why a new shard from ``member`` cannot be held now (``''`` when it can), after dropping what is no longer needed."""
    prune(files_root, live, held, MOST_KEPT - 1)
    kept = folders(files_root)
    if len(kept) >= MOST_KEPT:
        return f"this device holds {len(kept)} shards already; try again when one has ended"
    if sum(owner(p) == member for p in kept) >= MOST_OWNED:
        return f"this device holds {MOST_OWNED} of your shards already; wait for one to end"
    return ""


def append(folder: Path, offset: int, total: int, body: bytes) -> tuple[int, str]:
    """Add ``body`` at ``offset`` of a tree of ``total`` bytes, in order; ``(status, said)``. The last bytes make the tree."""
    if not 0 < total <= MOST_PACKED:
        return 400, f"the tree is 1 to {MOST_PACKED} bytes and Content-Range says how many"
    if (folder / TREE).exists():
        return 409, "that shard's tree is already here; send a new shard"
    part = folder / f"{TREE}.part"
    held = part.stat().st_size if part.exists() else 0
    if offset != held or held + len(body) > total:
        return 416, f"this device holds {held} bytes of that tree; send the next bytes from there, within the {total} declared"
    with part.open("ab") as handle:
        handle.write(body)
    if held + len(body) == total:
        promote(part, folder / TREE)
    return 200, ""

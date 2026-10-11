"""The spelling a claim's key is made under: what a resource is called, so two names for it clash."""

import hashlib
from pathlib import Path

from poolhouse import worktreerules
from poolhouse.home import expand

__all__ = ["normal", "shared_root"]

SHARED = ".poolhouse-areas"


def shared_root(primary: Path) -> Path:
    """Where the areas of the repository whose primary checkout is ``primary`` are spelled: a folder of its own at the top of the
    disk, so an area nests in no checkout and a claim on a whole checkout never reaches it."""
    return Path(primary.anchor) / SHARED / hashlib.sha256(str(primary).encode()).hexdigest()[:16]


def normal(kind, key):
    """The spelling of a resource's key a claim is made under: a port as a number, a path resolved, an area
    named under `shared_root` of its repository by its path in the checkout, so every worktree of one repository shares it."""
    if kind == 'port':
        if not key.isdigit() or not 0 < int(key) < 65536:
            raise ValueError('a port is a number from 1 to 65535')
        return str(int(key))
    if kind in ('file', 'worktree', 'install', 'area'):
        expanded = expand(key)
        if not expanded.is_absolute():
            raise ValueError(f'a {kind} claim needs an absolute path')
        target = expanded.resolve()
        if kind != 'area' or (len(target.parts) > 1 and target.parts[1] == SHARED):
            return str(target)
        directory = next((parent for parent in (target, *target.parents) if parent.is_dir()), None)
        checkout = worktreerules.checkouts(directory) if directory else None
        if not checkout:
            raise ValueError('an area claim needs a Git checkout')
        top, common = checkout
        return str(shared_root(common) / target.relative_to(top))
    return key

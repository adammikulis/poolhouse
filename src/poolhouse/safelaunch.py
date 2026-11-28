"""Starting a program without taking anything from the directory this process happens to stand in, which may be a
project somebody else wrote: `empty_directory` is where an interpreter or installer is started from, so a module or a
settings file planted beside the work is never loaded, and `which` finds a program only in the absolute entries of
``PATH`` that are not inside that directory.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

from poolhouse import home

__all__ = ["absolute", "empty_directory", "which"]


def absolute(program: str | Path) -> str:
    """``program`` as it is named from here, so that it still names the same file from another directory. A bare name,
    which is looked up on ``PATH``, is left as it is."""
    text = str(program)
    bare = not any(separator and separator in text for separator in (os.sep, os.altsep))
    return text if bare else str(Path(text).absolute())


@contextlib.contextmanager
def empty_directory() -> Iterator[str]:
    """A new directory only this account can read, holding nothing, for one launch to stand in."""
    with tempfile.TemporaryDirectory(prefix="poolhouse-launch-", ignore_cleanup_errors=True) as made:
        yield made


def which(name: str, path: str | None = None) -> str | None:
    """``name`` in the absolute entries of ``path`` (``PATH`` when None), never in the current directory or, unless that
    is the account's home or a filesystem root, anywhere below it. None when no such entry has it."""
    try:
        here = Path.cwd().resolve()
    except OSError:
        here = None
    wide = here is None or here == Path(here.anchor) or here == home.user_home().resolve()
    for entry in map(Path, (os.environ.get("PATH", os.defpath) if path is None else path).split(os.pathsep)):
        if not entry.is_absolute():
            continue
        if here is not None:
            folder = entry.resolve()
            if folder == here or (not wide and folder.is_relative_to(here)):
                continue
        if found := shutil.which(str(entry / name)):
            return found
    return None

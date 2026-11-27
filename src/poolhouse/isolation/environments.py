"""The environments a launch uses: the project's own folders, or the main checkout's for a git worktree that has none.

A worktree holds what git holds, so an ignored environment folder is only in the checkout it was made from. That checkout is used only
when git's own records show the link both ways (`main_checkout`): the project's `.git` file names a record folder inside the checkout's
`.git/worktrees`, and that record names this `.git` file back and that `.git` as its common directory. Nothing else a project holds chooses
a folder, a link of its own among them. What is borrowed is read and run exactly as a project's own environment is: never written, never
published, and refused when the credential walk of a read grant finds a credential in it.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Collection
from pathlib import Path

from poolhouse.isolation import places

__all__ = ["ENVIRONMENT_LINKS", "folders", "main_checkout", "note", "sources"]

PYTHON = (".venv", "venv")
NODE = ("node_modules",)
KINDS = ((PYTHON, ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"), "Python environment (.venv or venv)"),
         (NODE, ("package.json",), "node_modules"))
"""Each kind of environment: its folder names, the files by which a project asks for one, and what the person calls it."""
ENVIRONMENT_LINKS = (*PYTHON, *NODE)
"""The ignored folders a project needs that a stage does not hold: each is linked into the stage, and read and run by the session, when it is a
real directory inside the checkout that holds it (`folders`) that the credential walk of every read grant (`secretpaths.tree_hit`) passes."""
RECORD_BYTES = 4096
"""The most a git pointer file holds: one path."""


def _own_directory(path: Path) -> bool:
    """Whether ``path`` is a directory itself (not a link) that this account owns and that not every user can write."""
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & stat.S_IWOTH


def _record(path: Path) -> str:
    """The one line of a git pointer file, read from a regular file of this account opened without following a link; empty for anything else."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & stat.S_IWOTH or info.st_nlink != 1:
            return ""
        text = os.fsdecode(os.read(descriptor, RECORD_BYTES + 1)).rstrip("\r\n")
    finally:
        os.close(descriptor)
    return "" if len(text) > RECORD_BYTES or "\n" in text or "\0" in text else text


def _named(base: Path, text: str) -> Path | None:
    """The path ``text`` names from ``base``, only when it is non-empty and no part of it is a link."""
    if not text:
        return None
    path = os.path.normpath(base / text)
    return Path(path) if os.path.realpath(path) == path else None


def _same(first: Path | None, second: Path) -> bool:
    try:
        return first is not None and first.samefile(second)
    except OSError:
        return False


def main_checkout(project: Path) -> Path | None:
    """The main checkout of the repository that ``project`` (a resolved folder) is a linked worktree of, when git's records show it both ways;
    None for anything else: a plain folder, a main checkout, a bare repository, a record that is missing, foreign, reached through a link or
    that names another folder, a "checkout" inside the project itself, and one no session is given (`places.project_refusal`)."""
    if os.name != "posix":
        return None
    marker = project / ".git"
    pointer = _record(marker)
    records = _named(project, pointer.removeprefix("gitdir: ")) if pointer.startswith("gitdir: ") else None
    if records is None or records.parent.name != "worktrees" or records.parent.parent.name != ".git":
        return None
    common = records.parent.parent
    main = common.parent
    if main == project or project in main.parents or places.project_refusal(str(main)):
        return None
    if not all(_own_directory(each) for each in (main, common, records.parent, records)):
        return None
    if not _same(_named(records, _record(records / "gitdir")), marker):
        return None
    return main if _same(_named(records, _record(records / "commondir")), common) else None


def folders(project: Path) -> tuple[tuple[str, Path], ...]:
    """Every environment folder a launch in ``project`` (a resolved folder) may use, as its name and the checkout that holds it. A kind the
    project has a real directory of is its own; a kind it has none of comes from the main checkout of its worktree, where that has one."""
    found: list[tuple[str, Path]] = []
    main: Path | None = None
    for names, _, _ in KINDS:
        own = [name for name in names if (project / name).is_dir() and not (project / name).is_symlink()]
        if own:
            found += [(name, project) for name in own]
        elif (main := main or main_checkout(project)) is not None:
            found += [(name, main) for name in names if _own_directory(main / name)]
    return tuple(found)


def sources(stage: Path, linked: Collection[str]) -> tuple[str, ...]:
    """Where Python of the box imports the project from, ahead of anything installed in a linked environment: the working copy's `src`, when it
    has one, and the working copy itself. An installed or editable copy in the environment is of the person's checkout, not of this working
    copy. Empty when no Python environment is linked."""
    if not set(PYTHON) & set(linked):
        return ()
    src = stage / "src"
    return (*((str(src),) if src.is_dir() and not src.is_symlink() else ()), str(stage))


def note(project: Path, linked: Collection[str]) -> str:
    """One sentence for a project that asks for an environment the sandbox has none of; empty otherwise."""
    lacking = [words for names, asks, words in KINDS if not set(names) & set(linked) and any((project / each).is_file() for each in asks)]
    if not lacking:
        return ""
    return (f"This working copy has no {' and no '.join(lacking)} the sandbox can use, and nothing can be installed in the sandbox (it has no "
            "network), so a test or command that needs those packages cannot run there.")

"""Which paths are closed to an agent (the guards, the hook settings, git's hooks and config, the authority registry, the person folder and
the session tokens), where tracked guard code is open to one (`editable`: a linked worktree on a branch of its own that runs no guard) and
which paths of a pull request are guard code (`reviewed`). Standard library only: the hook scripts load it by file."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable, Mapping
from pathlib import Path

__all__ = ["above_state", "checkout", "editable", "file_guarded", "guarded", "holds_secret", "holds_state", "live", "reviewed", "secret"]

CODE_PATHS = (
    r"src/poolhouse/workspace/harness_ancestry\.py",
    r"scripts/hooks/(claude-harness-bind|harness-ancestry)",
    r"scripts/(release-key|github-protection-apply|github-protection|dev-branch-rule|agent-identity)", r"src/poolhouse/credentials/.*")
"""The tracked guard code that is closed wherever it runs and open as branch code (`editable`)."""
INSTALLED = r"site-packages/poolhouse/(credentials/.*|workspace/harness_ancestry\.py|(guardpaths|guardgit|protectedwrites|worktreerules)\.py)"
"""The guards of an installed build."""
GUARD_PATHS = (
    *CODE_PATHS, INSTALLED,
    r"\.git/(refs/remotes/.*|packed-refs)",
    r"\.git/(hooks(/.*)?|config(\.worktree)?|worktrees/[^/]+/config\.worktree)",
    r"\.gitconfig", r"\.config/git/(config|hooks(/.*)?)", r"\.poolhouse/(authority\.json|authority-audit\.jsonl)")
GUARDS = re.compile(r"(^|/)(" + "|".join(GUARD_PATHS) + ")$", re.I)
REGISTRY = re.compile(r"(^|/)" + GUARD_PATHS[-1] + "$", re.I)
SECRETS = re.compile(r"(^|/)\.poolhouse/(person|node/client|node/person)(/|$)", re.I)
CODE = re.compile("|".join(CODE_PATHS), re.I)
"""A path from the top of a checkout that names tracked guard code."""
REVIEWED = re.compile("|".join((
    *CODE_PATHS, r"scripts/hooks/.*", r"scripts/(lead-review|install-hooks(\.py|\.sh)?)",
    r"src/poolhouse/(guardpaths|guardgit|protectedwrites|worktreerules|harnesshook|harnesspolicy|caller|authority|real_machine|secretpaths"
    r"|devbranch|session_identity)\.py",
    r"src/poolhouse/guard/.*", r"src/poolhouse/workspace/(review_depth|release_check|landing)\.py", r"app/poolhouse-node/src/landing\.rs",
    r"\.claude/settings[^/]*\.json", r"\.codex/(config\.toml|hooks\.json)")), re.I)
"""A path from the top of the repository that holds guard code, for the review of a pull request that changes it."""
HELD = tuple(tuple(path.split("/")) for path in (
    "src/poolhouse/workspace/harness_ancestry.py", "scripts/hooks/harness-ancestry",
    "scripts/hooks/claude-harness-bind", "scripts/release-key", "src/poolhouse/credentials/__init__.py",
    ".git/hooks/pre-push", ".git/config", ".config/git/config", ".gitconfig"))
DEVELOPMENT = re.compile(r"\d+(\.\d+)*dev", re.I)
"""The name of a development branch."""
PUBLISHED = ("main", "0.3dev")
"""Branches of `origin` whose guard code has been reviewed, besides the one named by `published`."""
POINTER_BYTES = 4096


def reviewed(path: str) -> bool:
    """Whether ``path``, from the top of the repository, is guard code."""
    return bool(REVIEWED.fullmatch(path))


def _line(path: Path) -> str:
    with path.open("rb") as stream:
        return stream.read(POINTER_BYTES).decode("utf-8").strip()


def same(one: str | Path, other: str | Path) -> bool:
    """Whether two paths are one file or folder, whatever they are spelled."""
    try:
        return Path(one).samefile(other)
    except OSError:
        return False


def checkout(path: str | Path) -> tuple[str, str, str] | None:
    """(top, git folder, common git folder) of the nearest checkout that holds ``path``, links resolved, read from the files. None outside one,
    and for a `.git` file that does not lead to a linked worktree which points back at it."""
    here = Path(os.path.realpath(path))
    for top in (here, *here.parents):
        marker = top / ".git"
        if marker.is_dir():
            return str(top), str(marker), str(marker)
        if marker.is_file():
            try:
                pointer = _line(marker)
                admin = Path(os.path.realpath(top / pointer.removeprefix("gitdir: ")))
                linked = pointer.startswith("gitdir: ") and admin.parent.name == "worktrees" and admin.parent.parent.name == ".git"
                if not linked or not same(admin / _line(admin / "gitdir"), marker):
                    return None
            except (OSError, ValueError):
                return None
            return str(top), str(admin), str(admin.parent.parent)
    return None


def branch(gitdir: str) -> str:
    """The branch the checkout with the git folder ``gitdir`` is on, empty when detached or unreadable."""
    try:
        head = _line(Path(gitdir) / "HEAD")
    except (OSError, ValueError):
        return ""
    return head.removeprefix("ref: refs/heads/") if head.startswith("ref: refs/heads/") else ""


def published(common: str) -> list[str]:
    """The branch names of `origin` whose content has been reviewed: `PUBLISHED`, `POOLHOUSE_DEV_BRANCH` and the primary checkout's branch
    when it is named as a development branch."""
    primary = branch(common)
    named = [os.environ.get("POOLHOUSE_DEV_BRANCH", "").strip(), primary if DEVELOPMENT.fullmatch(primary) else ""]
    return [*PUBLISHED, *filter(None, named)]


def _top(path: str | Path) -> str:
    """The nearest folder at or above ``path`` that holds a `.git`, links resolved, else an empty string."""
    here = Path(os.path.realpath(path))
    return next((str(top) for top in (here, *here.parents) if os.path.lexists(top / ".git")), "")


def live_trees(common: str) -> list[str]:
    """The checkouts whose files run as the guards: the primary, the one this module was loaded from and the harness's project folder."""
    found = [str(Path(common).parent), _top(__file__)]
    if named := os.environ.get("CLAUDE_PROJECT_DIR"):
        found.append(_top(named) or named)
    return [tree for tree in found if tree]


def live(top: str, common: str) -> bool:
    """Whether the files of the checkout at ``top`` run as the guards."""
    return any(same(top, tree) for tree in live_trees(common))


def editable(path: str) -> bool:
    """Whether ``path``, links resolved, is branch code: tracked guard code in a linked worktree that runs no guard, on a branch other than
    main, a development branch and the primary checkout's, and no other name of a file that does run (a hard link, a link from a tree that
    runs)."""
    real = os.path.realpath(path)
    found = checkout(real)
    if found is None:
        return False
    top, gitdir, common = found
    name = os.path.relpath(real, top).replace(os.sep, "/")
    if gitdir == common or not CODE.fullmatch(name):
        return False
    on = branch(gitdir).casefold()
    closed = {one.casefold() for one in ("main", "master", branch(common), *published(common))}
    if not on or on in closed or DEVELOPMENT.fullmatch(on):
        return False
    if live(top, common) or any(same(Path(tree) / name, real) for tree in live_trees(common)):
        return False
    try:
        info = os.lstat(real)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return not (stat.S_ISREG(info.st_mode) and info.st_nlink > 1)


def roots(environ: Mapping[str, str]) -> list[str]:
    """The case-folded active state root, as a list of at most one."""
    named = environ.get("POOLHOUSE_HOME")
    return [os.path.normpath(named).casefold()] if named else []


def secret(path: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether ``path`` lies in the person folder or the session tokens."""
    if SECRETS.search(path):
        return True
    places, low = ("/person", "/node/client", "/node/person"), path.casefold()
    return any(low == root + p or low.startswith(root + p + "/") for root in roots(environ or os.environ) for p in places)


def _holds_a_guard(path: str, branches: bool) -> bool:
    """Whether ``path`` is a folder that holds one of the guard files, as the repository root or as `scripts/hooks`. With ``branches``,
    branch code under a folder inside a worktree does not count; the top of a checkout holds what it holds."""
    folder = path.rstrip("/")
    low = folder.casefold()
    inside_a_tree = branches and not os.path.lexists(f"{folder}/.git")
    for held in HELD:
        for i in range(len(held)):
            head = "/".join(held[:i])
            inside = not head or low == head or low.endswith("/" + head)
            file = f"{folder}/{'/'.join(held[i:])}"
            if inside and os.path.lexists(file) and not (inside_a_tree and editable(file)):
                return True
    return False


def file_guarded(path: str, environ: Mapping[str, str] | None = None, owns: Callable[[str], bool] | None = None,
                 branches: bool = False) -> bool:
    """Whether ``path`` is itself protected: the authority registry, or (where ``owns`` reaches) a guard, a hook setting, git's hooks or
    config. With ``branches``, branch code (`editable`) is not."""
    if REGISTRY.search(path) or any(path.casefold() in (f"{root}/authority.json", f"{root}/authority-audit.jsonl")
                                    for root in roots(environ or os.environ)):
        return True
    return bool(GUARDS.search(path)) and (owns is None or owns(path)) and not (branches and editable(path))


def guarded(path: str, environ: Mapping[str, str] | None = None, owns: Callable[[str], bool] | None = None, branches: bool = False) -> bool:
    """Whether a write to ``path`` (a file, or a folder holding one) is refused to an agent: the guards, settings, git's hooks."""
    return file_guarded(path, environ, owns, branches) or (_holds_a_guard(path, branches) and (owns is None or owns(path)))


def _state_places(found: Mapping[str, str]) -> tuple[str, list[str]]:
    """The case-folded home folder and the state folders (the active root, and the one under the home folder)."""
    home = os.path.normpath(found["HOME"]).casefold() if found.get("HOME") else ""
    return home, [*roots(found), *([f"{home}/.poolhouse"] if home else [])]


def holds_state(path: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether ``path`` is the state folder that holds the person folder, or the folder of the session tokens' node."""
    folder = path.rstrip("/").casefold()
    _, found = _state_places(environ or os.environ)
    return any(root == folder or f"{root}/node" == folder for root in found)


def above_state(path: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether ``path`` is the home folder, a folder above it, or a state folder: a tree the person folder sits in."""
    folder = path.rstrip("/").casefold()
    home, found = _state_places(environ or os.environ)
    return any(own == folder or own.startswith(folder + "/") for own in [home, *found] if own)


def holds_secret(path: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether ``path`` is the person folder, a folder it sits in directly, or an ancestor of the home folder."""
    return holds_state(path, environ) or above_state(path, environ)

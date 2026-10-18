"""Git queries and worktree helpers for the lander and the push check: ``git`` runs in the checkout, ``sealed`` reads commits and trees as stored."""

from __future__ import annotations

import atexit
import functools
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

PROTECTED = ("main", "master")
REDIRECTS = re.compile(
    r"^GIT_(DIR|WORK_TREE|COMMON_DIR|INDEX_FILE|OBJECT_DIRECTORY|ALTERNATE_OBJECT_DIRECTORIES|NAMESPACE|PREFIX|REPLACE_REF_BASE"
    r"|SHALLOW_FILE|CONFIG_COUNT|CONFIG_KEY_\d+|CONFIG_VALUE_\d+|CONFIG_PARAMETERS)$")
STORED = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull}
HEX = re.compile(r"[0-9a-f]{40}")
OBJECT_COMMANDS = frozenset({"cat-file", "cherry", "diff", "diff-tree", "hash-object", "log", "ls-tree", "merge-base", "merge-tree",
                             "rev-list", "show"})
SEALED_KEEP = ("PATH", "SYSTEMROOT")
SEALED_CONFIG = "[core]\n" + "".join(f"\t{key} = {value}\n" for key, value in {
    "bare": "true", "attributesFile": os.devnull, "excludesFile": os.devnull, "hooksPath": os.devnull, "commitGraph": "false",
    "useReplaceRefs": "false", "fsmonitor": "false"}.items())
_stores: dict[str, Path] = {}


@dataclass(frozen=True)
class Tree:
    """A worktree: its path, the branch checked out there (empty when detached) and its head."""

    path: Path
    branch: str
    head: str


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment of a git command: this process's, without the variables that move the repository or add configuration,
    with ``extra`` and with replace refs and the grafts file off."""
    return {**{k: v for k, v in os.environ.items() if not REDIRECTS.match(k)}, **(extra or {}), **STORED}


def git(root: Path | str, *args: str, check: bool = True, env: dict[str, str] | None = None,
        stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``git -C root args`` in the checkout, with its configuration, and return the completed process.

    ``env`` replaces the environment of :func:`clean_env`. A command that reads commits or trees is refused: it goes through :func:`sealed`.
    """
    if args and args[0] in OBJECT_COMMANDS:
        raise ValueError(f"git {args[0]} reads commits and trees: run it with sealed()")
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, errors="replace",
                          check=False, env=clean_env() if env is None else {**env, **STORED}, input=stdin)
    if check and done.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {(done.stderr or done.stdout).strip()}")
    return done


@functools.cache
def scratch() -> Path:
    """An empty bare repository, removed when the process ends, that :func:`sealed` commands run in."""
    base = Path(tempfile.mkdtemp(prefix="land-sealed-"))
    atexit.register(shutil.rmtree, base, ignore_errors=True)
    for name in ("home", "templates"):
        (base / name).mkdir()
    env = {**{k: os.environ[k] for k in SEALED_KEEP if k in os.environ}, "HOME": str(base / "home"), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TEMPLATE_DIR": str(base / "templates")}
    done = subprocess.run(["git", "init", "-q", "--bare", str(base / "git")], cwd=base, env=env, capture_output=True, text=True, check=False)
    if done.returncode:
        raise RuntimeError(f"git init: {done.stderr.strip()}")
    (base / "git" / "config").write_text(SEALED_CONFIG, encoding="utf-8")
    return base


def store(root: Path | str) -> Path:
    """The object directory of the repository at ``root``."""
    found = _stores.get(str(root))
    if found is None or not found.is_dir():
        found = _stores[str(root)] = common_dir(root) / "objects"
    return found


def sealed_env(objects: Path, write: bool) -> dict[str, str]:
    """The environment of a sealed command: the empty repository, the objects of the real one, and none of the user's or system's files."""
    base = scratch()
    own = str(base / "git" / "objects")
    env = {k: os.environ[k] for k in SEALED_KEEP if k in os.environ}
    env.update(HOME=str(base / "home"), GIT_DIR=str(base / "git"), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_ATTR_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", **STORED)
    if write:
        env["GIT_OBJECT_DIRECTORY"] = str(objects)
    else:
        env.update(GIT_OBJECT_DIRECTORY=own, GIT_ALTERNATE_OBJECT_DIRECTORIES=str(objects))
    return env


def sealed_command(root: Path | str, *args: str, write: bool = False) -> tuple[list[str], dict[str, str]]:
    """The argument list and environment that run ``git args`` on the objects of the repository at ``root``, in an empty bare repository.

    No configuration, attribute file, hook, ref, replace ref, graft or commit graph of the checkout, the user or the system reaches
    the command, so its answer is a function of the commits it is given. Name commits by id: no ref is visible. ``write`` lets it
    store objects in the checkout's object directory (a merge's tree); otherwise they go to the empty repository.
    """
    return ["git", *args], sealed_env(store(root), write)


def sealed_bytes(root: Path | str, *args: str, stdin: str | None = None, write: bool = False) -> subprocess.CompletedProcess[bytes]:
    """Run ``git args`` as :func:`sealed_command` says and return the raw process."""
    argv, env = sealed_command(root, *args, write=write)
    return subprocess.run(argv, cwd=scratch(), env=env, capture_output=True, check=False, input=None if stdin is None else stdin.encode())


def sealed(root: Path | str, *args: str, check: bool = True, stdin: str | None = None,
           write: bool = False) -> subprocess.CompletedProcess[str]:
    """Run ``git args`` as :func:`sealed_bytes` does and return the output as text; output that is not UTF-8 raises ``RuntimeError``."""
    done = sealed_bytes(root, *args, stdin=stdin, write=write)
    try:
        text = subprocess.CompletedProcess(done.args, done.returncode, done.stdout.decode("utf-8"), done.stderr.decode("utf-8", "replace"))
    except UnicodeDecodeError:
        raise RuntimeError(f"git {' '.join(args)}: the output is not UTF-8") from None
    if check and done.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {(text.stderr or text.stdout).strip()}")
    return text


def blob(root: Path | str, commit: str, name: str) -> bytes | None:
    """The bytes of the file ``name`` in the tree of the commit with id ``commit`` (no filter or text conversion), or None when it has none."""
    done = sealed_bytes(root, "cat-file", "blob", f"{commit}:{name}")
    return done.stdout if done.returncode == 0 else None


def out(root: Path | str, *args: str) -> str:
    """Stripped standard output of a git command that must succeed."""
    return git(root, *args).stdout.strip()


def empty_tree(root: Path | str) -> str:
    """The id of the tree with no files, in the hash of this repository."""
    return sealed(root, "hash-object", "-t", "tree", "--stdin", stdin="").stdout.strip()


def lines(root: Path | str, *args: str) -> list[str]:
    """Non-empty output lines of a git command that must succeed."""
    return [x for x in git(root, *args).stdout.splitlines() if x]


def merge_tree(root: Path | str, ours: str, theirs: str) -> tuple[str, list[str]]:
    """The tree of the automatic merge of two commits, or an empty string and the paths that conflict."""
    done = sealed(root, "merge-tree", "--write-tree", "--name-only", "--no-messages", ours, theirs, check=False, write=True)
    if done.returncode > 1:
        raise RuntimeError(f"git merge-tree: {(done.stderr or done.stdout).strip()}")
    tree, *names = done.stdout.splitlines()
    return ("", [n for n in names if n]) if done.returncode else (tree, [])


def toplevel(start: Path | str) -> Path:
    """The root of the worktree containing ``start``."""
    return Path(out(start, "rev-parse", "--show-toplevel"))


def common_dir(root: Path | str) -> Path:
    """The git directory every worktree of this repository shares."""
    return Path(out(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))


def trees(root: Path | str) -> list[Tree]:
    """Every registered worktree; the first is the primary checkout."""
    found: list[Tree] = []
    path, head, branch = "", "", ""
    for line in [*git(root, "worktree", "list", "--porcelain").stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            path = line[9:]
        elif line.startswith("HEAD "):
            head = line[5:]
        elif line.startswith("branch refs/heads/"):
            branch = line[18:]
        elif not line and path:
            found.append(Tree(Path(path), branch, head))
            path, head, branch = "", "", ""
    return found


def primary(root: Path | str) -> Tree:
    """The primary checkout."""
    return trees(root)[0]


def tree_of(root: Path | str, branch: str) -> Tree | None:
    """The worktree that has ``branch`` checked out, if any."""
    return next((t for t in trees(root) if t.branch == branch), None)


def dirty(path: Path | str) -> bool:
    """Whether the worktree at ``path`` has uncommitted or untracked files; a missing directory has none."""
    if not Path(path).is_dir():
        return False
    return bool(git(path, "status", "--porcelain").stdout.strip())


def commit_id(root: Path | str, sha: str) -> str:
    """The commit with id ``sha`` (a full id; a name or an abbreviation names none), or an empty string when the repository lacks it."""
    if not HEX.fullmatch(sha):
        return ""
    done = sealed(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}", check=False)
    return done.stdout.strip() if done.returncode == 0 else ""


def tree_id(root: Path | str, commit: str) -> str:
    """The id of the tree of the commit with id ``commit``."""
    return sealed(root, "rev-parse", f"{commit}^{{tree}}").stdout.strip()


def is_ancestor(root: Path | str, older: str, newer: str) -> bool:
    """Whether commit ``older`` is ``newer`` or reachable from it."""
    return sealed(root, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def changed(root: Path | str, base: str, tip: str, *flags: str) -> list[str]:
    """The paths that differ between the trees of commits ``base`` and ``tip``, every path in full."""
    done = sealed(root, "diff-tree", "-r", "--name-only", "--no-renames", "--ignore-submodules=none", "-z", *flags, base, tip)
    return [name for name in done.stdout.split("\0") if name]


def unpushed(root: Path | str, tip: str) -> str:
    """Revisions for ``--stdin`` that select the commits reachable from ``tip`` and from no remote-tracking ref."""
    held = git(root, "for-each-ref", "--format=%(objectname)", "refs/remotes").stdout.split()
    return "".join(f"{rev}\n" for rev in [tip, *(f"^{sha}" for sha in held)])


def conflicted(root: Path | str) -> list[str]:
    """The paths the index of the worktree at ``root`` holds unmerged."""
    rows = git(root, "ls-files", "-z", "--unmerged").stdout.split("\0")
    return sorted({row.split("\t", 1)[1] for row in rows if "\t" in row})


def refuse_protected(*names: str) -> None:
    """Raise when a branch named here is one landing never touches."""
    for name in names:
        if name in PROTECTED:
            raise ValueError(f"land never touches {name}")

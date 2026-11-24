"""Git commit primitives for runtime source admission."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path

from poolhouse.lock import Busy, held_by, only_one

COMMIT = re.compile(r"[0-9a-f]{40}")
BOUNDARY_WAIT_S = 120.0
BOUNDARY_WAIT_MAX_S = 900.0


class DeployError(RuntimeError):
    """A runtime source, build or selection step failed."""


def environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the environment for literal, local runtime Git operations."""
    env = {name: value for name, value in (os.environ if source is None else source).items()
           if not name.startswith(("GIT_", "DYLD_", "LD_"))}
    return {**env, "PATH": os.defpath, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def object_view(checkout: Path, directory: Path, *, branch: str = "runtime-source") -> None:
    """Create a private repository that reads only the checkout's object database."""
    directory.mkdir(parents=True, exist_ok=True)
    git(directory, "init", "--quiet", "--template=", f"--initial-branch={branch}")
    objects = Path(git(checkout, "rev-parse", "--path-format=absolute", "--git-path", "objects"))
    (directory / ".git/objects/info/alternates").write_text(str(objects) + "\n")


def git(checkout: Path, *words: str, check: bool = True) -> str:
    """Run git in a checkout and return stdout."""
    done = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(checkout), *words], capture_output=True, text=True, timeout=60, env=environment())
    if check and done.returncode:
        raise DeployError(f"git {words[0]} failed: {(done.stderr or done.stdout).strip()[:300]}")
    return done.stdout.strip() if done.returncode == 0 else ""



def common_dir(path: Path) -> Path:
    """The git directory shared by every worktree of the repository at `path`, read from files without running git."""
    link = path / ".git"
    if link.is_dir():
        return link.resolve()
    try:
        text = link.read_text(encoding="utf-8").strip() if link.is_file() else ""
    except (OSError, ValueError):
        text = ""
    if not text.startswith("gitdir:"):
        raise DeployError(f"{path} is not a git checkout")
    gitdir = Path(text.split(":", 1)[1].strip())
    gitdir = gitdir if gitdir.is_absolute() else path / gitdir
    pointer = gitdir / "commondir"
    if pointer.is_file():
        target = Path(pointer.read_text(encoding="utf-8").strip())
        gitdir = target if target.is_absolute() else gitdir / target
    return gitdir.resolve()


def main_worktree(path: Path) -> Path:
    """The primary checkout of the repository that holds `path`."""
    shared = common_dir(path)
    if shared.name != ".git":
        raise DeployError(f"{path} belongs to a repository with no primary checkout")
    return shared.parent


def primary(checkout: Path) -> Path:
    """Return the literal primary source checkout, refusing linked worktrees."""
    top = Path(git(checkout, "rev-parse", "--show-toplevel")).resolve()
    try:
        found = main_worktree(top)
    except DeployError:
        found = None
    if found != top:
        raise DeployError("runtime selection must use the primary source checkout")
    return top


def boundary(checkout: Path) -> Path:
    """Return the primary repository's runtime cutover lock."""
    return primary(checkout) / ".git" / "runtime-cutover.lock"


def holder(path: Path) -> str:
    """Who holds the lock file `path`, as its text records it (printable ASCII, at most 200 characters), or "nobody"."""
    return held_by(path)[:200] or "nobody"


@contextmanager
def crossing(checkout: Path, *, wait_s: float = 0.0, note: str = "") -> Iterator[Path]:
    """Hold the primary repository's runtime cutover boundary for the block, waiting up to `wait_s` seconds (at most
    `BOUNDARY_WAIT_MAX_S`); `Busy` names the holder."""
    path = boundary(checkout)
    wait_s = min(BOUNDARY_WAIT_MAX_S, max(0.0, wait_s))
    with ExitStack() as stack:
        try:
            stack.enter_context(only_one(path, wait=wait_s > 0, timeout=wait_s, note=note))
        except Busy:
            raise Busy(f"{path} is held by {holder(path)}"
                       + (f"; still held after waiting {wait_s:.0f}s" if wait_s > 0 else "")) from None
        yield path


def until_free(checkout: Path, wait_s: float) -> None:
    """Wait up to `wait_s` seconds (at most `BOUNDARY_WAIT_MAX_S`) for the boundary to be free and hold nothing afterwards; `Busy` names the holder."""
    with crossing(checkout, wait_s=wait_s, note="runtime build waiting"):
        pass

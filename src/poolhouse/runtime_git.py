"""Git commit primitives for runtime source admission."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path

import psutil

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



def primary(checkout: Path) -> Path:
    """Return the literal primary source checkout, refusing linked worktrees."""
    paths = git(checkout, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir").splitlines()
    if len(paths) != 2 or Path(paths[0]).resolve() != Path(paths[1]).resolve().parent or Path(paths[1]).name != ".git":
        raise DeployError("runtime selection must use the primary source checkout")
    return Path(paths[0]).resolve()


def boundary(checkout: Path) -> Path:
    """Return the primary repository's shared landing and runtime cutover lock."""
    return primary(checkout) / ".git" / "land" / "runner.lock"


def _printable(text: str, limit: int) -> str:
    """`text` cut to `limit` characters with every character outside printable ASCII replaced by `?`."""
    return re.sub(r"[^\x20-\x7e]", "?", text)[:limit]


def holder(path: Path) -> str:
    """Who holds the lock file `path`: its recorded text and the command line of that process, printable characters only, or "nobody"."""
    text = _printable(held_by(path), 200)
    found = re.match(r"pid (\d+)", text)
    command = ""
    if found:
        try:
            command = _printable(" ".join(psutil.Process(int(found.group(1))).cmdline()), 120)
        except psutil.Error:
            command = ""
    return (text + (f" ({command})" if command else "")) if text else "nobody"


@contextmanager
def crossing(checkout: Path, *, wait_s: float = 0.0, note: str = "") -> Iterator[Path]:
    """Hold the primary repository's landing and cutover boundary for the block, waiting up to `wait_s` seconds (at most
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

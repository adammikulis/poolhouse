"""Git commit primitives for runtime source admission."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

COMMIT = re.compile(r"[0-9a-f]{40}")


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

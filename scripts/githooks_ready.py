"""Put the repository's git hooks in place at the start of a test session."""

from __future__ import annotations

import subprocess
from pathlib import Path

HOOKS = ("pre-commit", "commit-msg", "pre-push")


def in_place(hooks: Path) -> bool:
    """Whether each hook is ours or a file the installer leaves alone (a local wrapper)."""
    for name in HOOKS:
        hook = hooks / name
        if hook.is_symlink():
            if "scripts/hooks/" not in str(hook.readlink()):
                return False
        elif not hook.exists():
            return False
    return True


def ensure(repo: Path) -> None:
    """Run the installer when a hook is missing; a repository without it, or a failed run, is left alone."""
    installer = repo / "scripts" / "install-hooks.sh"
    if not installer.is_file():
        return
    try:
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=repo,
                                capture_output=True, text=True, timeout=10)
        if common.returncode != 0 or in_place((repo / common.stdout.strip()).resolve() / "hooks"):
            return
        done = subprocess.run(["sh", str(installer)], cwd=repo, capture_output=True, text=True, timeout=30)
        if done.returncode == 0:
            print("conftest: git hooks installed")
        else:
            print(f"conftest: git hooks not installed: {done.stderr.strip()}")
    except (OSError, subprocess.SubprocessError):
        pass

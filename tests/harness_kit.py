"""Harness hook tests on a real node: sessions of one board, a project checkout and a worker's linked checkout, and the hook as a child process."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from git_template import copy_checkout

from poolhouse.net import git

HOOK = "poolhouse.harnesshook"
SRC = str(Path(__file__).resolve().parents[1] / "src")


def _repository(root: Path) -> None:
    primary = root / "repository"
    primary.mkdir()
    git.run(["init", "-b", "development", str(primary)])
    (primary / "source.py").write_text("value = 1\n")
    git.run(["add", "source.py"], cwd=primary)
    git.run(["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-m", "chore: fixture"], cwd=primary)
    git.run(["worktree", "add", "-b", "worker/change", str(root / "checkout")], cwd=primary)


def checkouts(tmp_path: Path) -> SimpleNamespace:
    """A primary checkout on ``development`` with one commit and a linked checkout on ``worker/change``."""
    copy_checkout("harness-kit", _repository, tmp_path, "repository", "checkout")
    return SimpleNamespace(primary=tmp_path / "repository", checkout=tmp_path / "checkout")


def commit(path: Path, name: str, text: str, message: str) -> str:
    """Write ``name`` in the checkout ``path`` with ``text``, commit it and return the commit."""
    (path / name).write_text(text)
    git.run(["add", name], cwd=path)
    git.run(["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-m", message], cwd=path)
    return git.run(["rev-parse", "HEAD"], cwd=path).stdout.strip()


def held(session, owner: str) -> list[tuple[str, str]]:
    """The claims ``owner`` holds on the board of ``session``, as (kind, key)."""
    return sorted((c.kind, c.key) for c in session.claims() if c.owner == owner)


def hook(node, event: str, payload: dict | str, *args: str, who=None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the hook of a harness: a child process on this node, the event on stdin."""
    return subprocess.run([sys.executable, "-m", HOOK, event, *args], input=payload if isinstance(payload, str) else json.dumps(payload),
                          capture_output=True, text=True, env=node.env(who, env), timeout=60, check=False)


def verdict(done: subprocess.CompletedProcess[str]) -> dict:
    """The decision a hook printed."""
    return json.loads(done.stdout)["hookSpecificOutput"]

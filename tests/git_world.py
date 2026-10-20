"""A tiny real git project with a bare origin, and a real node with an author, a pusher and a reviewer."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV = "devline"
AGENT_PUSH = {"CLAUDECODE": "1", "PYTHON": sys.executable, "POOLHOUSE_RUNTIME_ENSURE": "off"}


def git(cwd: Path, *args: str) -> str:
    """Run git in ``cwd`` and return its output."""
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


class Project:
    """A repository with a bare origin and sibling worktrees for branches."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.root = base / "proj"
        self.origin = base / "origin.git"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", DEV)
        git(self.root, "config", "user.name", "Test Agent")
        git(self.root, "config", "user.email", "agent@example.invalid")
        git(self.root, "config", "commit.gpgsign", "false")
        self.write(self.root, "src/poolhouse/mod.py", "VALUE = 1\n")
        self.write(self.root, "tests/test_mod.py", "import poolhouse.mod\n")
        self.write(self.root, "docs/a.md", "a\n")
        self.write(self.root, ".gitignore", "__pycache__/\n.env\n")
        self.commit(self.root, "chore: seed")
        git(base, "init", "-q", "--bare", str(self.origin))
        git(self.root, "remote", "add", "origin", str(self.origin))
        git(self.root, "push", "-q", "origin", DEV)

    @staticmethod
    def write(where: Path, rel: str, text: str) -> None:
        """Write ``text`` to ``rel`` under ``where``."""
        path = where / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def commit(where: Path, message: str) -> str:
        """Commit every change in ``where`` and return the new sha."""
        git(where, "add", "-A")
        git(where, "commit", "-q", "-m", message)
        return git(where, "rev-parse", "HEAD")

    def branch(self, name: str, files: dict[str, str], start: str = DEV) -> Path:
        """A worktree on a new branch cut from ``start`` holding one commit of ``files``."""
        where = self.base / name.replace("/", "-")
        git(self.root, "worktree", "add", "-q", "-b", name, str(where), start)
        for rel, text in files.items():
            self.write(where, rel, text)
        if files:
            self.commit(where, f"feat: {name}")
        return where

    def advance(self, rel: str, text: str) -> str:
        """Commit a change on the development branch in the primary checkout and push it, as another agent would."""
        self.write(self.root, rel, text)
        sha = self.commit(self.root, f"chore: {rel}")
        git(self.root, "push", "-q", "origin", DEV)
        return sha

    def origin_head(self) -> str:
        """The development branch as the bare origin holds it."""
        return git(self.origin, "rev-parse", DEV)

    def local_head(self) -> str:
        """The development branch of the primary checkout."""
        return git(self.root, "rev-parse", DEV)


class World:
    """A project, a real node, and four sessions: the author of a branch, a pusher, an independent reviewer and another."""

    def __init__(self, tmp_path: Path, node) -> None:
        self.node = node
        (tmp_path / "git").mkdir()
        self.proj = Project(tmp_path / "git")
        self.author = node.member("author")
        self.pusher = node.member("pusher")
        self.reviewer = node.member("reviewer", model="claude-opus-5-5")
        self.other = node.member("other")

    def start(self, name: str, files: dict[str, str]) -> tuple[Path, str]:
        """A branch of ``files``; the worktree and the tip."""
        tree = self.proj.branch(name, files)
        return tree, git(tree, "rev-parse", "HEAD")

    def env(self, who) -> dict[str, str]:
        """The environment of a command run for ``who``."""
        return self.node.env(who, {"POOLHOUSE_DEV_BRANCH": DEV})

    def install_hook(self) -> None:
        """Install the repository's pre-push hook; it checks a push made with the ``AGENT_PUSH`` environment."""
        hooks = Path(git(self.proj.root, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        hooks.mkdir(exist_ok=True)
        hook = hooks / "pre-push"
        hook.write_text("#!/bin/sh\nexec sh " + shlex.quote((ROOT / "scripts/hooks/pre-push").as_posix()) + ' "$@"\n', encoding="utf-8")
        hook.chmod(0o755)

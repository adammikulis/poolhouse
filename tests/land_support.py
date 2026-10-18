"""A tiny real git project with a bare origin and a fake test runner, and a real node with a creator, a lander and a reviewer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from poolhouse import lock as filelock

ROOT = Path(__file__).resolve().parents[1]
LAND = ROOT / "scripts" / "land"
DEV = "devline"

FAKE_TEST = r'''
import os, pathlib, sys, time
here = pathlib.Path('.').resolve()
base = pathlib.Path(__BASE__)
with open(base / 'calls.log', 'a') as f:
    f.write(' '.join(sys.argv[1:]) + '\n')
    for name in ('PYTEST_ADDOPTS', 'POOLHOUSE_LIVE_API', 'POOLHOUSE_GATE_TREE_ONLY', 'LAND_HOSTILE'):
        f.write(f'ENV {name}={os.environ.get(name, "")}\n')
bad = 0
if sys.argv[1] == 'gate':
    bad = int(bool(list(here.glob('BAD_GATE*'))))
for arg in sys.argv[2:]:
    path = pathlib.Path(arg)
    if not (arg.startswith('tests/') and path.exists()):
        continue
    text = path.read_text()
    if 'SLOW' in text:
        (base / 'started').write_text('1')
        for _ in range(600):
            if (base / 'proceed').exists():
                break
            time.sleep(0.1)
    if 'MUTATE' in text:
        with open(here / 'docs' / 'a.md', 'a') as f:
            f.write('changed while testing\n')
    if 'FAILME' in text:
        print(f'FAILED {arg}::test_x - boom')
        bad = 1
sys.exit(bad)
'''


def git(cwd: Path, *args: str) -> str:
    """Run git in ``cwd`` and return its output."""
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


class Project:
    """A repository with a bare origin, a fake test runner and sibling worktrees for branches."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.root = base / "proj"
        self.origin = base / "origin.git"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", DEV)
        git(self.root, "config", "user.name", "Test Agent")
        git(self.root, "config", "user.email", "agent@example.invalid")
        git(self.root, "config", "commit.gpgsign", "false")
        self.write(self.root, "scripts/test", FAKE_TEST.replace("__BASE__", repr(str(base))))
        self.write(self.root, "scripts/budgets", "import sys\nsys.exit(0)\n")
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
        """Commit a change on the development branch in the primary checkout and push it, as another lander would."""
        self.write(self.root, rel, text)
        sha = self.commit(self.root, f"chore: {rel}")
        git(self.root, "push", "-q", "origin", DEV)
        return sha

    def calls(self) -> list[str]:
        """The lines the fake test runner logged: its arguments and the environment it saw."""
        log = self.base / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def origin_head(self) -> str:
        """The development branch as the bare origin holds it."""
        return git(self.origin, "rev-parse", DEV)

    def local_head(self) -> str:
        """The development branch of the primary checkout."""
        return git(self.root, "rev-parse", DEV)


class World:
    """A project, a real node, and three sessions: the creator of a branch, a lander and an independent reviewer."""

    def __init__(self, tmp_path: Path, node) -> None:
        self.node = node
        (tmp_path / "git").mkdir()
        self.proj = Project(tmp_path / "git")
        self.creator = node.member("creator")
        self.lander = node.member("lander")
        self.reviewer = node.member("reviewer", model="claude-opus-5-5")
        self.other = node.member("other")

    def start(self, name: str, files: dict[str, str], claim: bool = True) -> tuple[Path, str]:
        """A branch of ``files`` whose creator claims the branch and its worktree; the worktree and the tip."""
        tree = self.proj.branch(name, files)
        if claim:
            mine = self.node.session(self.creator)
            mine.claim("branch", name)
            mine.claim("worktree", str(tree))
        return tree, git(tree, "rev-parse", "HEAD")

    def env(self, who, hostile: dict[str, str] | None = None) -> dict[str, str]:
        """The environment of a lander run for ``who``."""
        return self.node.env(who, {"POOLHOUSE_DEV_BRANCH": DEV, **(hostile or {})})

    def land(self, tree: Path, *args: str, who=None, hostile: dict[str, str] | None = None) -> tuple[int, str]:
        """Run ``scripts/land`` from ``tree`` as ``who`` (the lander by default); the exit status and the output."""
        done = subprocess.run([sys.executable, str(LAND), *args], cwd=tree, env=self.env(who or self.lander, hostile),
                              capture_output=True, text=True, check=False, timeout=300)
        return done.returncode, done.stdout + done.stderr

    def start_land(self, tree: Path, *args: str, who=None) -> subprocess.Popen:
        """Start ``scripts/land`` from ``tree`` and return the running process."""
        return subprocess.Popen([sys.executable, str(LAND), *args], cwd=tree, env=self.env(who or self.lander),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def accept(self, branch: str, sha: str, verdict: str = "accept", who=None) -> None:
        """An independent review of ``sha`` under ``branch``."""
        from poolhouse.workspace import landing

        landing.review(self.node.session(who or self.reviewer), branch, sha, verdict)

    def records(self) -> list[dict]:
        """The lines of the landings log."""
        log = Path(git(self.proj.root, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "land" / "landings.jsonl"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


class RunnerLock:
    """An exclusive lock on the landing lock file of a checkout, held by the process until released."""

    def __init__(self, root: Path) -> None:
        folder = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "land"
        folder.mkdir(exist_ok=True)
        self.path = folder / "runner.lock"
        self.fd: int | None = None

    def acquire(self) -> bool:
        """Take the lock; false when another process holds it."""
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        if not filelock.take(fd):
            os.close(fd)
            return False
        os.write(fd, f"pid {os.getpid()}".encode())
        self.fd = fd
        return True

    def release(self) -> None:
        """Give the lock up."""
        if self.fd is not None:
            os.ftruncate(self.fd, 0)
            filelock.release(self.fd)
            os.close(self.fd)
            self.fd = None

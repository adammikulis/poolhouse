"""A tiny real git project with a bare origin and a fake test runner, and a real node with a creator, a lander and a reviewer."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import land_git

from poolhouse import lock as filelock

ROOT = Path(__file__).resolve().parents[1]
DEV = "devline"
AGENT_PUSH = {"CLAUDECODE": "1", "PYTHON": sys.executable, "POOLHOUSE_RUNTIME_ENSURE": "off"}

FAKE_TEST = r'''
import os, pathlib, sys, time
here = pathlib.Path('.').resolve()
base = pathlib.Path(__BASE__)
with open(base / 'calls.log', 'a') as f:
    f.write(' '.join(sys.argv[1:]) + '\n')
    for name in ('PYTEST_ADDOPTS', 'POOLHOUSE_LIVE_API', 'LAND_HOSTILE'):
        f.write(f'ENV {name}={os.environ.get(name, "")}\n')
import subprocess
head = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
with open(base / 'tested.log', 'a') as f:
    f.write(f'{sys.argv[1]} {head}\n')
flag = base / 'fail_once'
if flag.exists():
    flag.unlink()
    print('FAILED tests/test_once.py::test_x - boom')
    sys.exit(1)
bad = 0
if sys.argv[1] == 'gate':
    bad = int(bool(list(here.glob('BAD_GATE*'))))
for arg in sys.argv[2:]:
    path = pathlib.Path(arg)
    if not (arg.startswith('tests/') and path.exists()):
        continue
    text = path.read_text()
    if 'poolhouse.mod' in text and 'BROKEN' in (here / 'src' / 'poolhouse' / 'mod.py').read_text():
        print(f'FAILED {arg}::test_x - mod is broken')
        bad = 1
    if 'MOVER' in text:
        mover = base / 'mover'
        for step in (['pull', '-q', '--ff-only', 'origin', __DEV__], ['add', 'src/poolhouse/mod.py'], ['commit', '-q', '-m', 'chore: mover'],
                     ['push', '-q', 'origin', __DEV__]):
            if step[0] == 'add':
                (mover / 'src' / 'poolhouse' / 'mod.py').write_text(f'VALUE = {time.time_ns()}\n')
            subprocess.run(['git', '-C', str(mover), *step], check=True, capture_output=True)
    if 'SLOW' in text:
        (base / 'started').write_text('1')
        with open(base / 'slow.log', 'a') as f:
            f.write(arg + '\n')
        for _ in range(600):
            if (base / 'proceed').exists():
                break
            time.sleep(0.1)
    if 'ORPHAN' in text:
        import subprocess
        child = subprocess.Popen(['sleep', '120'])
        (base / 'orphan.pid').write_text(str(child.pid))
    if 'MUTATE' in text:
        with open(here / 'docs' / 'a.md', 'a') as f:
            f.write('changed while testing\n')
    if 'INTERRUPTME' in text:
        print(f'test: interrupted during {arg}::test_y')
        sys.exit(130)
    if 'FAILME' in text:
        print(f'FAILED {arg}::test_x - boom')
        bad = 1
sys.exit(bad)
'''


def lander_files() -> set[str]:
    """The lander's scripts/ files in this checkout."""
    def read(name: str) -> bytes | None:
        path = ROOT / name
        return path.read_bytes() if path.is_file() else None

    return land_git.lander_set({f"scripts/{p.name}" for p in (ROOT / "scripts").iterdir() if p.is_file()}, read)


def entry(tree: Path) -> Path:
    """The scripts/land an agent in the worktree ``tree`` runs: the branch's own copy."""
    return tree / "scripts" / "land"


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
        self.write(self.root, "scripts/test", FAKE_TEST.replace("__BASE__", repr(str(base))).replace("__DEV__", repr(DEV)))
        self.write(self.root, "scripts/budgets", "import sys\nsys.exit(0)\n")
        for name in sorted(lander_files()):
            shutil.copy2(ROOT / name, self.root / name)
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

    def tested(self, kind: str = "all") -> list[str]:
        """The commits the fake test runner ran ``kind`` on, in order."""
        log = self.base / "tested.log"
        rows = [x.split() for x in log.read_text().splitlines()] if log.exists() else []
        return [sha for what, sha in rows if what == kind]

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
        done = subprocess.run([sys.executable, str(entry(tree)), *args], cwd=tree, env=self.env(who or self.lander, hostile),
                              capture_output=True, text=True, check=False, timeout=300)
        return done.returncode, done.stdout + done.stderr

    def start_land(self, tree: Path, *args: str, who=None, hostile: dict[str, str] | None = None) -> subprocess.Popen:
        """Start ``scripts/land`` from ``tree`` and return the running process."""
        return subprocess.Popen([sys.executable, str(entry(tree)), *args], cwd=tree, env=self.env(who or self.lander, hostile),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def accept(self, branch: str, sha: str, verdict: str = "accept", who=None) -> None:
        """An independent review of ``sha`` under ``branch``."""
        from poolhouse.workspace import landing

        landing.review(self.node.session(who or self.reviewer), branch, sha, verdict)

    def hold_pushes(self) -> None:
        """A post-receive hook on origin that marks each push as ``pushed`` and holds it until ``release`` exists."""
        base = shlex.quote(str(self.proj.base))
        hook = self.proj.origin / "hooks" / "post-receive"
        hook.write_text(f"#!/bin/sh\ntouch {base}/pushed\nwhile [ ! -f {base}/release ]; do sleep 0.1; done\n", encoding="utf-8")
        hook.chmod(0o755)

    def mover(self) -> None:
        """A clone of origin that a test file marked MOVER pushes a change to ``src/poolhouse/mod.py`` from, each time it runs."""
        clone = self.proj.base / "mover"
        git(self.proj.base, "clone", "-q", "-b", DEV, str(self.proj.origin), str(clone))
        git(clone, "config", "user.name", "Mover")
        git(clone, "config", "user.email", "mover@example.invalid")
        git(clone, "config", "commit.gpgsign", "false")

    def install_hook(self) -> None:
        """Install the repository's pre-push hook; it checks a push made with the ``AGENT_PUSH`` environment."""
        hooks = Path(git(self.proj.root, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        hooks.mkdir(exist_ok=True)
        hook = hooks / "pre-push"
        hook.write_text("#!/bin/sh\nexec sh " + shlex.quote((ROOT / "scripts/hooks/pre-push").as_posix()) + ' "$@"\n', encoding="utf-8")
        hook.chmod(0o755)

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

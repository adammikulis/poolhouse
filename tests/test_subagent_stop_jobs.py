"""A stopped subagent leaves no test job running or queued."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from poolhouse.activity import reuse
from poolhouse.board import session as board_session
from poolhouse.workspace import testruns

pytest_plugins = ["node_kit"]
ROOT = Path(__file__).resolve().parents[1]
GIT = ["git", "-c", "user.name=probe", "-c", "user.email=probe@example.invalid"]
SLEEPER = "import time\ntime.sleep(120)\n"
AGENT = "abcdef12345"


def git(*arguments: str, cwd: Path) -> None:
    subprocess.run([*GIT, *arguments], cwd=cwd, check=True, capture_output=True)


class Scene:
    """A project with a linked worktree, a job table, a real node that names the subagent, and the real stop hook."""

    def __init__(self, tmp_path: Path, node, monkeypatch) -> None:
        binary = tmp_path / "bin"
        binary.mkdir()
        (binary / "poolhouse-workspace").write_text(f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
if sys.argv[1:3] == ['claims', '--json']:
    print('[]')
    raise SystemExit(0)
path = Path(os.environ['HOOK_CALLS'])
calls = json.loads(path.read_text()) if path.exists() else []
calls.append(sys.argv[1:])
path.write_text(json.dumps(calls))
""")
        (binary / "poolhouse-workspace").chmod(0o700)
        (binary / "git").symlink_to(shutil.which("git"))
        self.calls = tmp_path / "calls.json"
        tree = tmp_path / "hooktree"
        shutil.copytree(ROOT / "scripts", tree / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        (tree / "src").symlink_to(ROOT / "src")
        self.hook = tree / "scripts/hooks/claude-subagent-stop"
        self.primary = tmp_path / "proj"
        self.primary.mkdir()
        (self.primary / "file").write_text("x\n")
        git("init", "-q", "-b", "dev", cwd=self.primary)
        git("add", "file", cwd=self.primary)
        git("commit", "-q", "-m", "one", cwd=self.primary)
        self.worktree = tmp_path / "wt"
        git("worktree", "add", "-q", "-b", "work", str(self.worktree), cwd=self.primary)
        monkeypatch.setenv("DEV_TEST_REUSE_DIR", str(tmp_path / "reuse"))
        monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{Path(sys.executable).parent}")
        monkeypatch.setenv("HOOK_CALLS", str(self.calls))
        monkeypatch.setenv("POOLHOUSE_HOOK_STATE", str(tmp_path / "hookstate"))
        sys.path.insert(0, str(ROOT / "scripts"))
        import testjobs
        self.table = testjobs.Jobs(reuse.reuse_base() / testruns.scope(self.primary))
        self.runner = tmp_path / "runner.py"
        self.runner.write_text(SLEEPER)
        node.member(AGENT, model="claude-haiku-4-5")
        self.name = board_session.find("claude-code", AGENT, client=node.client)

    def job(self, owner: str, root: Path) -> str:
        return self.table.submit(["all", "tests/x.py"], {"id": owner}, root, self.runner, {"exclusive": False})

    def state(self, job: str) -> str:
        return self.table.read(job, "status.json").get("state", "")

    def stop(self, cwd: Path) -> subprocess.CompletedProcess:
        event = {"agent_type": "general-purpose", "agent_id": AGENT, "cwd": str(cwd)}
        return subprocess.run([sys.executable, str(self.hook)], input=json.dumps(event), text=True, capture_output=True,
                              timeout=90, check=False)

    def announced(self) -> list[list[str]]:
        return json.loads(self.calls.read_text()) if self.calls.exists() else []


@pytest.fixture
def scene(tmp_path, monkeypatch, workspace_node):
    monkeypatch.chdir(tmp_path)
    made = Scene(tmp_path, workspace_node, monkeypatch)
    yield made
    for job in made.table.ids():
        if made.state(job) in ("queued", "running"):
            made.table.cancel(job, "someone")


def test_a_stopped_subagent_has_its_queued_and_running_test_jobs_cancelled(scene):
    own = scene.job(scene.name, scene.primary)
    in_tree = scene.job("someone-else", scene.worktree)
    other = scene.job("another-agent", scene.primary)
    done = scene.job(scene.name, scene.primary)
    scene.table.cancel(done, scene.name)
    time.sleep(0.5)
    result = scene.stop(scene.worktree)
    assert result.returncode == 0, result.stderr
    assert scene.state(own) == "cancelled"
    assert scene.state(in_tree) == "cancelled"
    assert scene.state(other) in ("queued", "running")
    assert not result.stderr


def test_stopping_twice_changes_nothing_more(scene):
    job = scene.job(scene.name, scene.primary)
    scene.stop(scene.worktree)
    first = scene.table.read(job, "status.json")
    again = scene.stop(scene.worktree)
    assert again.returncode == 0 and scene.table.read(job, "status.json") == first


def test_a_job_that_cannot_be_cancelled_is_reported_loudly(scene, monkeypatch):
    job = scene.job(scene.name, scene.primary)
    log = scene.table.path(job) / "log"
    log.chmod(0o400)
    result = scene.stop(scene.worktree)
    log.chmod(0o600)
    assert result.returncode == 0
    assert "could not cancel a test job" in result.stderr
    assert any(call[:2] == ["announce", "blocked"] and job in call[2] for call in scene.announced())

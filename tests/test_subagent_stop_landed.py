"""A subagent cannot stop while its own tree holds work that the development branch on origin lacks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest_plugins = ["node_kit"]
ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts/hooks/claude-subagent-stop"
GIT = shutil.which("git") or "git"
AGENT = "abcdef12345"
IDENTITY = ["-c", "user.name=Stop fixture", "-c", "user.email=fixture@example.invalid"]


def git(where: Path, *args: str) -> str:
    done = subprocess.run([GIT, *IDENTITY, "-C", str(where), *args], capture_output=True, text=True, check=True,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    return done.stdout.strip()


def commit(where: Path, name: str) -> None:
    (where / name).write_text(name)
    git(where, "add", name)
    git(where, "commit", "-m", f"add {name}")


class World:
    """An origin, the primary checkout on its development branch, a worktree for the worker and the board's record of the calls."""

    def __init__(self, root: Path, name: str) -> None:
        self.root, self.name = root, name
        self.origin, self.primary, self.tree = root / "origin.git", root / "project", root / f"agent-{AGENT}"
        self.calls, self.claims, self.gitlog = root / "calls.json", root / "claims.json", root / "git.log"
        git(root, "init", "--bare", "-b", "0.3dev", str(self.origin))
        git(root, "init", "-b", "0.3dev", str(self.primary))
        commit(self.primary, "base.txt")
        git(self.primary, "remote", "add", "origin", str(self.origin))
        git(self.primary, "push", "origin", "0.3dev")
        git(self.primary, "worktree", "add", "-b", "worker/topic", str(self.tree))

    def stop(self, where: Path | None = None) -> subprocess.CompletedProcess:
        event = {"agent_type": "branch-worker", "agent_id": AGENT, "cwd": str(where or self.tree)}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), text=True, capture_output=True,
                              timeout=60, check=False)

    def recorded(self) -> list[list[str]]:
        return [call["argv"] for call in json.loads(self.calls.read_text())] if self.calls.exists() else []

    def announced(self, kind: str) -> list[str]:
        return [argv[2] for argv in self.recorded() if argv[:2] == ["announce", kind]]

    def land(self) -> None:
        git(self.tree, "push", "origin", "worker/topic:0.3dev")

    def add_tree(self, name: str) -> Path:
        """A worktree with its own branch `NAME-branch`, which the harness did not make for the subagent."""
        git(self.primary, "worktree", "add", "-b", f"{name}-branch", str(self.root / name))
        return self.root / name

    def claim(self, kind: str, key: str, owner: str | None = None) -> None:
        self.claims.write_text(json.dumps([{"kind": kind, "key": key, "owner": owner or self.name}]))


@pytest.fixture
def world(tmp_path, monkeypatch, workspace_node):
    name = workspace_node.member(AGENT, model="claude-haiku-4-5").name
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "poolhouse-workspace"
    cli.write_text(f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
path = Path(os.environ['HOOK_CALLS'])
calls = json.loads(path.read_text()) if path.exists() else []
calls.append(dict(argv=sys.argv[1:], agent=os.environ.get('POOLHOUSE_WORKSPACE_AGENT')))
path.write_text(json.dumps(calls))
claims = Path(os.environ['HOOK_CLAIMS'])
print(claims.read_text() if sys.argv[1] == 'claims' and claims.exists() else '[]' if sys.argv[1] == 'claims' else 'ok')
""")
    cli.chmod(0o700)
    logged = bin_dir / "git"
    logged.write_text(f'#!/bin/sh\n[ -n "$GIT_LOG" ] && echo "$@" >> "$GIT_LOG"\nexec {GIT} "$@"\n')
    logged.chmod(0o700)
    built = World(tmp_path, name)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HOOK_CALLS", str(built.calls))
    monkeypatch.setenv("HOOK_CLAIMS", str(built.claims))
    monkeypatch.setenv("GIT_LOG", str(built.gitlog))
    monkeypatch.setenv("POOLHOUSE_HOOK_STATE", str(tmp_path / "hookstate"))
    monkeypatch.delenv("POOLHOUSE_DEV_BRANCH", raising=False)
    return built


def refusal(done: subprocess.CompletedProcess) -> str:
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    assert answer["decision"] == "block"
    return answer["reason"]


def passed(done: subprocess.CompletedProcess) -> None:
    assert done.returncode == 0 and not done.stdout.strip(), done.stdout


def test_unlanded_commits_block_the_stop_and_name_the_ways_out(world):
    commit(world.tree, "work.txt")
    reason = refusal(world.stop())
    assert "1 commit" in reason and "origin/0.3dev" in reason and "worker/topic" in reason
    assert "scripts/land" in reason and f"scripts/worktrees close {world.tree.resolve()} --bundle" in reason
    assert "--abandon" in reason
    assert world.announced("done") == [] and ["retire"] not in world.recorded()


def test_uncommitted_files_count_as_unlanded(world):
    (world.tree / "base.txt").write_text("changed")
    (world.tree / "new.txt").write_text("new")
    assert "2 uncommitted" in refusal(world.stop())


def test_a_tree_is_held_only_while_it_holds_unlanded_work(world):
    passed(world.stop())
    (world.tree / "base.txt").write_text("changed")
    refusal(world.stop())
    git(world.tree, "checkout", "--", "base.txt")
    passed(world.stop())
    commit(world.tree, "work.txt")
    refusal(world.stop())
    world.land()
    passed(world.stop())
    assert world.announced("done") and ["retire"] in world.recorded()


def test_a_commit_only_in_the_local_development_branch_is_not_landed(world):
    commit(world.tree, "work.txt")
    git(world.primary, "merge", "--ff-only", "worker/topic")
    assert "1 commit" in refusal(world.stop())


@pytest.mark.parametrize("where", ["primary", "foreign", "elsewhere", "gone"])
def test_only_the_trees_of_the_subagent_are_held(world, tmp_path, where):
    commit(world.tree, "work.txt")
    refusal(world.stop())
    world.land()
    foreign = world.add_tree("lead-tree")
    (foreign / "base.txt").write_text("an edit of the lead")
    (world.primary / "base.txt").write_text("an edit in the primary checkout")
    (tmp_path / "elsewhere").mkdir()
    places = {"primary": world.primary, "foreign": foreign, "elsewhere": tmp_path / "elsewhere", "gone": tmp_path / "removed"}
    passed(world.stop(places[where]))
    assert ["retire"] in world.recorded()


def test_the_tree_made_for_the_subagent_is_held_wherever_it_stops(world):
    commit(world.tree, "work.txt")
    assert str(world.tree.resolve()) in refusal(world.stop(world.primary))


def test_the_second_refusal_says_blocked_and_the_third_stop_goes_through_with_a_milestone(world):
    commit(world.tree, "work.txt")
    refusal(world.stop())
    assert world.announced("blocked") == []
    refusal(world.stop())
    assert len(world.announced("blocked")) == 1 and f"agent-{AGENT}" in world.announced("blocked")[0]
    passed(world.stop())
    milestones = world.announced("milestone")
    assert len(milestones) == 1 and "unlanded" in milestones[0] and "worker/topic" in milestones[0]
    assert len(world.announced("blocked")) == 1 and all(len(text) <= 200 for text in world.announced("blocked") + milestones)
    assert world.announced("done") == [] and ["retire"] not in world.recorded()
    world.land()
    passed(world.stop())
    assert len(world.announced("done")) == 1 and ["retire"] in world.recorded()


def test_landing_between_stops_starts_the_count_again(world):
    commit(world.tree, "first.txt")
    refusal(world.stop())
    world.land()
    passed(world.stop())
    commit(world.tree, "second.txt")
    refusal(world.stop())
    assert world.announced("blocked") == []


def test_a_claimed_worktree_is_held(world):
    claimed = world.add_tree("finisher-tree")
    commit(claimed, "work.txt")
    world.claim("worktree", str(claimed.resolve()))
    reason = refusal(world.stop(world.primary))
    assert str(claimed.resolve()) in reason and str(world.tree.resolve()) not in reason


def test_a_claimed_branch_is_held_through_its_tree(world):
    claimed = world.add_tree("finisher-tree")
    commit(claimed, "work.txt")
    world.claim("branch", "finisher-tree-branch")
    assert "finisher-tree-branch" in refusal(world.stop(world.primary))


def test_a_claim_held_by_another_agent_is_not_held(world):
    claimed = world.add_tree("finisher-tree")
    commit(claimed, "work.txt")
    world.claim("worktree", str(claimed.resolve()))
    refusal(world.stop(world.primary))
    world.claim("worktree", str(claimed.resolve()), owner="someone-else")
    passed(world.stop(world.primary))


def test_a_stop_costs_few_git_commands_whether_it_is_held_or_not(world):
    commit(world.tree, "work.txt")
    refusal(world.stop())
    held = len(world.gitlog.read_text().splitlines())
    world.land()
    world.gitlog.write_text("")
    passed(world.stop())
    clear = len(world.gitlog.read_text().splitlines())
    assert held <= 18 and clear <= 18, (held, clear)


def test_the_repository_settings_run_a_hook_that_holds_the_stop(world):
    commit(world.tree, "work.txt")
    settings = json.loads((ROOT / ".claude/settings.json").read_text())
    command = settings["hooks"]["SubagentStop"][0]["hooks"][0]["command"]
    event = {"agent_id": AGENT, "cwd": str(world.tree)}
    done = subprocess.run(["/bin/sh", "-c", command], input=json.dumps(event), text=True, capture_output=True,
                          timeout=60, check=False, env={**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT)})
    assert json.loads(done.stdout)["decision"] == "block", done.stderr

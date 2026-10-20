"""A subagent that hands back with work unlanded keeps its identity so it can be resumed to land it; a landed one is retired."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from git_world import DEV, World, git
from node_kit import SRC

from poolhouse.board import session as board_session
from poolhouse.board.client import Denied

pytest_plugins = ["node_kit"]
HOOKS = Path(__file__).resolve().parents[1] / "scripts/hooks"
AGENT = "abcdef12345"


class Scene:
    """A project with the worktree the harness makes for a subagent, a lead, and that subagent registered through its start hook."""

    def __init__(self, world: World, lead, tree: Path) -> None:
        self.world, self.node, self.lead, self.tree = world, world.node, lead, tree
        self.event = {"agent_type": "branch-worker", "agent_id": AGENT, "session_id": lead.session, "cwd": str(tree)}
        self.start()
        self.child = self.node.adopt(board_session.find("claude-code", AGENT, client=self.node.client))

    def hook(self, name: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(HOOKS / name)], input=json.dumps(self.event), text=True,
                              capture_output=True, timeout=90, check=False)

    def start(self) -> None:
        done = self.hook("claude-subagent-start")
        assert done.returncode == 0 and not done.stderr, done.stderr

    def stop(self) -> subprocess.CompletedProcess:
        return self.hook("claude-subagent-stop")

    def refused(self) -> bool:
        done = self.stop()
        assert done.returncode == 0 and not done.stderr, done.stderr
        return bool(done.stdout.strip()) and json.loads(done.stdout)["decision"] == "block"

    def token_works(self) -> bool:
        try:
            self.node.client.call("whoami", self.node.board, self.child.token)
        except Denied:
            return False
        return True

    def announcements(self) -> list[tuple[str, str]]:
        entries = self.node.session(self.lead).read(channel="#announcements").entries
        return [(e.fields["type"], e.sender) for e in entries]


@pytest.fixture
def scene(tmp_path, workspace_node, monkeypatch):
    binary = tmp_path / "bin"
    binary.mkdir()
    wrapper = binary / "poolhouse-workspace"
    wrapper.write_text(f"#!{sys.executable}\nimport sys\nfrom poolhouse.workspace.cli import main\n"
                       "raise SystemExit(main(sys.argv[1:]))\n")
    wrapper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PYTHONPATH", SRC)
    monkeypatch.setenv("POOLHOUSE_HOOK_STATE", str(tmp_path / "hookstate"))
    monkeypatch.setenv("POOLHOUSE_NONINTERACTIVE", "1")
    monkeypatch.setenv("POOLHOUSE_RUNTIME_ENSURE", "off")
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", DEV)
    world = World(tmp_path, workspace_node)
    lead = workspace_node.member("sess1")
    tree = world.proj.branch(f"agent-{AGENT}", {"docs/a.md": "changed\n"})
    monkeypatch.chdir(tmp_path)
    return Scene(world, lead, tree)


def test_a_worker_that_hands_back_unlanded_work_is_resumed_with_its_own_token_to_claim(scene):
    assert scene.refused() and scene.refused()
    assert not scene.refused(), "the third stop goes through"
    assert scene.token_works()
    assert ("done", scene.child.name) not in scene.announcements()
    scene.start()
    assert scene.token_works()
    for kind, key in (("branch", f"agent-{AGENT}"), ("worktree", str(scene.tree))):
        claimed = scene.node.cli("claim", kind, key, who=scene.child)
        assert claimed.returncode == 0 and scene.child.name in claimed.stdout, claimed.stderr


def test_a_worker_is_retired_when_it_stops_with_its_branch_landed_and_not_before(scene):
    assert scene.refused() and scene.refused()
    assert not scene.refused() and scene.token_works()
    git(scene.tree, "push", "origin", f"agent-{AGENT}:{DEV}")
    assert not scene.refused()
    assert not scene.token_works()
    assert [kind for kind, _ in scene.announcements()] == ["blocked", "milestone"]


def said(scene, who, *argv):
    done = scene.node.cli(*argv, "--json", who=who)
    assert done.returncode == 0, (argv, done.stderr, done.stdout)
    return json.loads(done.stdout)


def land_and_stop(scene):
    git(scene.tree, "push", "origin", f"agent-{AGENT}:{DEV}")
    assert not scene.refused()


def test_the_parent_is_subscribed_to_its_subagent_and_the_roll_up_hears_no_join(scene):
    assert {"type": "agent", "target": scene.child.name, "mode": "inbox"} in said(scene, scene.lead, "subs")
    assert scene.announcements() == []


def test_a_finished_subagent_is_a_done_its_subscribers_learn_in_their_own_mode(scene):
    watcher, stranger = scene.node.member("sess2"), scene.node.member("sess3")
    said(scene, watcher, "subscribe", "agent", scene.child.name, "--mode", "digest")
    land_and_stop(scene)
    heard = [m for m in said(scene, scene.lead, "inbox") if scene.child.name in json.dumps(m)]
    assert len(heard) == 1 and "subject: done" in heard[0]["text"] and f"agent-{AGENT}" in heard[0]["text"]
    assert said(scene, stranger, "inbox") == []
    assert said(scene, watcher, "inbox") == []
    assert scene.child.name in scene.node.cli("digest", who=watcher).stdout
    assert scene.announcements() == []


def test_a_subagent_handed_back_with_work_unlanded_says_no_done(scene):
    for _ in range(3):
        scene.refused()
    assert [m for m in said(scene, scene.lead, "inbox") if "subject: done" in m["text"]] == []


def test_a_parent_keeps_its_subscriptions_to_ended_subagents_while_it_has_room_for_a_new_child(scene):
    ended = [scene.node.member(f"old{n}") for n in range(11)]
    for old in ended:
        said(scene, scene.lead, "subscribe", "agent", old.name, "--mode", "digest")
        assert scene.node.cli("retire", who=old).returncode == 0
    scene.event = {**scene.event, "agent_id": "fedcba98765"}
    scene.start()
    held = {(row["type"], row["target"]) for row in said(scene, scene.lead, "subs")}
    assert ("agent", scene.child.name) in held and all(("agent", old.name) in held for old in ended)
    assert len(held) == 13

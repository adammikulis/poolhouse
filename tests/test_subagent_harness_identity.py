"""A subagent's identity comes from the harness: the start hook registers every kind of subagent, even at the board's limit, and each command it runs is stamped as it, with no flag and no token typed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from git_world import DEV, World
from node_kit import SRC

from poolhouse import authority
from poolhouse.board import session as board_session
from poolhouse.board.client import Denied
from poolhouse.sentinel.human import HumanRequired

pytest_plugins = ["node_kit"]
ROOT = Path(__file__).resolve().parents[1]


class Scene:
    """A project, a real node with a lead session, and the real start, stop and Bash hooks run as Claude Code runs them."""

    def __init__(self, tmp_path: Path, node, monkeypatch) -> None:
        self.node, self.world = node, World(tmp_path, node)
        self.lead = node.member("sess1")
        self.hooks = ROOT / "scripts/hooks"
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
        monkeypatch.chdir(tmp_path)

    def inherited(self) -> dict[str, str]:
        """The environment every command of the lead's session, and so of its subagents, starts with."""
        return {**self.node.env(self.lead, {"POOLHOUSE_DEV_BRANCH": DEV, "PATH": os.environ["PATH"]}), "CLAUDECODE": "1"}

    def hook(self, name: str, event: dict) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.hooks / name)], input=json.dumps(event), text=True,
                              capture_output=True, timeout=90, check=False)

    def event(self, agent: str, kind: str = "general-purpose", **more: object) -> dict:
        return {"agent_type": kind, "agent_id": agent, "session_id": "sess1", "cwd": str(self.world.proj.root), **more}

    def start(self, agent: str, kind: str = "general-purpose") -> str:
        """Start a subagent with an empty brief; the context its hook delivers."""
        done = self.hook("claude-subagent-start", self.event(agent, kind))
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"] if done.stdout else ""

    def stop(self, agent: str, kind: str = "general-purpose") -> None:
        done = self.hook("claude-subagent-stop", self.event(agent, kind))
        assert done.returncode == 0 and not done.stdout.strip(), (done.stdout, done.stderr)

    def hand_back(self, agent: str, kind: str = "general-purpose") -> None:
        """Stop a subagent whose tree holds unlanded work: two refusals, then the stop goes through with its identity kept."""
        for _ in range(2):
            refused = self.hook("claude-subagent-stop", self.event(agent, kind))
            assert refused.returncode == 0 and json.loads(refused.stdout)["decision"] == "block", (refused.stdout, refused.stderr)
        self.stop(agent, kind)

    def name(self, agent: str) -> str:
        return board_session.find("claude-code", agent, client=self.node.client)

    def run(self, agent: str, command: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        """Run ``command`` as the Bash hook leaves it for the subagent ``agent``, in the shell the lead started."""
        asked = {"tool_name": "Bash", "agent_id": agent, "session_id": "sess1", "cwd": str(cwd or self.world.proj.root),
                 "tool_input": {"command": command, "description": "a step"}}
        guard = self.hook("claude-bash-guard", asked)
        assert guard.returncode == 0, guard.stderr
        stamped = json.loads(guard.stdout)["hookSpecificOutput"]["updatedInput"]
        assert stamped["description"] == "a step"
        return subprocess.run(["/bin/sh", "-c", stamped["command"]], cwd=cwd or self.world.proj.root, env=self.inherited(),
                              text=True, capture_output=True, timeout=120, check=False)


@pytest.fixture
def scene(tmp_path, workspace_node, monkeypatch):
    return Scene(tmp_path, workspace_node, monkeypatch)


def test_a_general_purpose_subagent_with_an_empty_brief_is_registered_and_records_a_review_with_no_flag(scene):
    _, sha = scene.world.start("fix/x", {"docs/a.md": "changed\n"})
    context = scene.start("rev1")
    child = scene.name("rev1")
    assert child and child != scene.lead.name and f"You are the subagent {child}, spawned by {scene.lead.name}" in context
    done = scene.run("rev1", f"poolhouse-workspace land-review fix/x {sha} --verdict accept")
    assert done.returncode == 0, done.stderr
    accepts = scene.node.client.call("land_accepts", scene.node.board, scene.lead.token, sha=sha)
    assert child in json.dumps(accepts) and scene.lead.name not in json.dumps(accepts)


def test_a_subagent_command_acts_as_the_subagent_not_the_session_that_started_it(scene):
    scene.start("rev1")
    child = scene.name("rev1")
    done = scene.run("rev1", "poolhouse-workspace whoami --json")
    assert done.returncode == 0 and json.loads(done.stdout)["id"] == child, done.stderr
    sent = scene.node.cli("send", scene.world.other.name, "note", "hello", "--agent", scene.lead.name, who=scene.lead,
                          env={"POOLHOUSE_SUBAGENT": "rev1"})
    assert sent.returncode == 0 and f"--agent {scene.lead.name} is not you: running as {child}" in sent.stderr, sent.stderr
    inbox = json.loads(scene.node.cli("inbox", "--json", who=scene.world.other).stdout)
    assert [m["from"] for m in inbox] == [child]


def test_a_stale_agent_name_falls_back_to_the_callers_own_identity(scene):
    done = scene.node.cli("whoami", "--json", "--agent", "claude-000000", who=scene.lead)
    assert done.returncode == 0 and json.loads(done.stdout)["id"] == scene.lead.name
    assert "claude-000000 is not a live session here: running as " + scene.lead.name in done.stderr
    assert scene.node.cli("whoami", "--agent", "claude-000000").returncode == 3


def test_a_resumed_worker_keeps_its_identity_and_claims(scene, tmp_path, monkeypatch):
    tree = scene.world.proj.branch("agent-w1", {"docs/a.md": "changed\n"})
    private = tmp_path / "private-terms"
    private.write_text("finished\n")
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_DENYLIST", str(private))
    scene.start("w1", "branch-worker")
    child = scene.name("w1")
    scene.hand_back("w1", "branch-worker")
    assert scene.name("w1") == child and scene.node.cli("whoami", who=scene.node.adopt(child)).returncode == 0
    context = scene.start("w1", "branch-worker")
    assert scene.name("w1") == child and child in context
    for kind, key in (("branch", "agent-w1"), ("worktree", str(tree))):
        claimed = scene.run("w1", f"poolhouse-workspace claim {kind} {key}", cwd=tree)
        assert claimed.returncode == 0 and child in claimed.stdout, claimed.stderr


def test_a_finished_reviewer_is_retired_and_its_name_stops_working(scene):
    _, sha = scene.world.start("fix/y", {"docs/a.md": "changed\n"})
    scene.start("rev2")
    child = scene.name("rev2")
    assert scene.run("rev2", f"poolhouse-workspace land-review fix/y {sha} --verdict accept").returncode == 0
    token = scene.node.adopt(child).token
    scene.stop("rev2")
    with pytest.raises(Denied):
        scene.node.client.call("whoami", scene.node.board, token)
    agents = {a.name: a for a in scene.node.session(scene.lead).agents(retired=True)}
    assert agents[child].retired
    revived = scene.start("rev2")
    assert scene.name("rev2.2") not in ("", child) and scene.name("rev2.2") in revived


def test_a_lead_registers_any_number_of_subagents_and_a_finished_one_is_retired(scene):
    agents = [f"a{i}" for i in range(20)]
    for agent in agents:
        context = scene.start(agent, "branch-worker")
        assert scene.name(agent) and scene.name(agent) in context, context
    old = scene.name("a0")
    scene.stop("a0", "branch-worker")
    live = {a.name for a in scene.node.session(scene.lead).agents()}
    assert len(live) >= 19 and old not in live


def test_a_subagent_the_board_cannot_register_is_told_and_so_is_the_board(scene):
    event = scene.event("a4")
    del event["agent_id"]
    done = scene.hook("claude-subagent-start", event)
    context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "could not register you" in context and "no agent_id" in context
    heard = [e.text for e in scene.node.session(scene.lead).read(channel="#announcements").entries]
    assert any("has no identity" in line for line in heard)


def test_the_stamp_removes_the_leads_name_for_a_subagent_the_board_never_registered(scene):
    done = scene.run("ghost", "poolhouse-workspace whoami")
    assert done.returncode == 3 and "no name on the board" in done.stderr
    env = scene.run("ghost", "env")
    assert scene.lead.name not in env.stdout and "POOLHOUSE_SUBAGENT=ghost" in env.stdout


@pytest.mark.redteam
def test_a_subagent_cannot_name_another_identity_or_the_subagent_variable(scene):
    scene.start("rev1")
    for command in (f"poolhouse-workspace inbox --agent {scene.lead.name}", f"POOLHOUSE_WORKSPACE_AGENT={scene.lead.name} true",
                    "POOLHOUSE_SUBAGENT=other true", "poolhouse-workspace inbox --token-file /x"):
        asked = {"tool_name": "Bash", "agent_id": "rev1", "session_id": "sess1", "cwd": "/", "tool_input": {"command": command}}
        done = scene.hook("claude-bash-guard", asked)
        assert done.returncode == 2 and "you are" in done.stderr, (command, done.stderr)
    benign = {"tool_name": "Bash", "agent_id": "rev1", "session_id": "sess1", "cwd": "/",
              "tool_input": {"command": 'grep -rn "POOLHOUSE_WORKSPACE_AGENT" src scripts'}}
    assert scene.hook("claude-bash-guard", benign).returncode == 0


@pytest.mark.redteam
def test_a_subagent_never_passes_a_delegated_gate_even_with_the_leads_name_in_its_environment(scene, monkeypatch):
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    lead = {"CLAUDECODE": "1", "POOLHOUSE_WORKSPACE_AGENT": scene.lead.name}
    assert authority.require("sentinel.policy", "mode", (False, False), lead) == authority.DELEGATED
    with pytest.raises(HumanRequired, match="not a helper"):
        authority.require("sentinel.policy", "mode", (False, False), {**lead, "POOLHOUSE_SUBAGENT": "rev1"})

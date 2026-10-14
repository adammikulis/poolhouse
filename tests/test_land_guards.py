"""Guarded source changes require live exact-commit independent queue approval."""

from __future__ import annotations

import json

import land_finish
import pytest
from land_support import Project, git
from land_world import World, mod

from poolhouse.activity.gate import tree_hash
from poolhouse.workspace import landing

pytest_plugins = ["node_kit"]

GUARDS = {
    "AGENTS.md": "rules\n",
    "CLAUDE.md": "rules\n",
    ".claude/settings.json": "{}\n",
    ".claude/agents/worker.md": "---\n---\n",
    "scripts/hooks/pre-push": "#!/bin/sh\nexit 0\n",
    "scripts/hooks/weakened-assertions": "#!/bin/sh\nexit 0\n",
    "scripts/hooks/claude-bash-guard": "#!/bin/sh\nexit 0\n",
    "src/poolhouse/workspace/person_store.py": "X = 1\n",
    "src/poolhouse/worktreerules.py": "X = 1\n",
    "scripts/land_board.py": "X = 1\n",
    "src/poolhouse/authority.py": "X = 1\n",
    "src/poolhouse/person.py": "X = 1\n",
    "src/poolhouse/real_machine.py": "X = 1\n",
    "src/poolhouse/board/credentials.py": "X = 1\n",
    "src/poolhouse/workspace/authority_cli.py": "X = 1\n",
    "scripts/agent_sandbox_profile.py": "X = 1\n",
    "scripts/github-protection": "X = 1\n",
    ".github/workflows/ci.yml": "on: push\n",
}


@pytest.fixture
def world(monkeypatch, tmp_path, workspace_node):
    return World(monkeypatch, tmp_path, workspace_node)


@pytest.mark.parametrize("path", sorted(GUARDS))
def test_independently_reviewed_guarded_source_lands(world, path):
    rid, _ = world.ready("guards", {**mod(1), path: GUARDS[path]})
    before = world.origin_head()
    world.runner.once()
    assert world.status(rid) == "landed", world.detail(rid)
    assert world.origin_head() != before
    assert world.origin_head() == world.local_head()
    assert world.proj.land("plan")[0] == 0


def test_unreviewed_guarded_source_waits_without_holding_reviewed_work(world):
    sha = world.branch("guards", {**mod(3), "AGENTS.md": "rules\n"})
    held = world.ask("alice", "guards", sha)
    free, _ = world.ready("plain", mod(4), who="bob")
    world.runner.once()
    assert (world.status(held), world.status(free)) == ("needs-review", "landed")


@pytest.mark.parametrize("path", ["AGENTS.md", "src/poolhouse/authority.py", "scripts/hooks/pre-push", ".claude/settings.json"])
def test_direct_landing_requires_authenticated_queue_approval(world, path):
    world.branch("held", {path: "x\n"})
    world.proj.env.update(world.members["bob"].env())
    for args in (("run", "held"), ("run", "--dry-run", "held")):
        code, out, summary = world.proj.land(*args)
        assert code == 4 and summary["status"] == "error", out
        assert "authenticated independent queue approval" in summary["error"]
    assert world.proj.calls() == []


def test_direct_guarded_landing_persists_exact_approval_and_revalidates_cancel(world):
    rid, sha = world.ready("held", {"AGENTS.md": "rules\n"})
    world.proj.env.update(world.members["bob"].env())
    code, out, summary = world.proj.land("run", "held")
    assert code == 0 and summary["status"] == "verified", out
    state = land_finish.load(world.proj.root)
    assert state["approvals"] == [{"id": rid, "branch": "held", "sha": sha, "target": "devline"}]
    landing.cancel(world.who["alice"], rid)
    code, out, summary = world.proj.land("finish", "--apply")
    assert code == 3 and summary["status"] == "blocked", out
    assert "no matching live request" in summary["reason"]
    assert world.local_head() == state["base"]


def test_direct_guarded_finish_revalidates_rejection(world):
    rid, sha = world.ready("held", {"AGENTS.md": "rules\n"})
    world.proj.env.update(world.members["bob"].env())
    assert world.proj.land("run", "held")[0] == 0
    landing.review(world.who["carol"], rid, sha, "reject")
    code, out, summary = world.proj.land("finish", "--apply")
    assert code == 3 and "rejected" in summary["reason"], out


def test_direct_guarded_finish_rejects_unrecorded_sensitive_merge_content(world):

    world.ready("held", {"AGENTS.md": "rules\n"})
    world.proj.env.update(world.members["bob"].env())
    assert world.proj.land("run", "held")[0] == 0
    state = land_finish.load(world.proj.root)
    tree = world.proj.root.parent / state["worktree"]
    world.proj.write(tree, "AGENTS.md", "injected\n")
    git(tree, "add", "AGENTS.md")
    git(tree, "commit", "--amend", "--no-edit", "-q")
    state["tree"] = tree_hash(tree)
    land_finish.land_run.state_file(world.proj.root).write_text(json.dumps(state))
    code, out, summary = world.proj.land("finish", "--apply")
    assert code == 3 and "reviewed merge content" in summary["reason"], out


@pytest.fixture
def proj(tmp_path):
    return Project(tmp_path)


def test_direct_ordinary_documentation_remains_usable_without_review(proj):
    proj.branch("plain", {"docs/a.md": "changed\n"})
    assert proj.land("run", "plain")[0] == 0


def test_direct_guarded_landing_refuses_missing_authenticated_session(world):
    world.ready("held", {"AGENTS.md": "rules\n"})
    world.proj.env.pop("POOLHOUSE_WORKSPACE_AGENT", None)
    world.proj.env.pop("POOLHOUSE_WORKSPACE_TOKEN", None)
    code, out, summary = world.proj.land("run", "held")
    assert code == 4 and "no token" in summary["error"], out


def test_direct_guarded_landing_refuses_request_for_another_sha(world):
    rid, _ = world.ready("held", {"AGENTS.md": "rules\n"})
    world.proj.env.update(world.members["bob"].env())
    tree = world.proj.base / "held"
    world.proj.write(tree, "AGENTS.md", "changed rules\n")
    world.proj.commit(tree, "fix: change rules")
    code, out, summary = world.proj.land("run", "held")
    assert code == 4 and "no matching live request" in summary["error"], out
    assert world.requests()[rid]["sha"] != git(tree, "rev-parse", "HEAD")


def test_direct_guarded_finish_refuses_revoked_reviewer(world):
    reviewer = world.node.member("review-child", parent=world.members["bob"])
    sha = world.branch("held", {"AGENTS.md": "rules\n"})
    rid = world.ask("alice", "held", sha)
    landing.review(world.node.session(reviewer), rid, sha, "accept")
    world.proj.env.update(world.members["bob"].env())
    assert world.proj.land("run", "held")[0] == 0
    world.who["bob"].retire(reviewer.name, "revoked")
    code, out, summary = world.proj.land("finish", "--apply")
    assert code == 3 and "no independent accept" in summary["reason"], out


def test_direct_guarded_finish_refuses_paused_queue(world):
    world.ready("held", {"AGENTS.md": "rules\n"})
    world.proj.env.update(world.members["bob"].env())
    assert world.proj.land("run", "held")[0] == 0
    world.lead.claim(*landing.runner_claim("devline"))
    landing.brake(world.lead, True)
    code, out, summary = world.proj.land("finish", "--apply")
    assert code == 3 and "paused" in summary["reason"], out


def test_direct_guarded_finish_refuses_forged_batch_base(world):
    world.ready("held", {"AGENTS.md": "rules\n"})
    world.proj.env.update(world.members["bob"].env())
    assert world.proj.land("run", "held")[0] == 0
    state = land_finish.load(world.proj.root)
    state["base"] = git(world.proj.root, "rev-parse", state["integration"])
    state["approvals"] = []
    land_finish.land_run.state_file(world.proj.root).write_text(json.dumps(state))
    code, out, summary = world.proj.land("finish", "--apply")
    assert code == 3 and "batch base differs" in summary["reason"], out


def test_direct_finish_refuses_forged_release_target(world):
    world.ready("held", {"AGENTS.md": "rules\n"})
    world.proj.env.update(world.members["bob"].env())
    assert world.proj.land("run", "held")[0] == 0
    state = land_finish.load(world.proj.root)
    git(world.proj.root, "branch", "main", state["base"])
    state["target"] = "main"
    land_finish.land_run.state_file(world.proj.root).write_text(json.dumps(state))
    code, out, summary = world.proj.land("finish", "--apply")
    assert code == 3 and summary["reason"] == "land never touches main", out

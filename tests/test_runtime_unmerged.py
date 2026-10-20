"""Delegated source admission against an authenticated node and exact Git refs."""

from dataclasses import replace

import pytest
from land_support import Project, git

from poolhouse import (
    authority,
    node_binary,
    node_supervise,
    runtime,
    runtime_cli,
    runtime_deploy,
    runtime_git,
    runtime_source,
    runtime_unmerged,
    worktreerules,
)
from poolhouse.board import place, session as board_session
from poolhouse.board.client import Denied
from poolhouse.fleet import runtime_wheel
from poolhouse.workspace import landing

pytest_plugins = ["node_kit"]


@pytest.fixture
def reviewed(tmp_path, monkeypatch, workspace_node):
    base = tmp_path / "git"
    base.mkdir()
    project = Project(base)
    Project.write(project.root, "src/poolhouse/__init__.py", "")
    Project.commit(project.root, "chore: runtime source marker")
    monkeypatch.delenv("POOLHOUSE_BOARD", raising=False)
    board = place.resolve(workspace_node.client, project.root)
    workspace_node.board = board
    author = workspace_node.member("source-author")
    reviewer = workspace_node.member("source-reviewer")
    lead = workspace_node.member("runtime-owner")
    project.branch("reviewed-source", {"extra.py": "VALUE = 2\n"})
    sha = git(project.root, "rev-parse", "reviewed-source")
    workspace_node.session(author).claim("branch", "reviewed-source")
    landing.review(workspace_node.session(reviewer), "reviewed-source", sha, "accept")
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv(authority.AGENT_ENV, lead.name)
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", "devline")
    return project, workspace_node, lead, reviewer, sha, "reviewed-source"


def approval(reviewed):
    project, _, _, _, sha, branch = reviewed
    return runtime_unmerged.approve(project.root, sha, branch)


def test_delegated_exact_review_rechecks_and_audits_both_gates(reviewed):
    project, _, lead, _, sha, _ = reviewed
    granted = approval(reviewed)
    runtime_source.admit(project.root, sha, allow_unmerged=granted)
    uses = [r for r in authority.audit_rows() if r["event"] == "authority.use"]
    assert {r["gate"] for r in uses} == {"runtime.deploy", "runtime.deploy-unmerged"}
    assert all(r["agent"] == lead.name and sha in r["action"] for r in uses)
    assert all(granted.branch in r["action"] for r in uses if r["gate"] == runtime_unmerged.GATE)


@pytest.mark.parametrize("field,value", [("commit", "f" * 40), ("board", "elsewhere"),
                                         ("branch", "missing"), ("actor", "other")])
def test_forged_or_replayed_binding_fails(reviewed, field, value):
    project, _, _, _, sha, _ = reviewed
    granted = replace(approval(reviewed), **{field: value})
    with pytest.raises(runtime_source.DeployError):
        runtime_source.admit(project.root, sha, allow_unmerged=granted)


def test_bool_only_agent_exception_never_authorizes_source(reviewed):
    project, _, _, _, sha, _ = reviewed
    with pytest.raises(runtime_source.DeployError, match="exact reviewed"):
        runtime_source.admit(project.root, sha, allow_unmerged=True)


@pytest.mark.parametrize("gate", ["runtime.deploy", "runtime.deploy-unmerged"])
def test_person_floor_blocks_each_gate(reviewed, gate):
    granted = approval(reviewed)
    authority.set_state([gate], authority.PERSON, by=granted.actor)
    with pytest.raises(runtime_source.DeployError, match="for a person"):
        runtime_unmerged.validate(granted, granted.primary, granted.commit)


def test_later_reject_invalidates_approval(reviewed):
    project, node, _, reviewer, sha, branch = reviewed
    granted = approval(reviewed)
    landing.review(node.session(reviewer), branch, sha, "reject")
    with pytest.raises(runtime_source.DeployError, match="standing independent"):
        runtime_source.admit(project.root, sha, allow_unmerged=granted)


def test_branch_movement_invalidates_approval(reviewed):
    project, _, _, _, sha, _ = reviewed
    granted = approval(reviewed)
    git(project.root, "update-ref", "refs/heads/reviewed-source", git(project.root, "rev-parse", "HEAD"))
    with pytest.raises(runtime_source.DeployError, match="branch changed"):
        runtime_source.admit(project.root, sha, allow_unmerged=granted)


def test_a_branch_with_no_independent_accept_is_not_approved(reviewed):
    project, node, _, _, _, _ = reviewed
    node.session(node.members["source-author"]).claim("branch", "unreviewed")
    project.branch("unreviewed", {"other.py": "VALUE = 3\n"})
    sha = git(project.root, "rev-parse", "unreviewed")
    with pytest.raises(runtime_source.DeployError, match="standing independent"):
        runtime_unmerged.approve(project.root, sha, "unreviewed")


def test_a_helper_cannot_use_lead_approval(reviewed, monkeypatch):
    project, node, lead, _, sha, branch = reviewed
    member = node.member("helper", parent=lead)
    monkeypatch.setenv(authority.AGENT_ENV, member.name)
    with pytest.raises(runtime_source.DeployError, match="live lead"):
        runtime_unmerged.approve(project.root, sha, branch)


def test_a_retired_lead_cannot_use_its_own_approval_or_ask_for_one(reviewed):
    project, node, lead, _, sha, branch = reviewed
    granted = approval(reviewed)
    node.session(lead).retire()
    with pytest.raises(runtime_source.DeployError, match="unavailable"):
        runtime_source.admit(project.root, sha, allow_unmerged=granted)
    with pytest.raises(runtime_source.DeployError, match="unavailable"):
        runtime_unmerged.approve(project.root, sha, branch)


def test_a_session_other_than_the_named_actor_cannot_use_the_approval(reviewed, monkeypatch):
    project, node, lead, _, sha, branch = reviewed
    granted = approval(reviewed)
    node.member("stand-in")
    monkeypatch.setenv(board_session.SUBAGENT_ENV, "stand-in")
    monkeypatch.setenv(authority.AGENT_ENV, lead.name)
    with pytest.raises(runtime_source.DeployError, match="live lead"):
        runtime_unmerged.validate(granted, project.root, sha)
    with pytest.raises(runtime_source.DeployError, match="live lead"):
        runtime_unmerged.approve(project.root, sha, branch)


def test_a_session_started_by_the_claim_holder_does_not_make_a_review_stand(reviewed):
    project, node, _, _, _, _ = reviewed
    author = node.members["source-author"]
    helper = node.member("author-helper", parent=author)
    project.branch("helped", {"helped.py": "VALUE = 4\n"})
    sha = git(project.root, "rev-parse", "helped")
    node.session(author).claim("branch", "helped")
    with pytest.raises(Denied):
        landing.review(node.session(helper), "helped", sha, "accept")
    with pytest.raises(runtime_source.DeployError, match="standing independent"):
        runtime_unmerged.approve(project.root, sha, "helped")


def test_offline_node_cannot_reuse_approval(reviewed):
    project, node, _, _, sha, _ = reviewed
    granted = approval(reviewed)
    node.stop()
    with pytest.raises(runtime_source.DeployError, match="unavailable"):
        runtime_source.admit(project.root, sha, allow_unmerged=granted)


def test_switch_rechecks_review_before_publishing_selection(reviewed, tmp_path):
    project, node, _, reviewer, sha, branch = reviewed
    granted = approval(reviewed)
    plan = runtime_deploy.Plan(project.root, sha, None, allow_unmerged=granted)
    chosen = runtime.Runtime(tmp_path / "build", sha, "0", runtime.identity())
    landing.review(node.session(reviewer), branch, sha, "reject")
    with pytest.raises(runtime_source.DeployError, match="standing independent"):
        runtime_deploy.switch(chosen, plan)
    assert not (runtime.directory() / "selected.json").exists()


def test_node_admission_preserves_and_rechecks_the_exact_approval(reviewed, tmp_path):
    project, node, _, reviewer, sha, branch = reviewed
    granted = approval(reviewed)
    prefix = tmp_path / "build"
    record = node_binary.install(prefix, node.binary, commit=sha)
    binary = node_binary.location(prefix)
    node_supervise._admitted(binary, record["sha256"], project.root, allow_unmerged_commit=granted)
    landing.review(node.session(reviewer), branch, sha, "reject")
    with pytest.raises(runtime_source.DeployError, match="standing independent"):
        node_supervise._admitted(binary, record["sha256"], project.root, allow_unmerged_commit=granted)
    with pytest.raises(runtime_source.DeployError, match="exact reviewed"):
        node_supervise._admitted(binary, record["sha256"], project.root, allow_unmerged_commit=sha)


def cli_args(reviewed, *extra):
    project, _, _, _, sha, branch = reviewed
    args = runtime_cli.GROUP.parser().parse_args([
        "ensure", "--checkout", str(project.root), "--ref", sha, "--allow-unmerged", "--review-branch", branch,
        *extra])
    args.via = authority.DELEGATED
    return args


def test_cli_builds_a_typed_exact_reviewed_plan(reviewed):
    plan = runtime_cli.plan_from(cli_args(reviewed))
    assert isinstance(plan.allow_unmerged, runtime_unmerged.Approval)
    assert plan.allow_unmerged.commit == plan.commit == reviewed[4]


@pytest.mark.parametrize("extra", [("--ref", "HEAD"), ("--ref", "a" * 7), ("--agent", "another-agent")])
def test_cli_rejects_aliases_short_shas_and_other_actors(reviewed, extra):
    with pytest.raises(runtime_source.DeployError, match="full commit SHA"):
        runtime_cli.plan_from(cli_args(reviewed, *extra))


def test_cli_rejects_background_reviewed_deployment(reviewed):
    with pytest.raises(runtime_source.DeployError, match="foreground"):
        runtime_cli._ensure(cli_args(reviewed, "--background"))


def test_node_rejects_a_different_published_commit_with_typed_approval(reviewed, tmp_path):
    project, node, _, _, _, _ = reviewed
    granted = approval(reviewed)
    published = git(project.root, "rev-parse", "HEAD")
    prefix = tmp_path / "published"
    record = node_binary.install(prefix, node.binary, commit=published)
    with pytest.raises(runtime_source.DeployError, match="does not match the reviewed"):
        node_supervise._admitted(node_binary.location(prefix), record["sha256"], project.root,
                                  allow_unmerged_commit=granted)


def test_reviewed_archive_ignores_replacements_attributes_config_and_path(reviewed, tmp_path, monkeypatch):
    project, _, _, _, sha, _ = reviewed
    granted = approval(reviewed)
    project.branch("replacement", {"extra.py": "MALICIOUS = True\n"})
    replacement = git(project.root, "rev-parse", "replacement")
    git(project.root, "replace", sha, replacement)
    (project.root / ".git/info/attributes").write_text("extra.py export-ignore\n")
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    marker = tmp_path / "executed"
    command = hostile / "git"
    command.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    command.chmod(0o700)
    expected_lock = worktreerules.landing_lock(project.root)
    monkeypatch.setenv("PATH", str(hostile))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(command))
    runtime_source.admit(project.root, sha, allow_unmerged=granted)
    assert runtime_git.boundary(project.root) == expected_lock
    original = runtime_wheel._run
    stage = tmp_path / "stage"
    stage.mkdir()

    def inspect_snapshot(argv, timeout):
        if "wheel" in argv:
            assert (stage / "source/extra.py").read_text() == "VALUE = 2\n"
            raise RuntimeError("snapshot inspected")
        return original(argv, timeout)

    monkeypatch.setattr(runtime_wheel, "_run", inspect_snapshot)
    with pytest.raises(RuntimeError, match="snapshot inspected"):
        runtime_wheel.build(project.root, sha, stage, timeout=15)
    assert not marker.exists()

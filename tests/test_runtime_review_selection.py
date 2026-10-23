"""A runtime resolves convenient refs once and reuses only an exact independent review."""

import os
from argparse import Namespace

import pytest
from git_world import git
from test_runtime_unmerged import cli_args, reviewed as _reviewed

from poolhouse import authority, home, runtime_cli, runtime_source, runtime_trust, runtime_unmerged
from poolhouse.workspace import landing

pytest_plugins = ["node_kit"]
reviewed = _reviewed


@pytest.mark.parametrize("ref", ["HEAD", "short"])
def test_reviewed_cli_resolves_a_ref_to_the_exact_reviewed_commit(reviewed, ref):
    project, _, _, _, sha, _ = reviewed
    git(project.root, "merge", "--ff-only", sha)
    supplied = sha[:12] if ref == "short" else ref
    plan = runtime_cli.plan_from(cli_args(reviewed, "--ref", supplied))
    assert plan.commit == plan.allow_unmerged.commit == sha


def test_reviewed_cli_finds_the_existing_review_without_repeating_its_branch(reviewed):
    args = cli_args(reviewed)
    args.review_branch = ""
    plan = runtime_cli.plan_from(args)
    assert plan.allow_unmerged.branch == reviewed[5]
    assert plan.commit == plan.allow_unmerged.commit == reviewed[4]


def test_replanning_keeps_the_first_resolved_ref_and_exact_approval(reviewed):
    project, _, _, _, sha, _ = reviewed
    git(project.root, "merge", "--ff-only", sha)
    args = cli_args(reviewed, "--ref", "HEAD")
    first = runtime_cli.plan_from(args)
    project.write(project.root, "later.py", "VALUE = 3\n")
    project.commit(project.root, "chore: newer development tip")
    second = runtime_cli.plan_from(args)
    assert first.commit == second.commit == sha
    assert first.allow_unmerged == second.allow_unmerged


def test_replanning_never_substitutes_another_branch_when_the_chosen_one_moves(reviewed):
    project, node, _, _, sha, branch = reviewed
    project.branch("another-reviewed", {}, start=branch)
    reviewer = node.member("another-reviewer")
    landing.review(node.session(reviewer), "another-reviewed", sha, "accept")
    args = cli_args(reviewed)
    args.review_branch = ""
    first = runtime_cli.plan_from(args)
    git(project.root, "update-ref", f"refs/heads/{first.allow_unmerged.branch}", git(project.root, "rev-parse", "HEAD"))
    with pytest.raises(runtime_source.DeployError, match="branch changed"):
        runtime_cli.plan_from(args)


def test_replanning_never_rebinds_an_approval_to_a_new_actor(reviewed, monkeypatch):
    _, node, _, _, _, _ = reviewed
    args = cli_args(reviewed)
    runtime_cli.plan_from(args)
    monkeypatch.setenv(authority.AGENT_ENV, node.member("other-runtime-owner").name)
    with pytest.raises(runtime_source.DeployError, match="another agent"):
        runtime_cli.plan_from(args)


def test_equivalent_exact_reviews_are_selected_deterministically(reviewed):
    project, node, _, _, sha, _ = reviewed
    project.branch("another-reviewed", {}, start="reviewed-source")
    reviewer = node.member("another-reviewer")
    landing.review(node.session(reviewer), "another-reviewed", sha, "accept")
    first = runtime_unmerged.approve(project.root, sha, "")
    second = runtime_unmerged.approve(project.root, sha, "")
    assert first == second
    assert first.branch == "another-reviewed"


def test_a_reject_on_another_branch_still_stops_inferred_review(reviewed):
    project, node, _, _, sha, _ = reviewed
    project.branch("rejected-alias", {}, start="reviewed-source")
    reviewer = node.member("rejecting-reviewer")
    landing.review(node.session(reviewer), "rejected-alias", sha, "reject")
    with pytest.raises(runtime_source.DeployError, match=r"independent.*reject"):
        runtime_unmerged.approve(project.root, sha, "")


def test_inference_never_substitutes_a_moved_branch_or_another_commit(reviewed):
    project, _, _, _, sha, branch = reviewed
    git(project.root, "update-ref", f"refs/heads/{branch}", git(project.root, "rev-parse", "HEAD"))
    with pytest.raises(runtime_source.DeployError, match="standing independent"):
        runtime_unmerged.approve(project.root, sha, "")


def test_a_deployers_advisory_review_cannot_satisfy_inference(reviewed):
    project, node, lead, _, _, _ = reviewed
    project.branch("own-unpublished", {"own.py": "VALUE = 1\n"})
    sha = git(project.root, "rev-parse", "own-unpublished")
    landing.review(node.session(lead), "own-unpublished", sha, "accept")
    with pytest.raises(runtime_source.DeployError, match="standing independent"):
        runtime_unmerged.approve(project.root, sha, "")


def test_an_ended_for_cause_reviewer_cannot_supply_an_inferred_branch(reviewed):
    project, node, _, _, sha, branch = reviewed
    project.branch("another-reviewed", {}, start=branch)
    parent = node.member("review-parent")
    reviewer = node.member("ended-reviewer", parent=parent)
    landing.review(node.session(reviewer), "another-reviewed", sha, "accept")
    node.session(parent).retire(reviewer.name, "forged")
    granted = runtime_unmerged.approve(project.root, sha, "")
    assert granted.branch == branch


@pytest.mark.parametrize("ref", ["--output=elsewhere", "HEAD^{tree}", "missing-ref"])
def test_malicious_or_noncommit_refs_never_select_runtime_source(reviewed, ref):
    args = cli_args(reviewed)
    args.ref = ref
    with pytest.raises(runtime_source.DeployError):
        runtime_cli.plan_from(args)


def test_equivalent_inherited_state_root_is_removed_without_warning(tmp_path, monkeypatch, capsys):
    state = tmp_path / "account" / ".poolhouse"
    monkeypatch.setattr(home, "account_roots", lambda: (state, tmp_path / "cache"))
    monkeypatch.setenv("POOLHOUSE_AGENT", "1")
    monkeypatch.setenv(home.ROOT_ENV, str(state))
    args = Namespace(cmd="status")
    assert runtime_cli._reported(lambda _args: 0)(args) == 0
    assert "ignoring POOLHOUSE_HOME" not in capsys.readouterr().err
    assert home.ROOT_ENV not in os.environ


def test_conflicting_state_root_stays_ignored_and_warns_without_its_value(tmp_path, monkeypatch, capsys):
    state = tmp_path / "account" / ".poolhouse"
    conflict = tmp_path / "private-value"
    monkeypatch.setattr(home, "account_roots", lambda: (state, tmp_path / "cache"))
    monkeypatch.setenv("POOLHOUSE_AGENT", "1")
    monkeypatch.setenv(home.ROOT_ENV, str(conflict))
    assert home.ROOT_ENV in runtime_trust.ignore_redirects()
    monkeypatch.setenv(home.ROOT_ENV, str(conflict))
    assert runtime_cli._reported(lambda _args: 0)(Namespace(cmd="status")) == 0
    output = capsys.readouterr().err
    assert "ignoring POOLHOUSE_HOME" in output and str(conflict) not in output

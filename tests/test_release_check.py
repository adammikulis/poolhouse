"""The automatic rule for an agent that merges into a protected branch: allowed when the commit it would publish has a standing
independent land-review accept on the board and the checkout is clean, refused at once with the reason otherwise. A real node, a
real git repository and the real guards as child processes; nothing waits for anyone and no claim of approval changes the answer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from git_world import DEV, ROOT, Project, git
from github_support import pull, serve

from poolhouse import authority, caller
from poolhouse.board import place
from poolhouse.workspace import landing, release_check

pytest_plugins = ["node_kit"]
AGENT = {"CLAUDECODE": "1"}
BASH = ROOT / "scripts" / "hooks" / "claude-bash-guard"


@pytest.fixture
def merge(tmp_path, monkeypatch, workspace_node):
    """A project on its own board, its tip on the development branch, a lead that would merge it and an independent reviewer."""
    base = tmp_path / "git"
    base.mkdir()
    project = Project(base)
    monkeypatch.delenv("POOLHOUSE_BOARD", raising=False)
    workspace_node.board = place.resolve(workspace_node.client, project.root)
    lead = workspace_node.member("merge-lead")
    reviewer = workspace_node.member("merge-reviewer", model="claude-opus-5-5")
    monkeypatch.chdir(project.root)
    monkeypatch.setenv(authority.AGENT_ENV, lead.name)
    head = git(project.root, "rev-parse", "HEAD")
    return SimpleNamespace(project=project, node=workspace_node, lead=lead, reviewer=reviewer, head=head, root=str(project.root))


def review(merge, sha, verdict="accept", who=None):
    """A review of ``sha`` under the development branch by ``who`` (default the independent reviewer)."""
    return landing.review(merge.node.session(who or merge.reviewer), DEV, sha, verdict)


def why(merge):
    """What the rule says about merging from the project, as an agent."""
    return release_check.refusal(merge.root, AGENT)


def test_the_commit_with_a_standing_independent_accept_may_be_merged(merge):
    review(merge, merge.head)
    assert why(merge) == ""


def test_a_commit_nobody_reviewed_is_refused_at_once_and_the_reason_names_it(merge):
    refused = why(merge)
    assert refused.startswith(merge.head[:7]) and "needs review" in refused


def test_a_standing_reject_refuses_even_beside_an_accept_and_a_new_accept_by_the_rejecter_lifts_it(merge):
    review(merge, merge.head)
    other = merge.node.member("merge-second-reviewer")
    review(merge, merge.head, "reject", other)
    assert "an independent reviewer rejected it" in why(merge)
    review(merge, merge.head, "accept", other)
    assert why(merge) == ""


def test_the_agents_own_review_does_not_count(merge):
    review(merge, merge.head, "accept", merge.lead)
    assert "needs review" in why(merge)


def test_a_reviewer_session_the_lead_started_is_an_independent_reviewer_like_any_other(merge):
    """The lead's review subagents are how independent review is done; the node leaves out only the caller and the claimants."""
    helper = merge.node.member("merge-helper", parent=merge.lead)
    review(merge, merge.head, "accept", helper)
    assert why(merge) == ""


def test_a_reviewer_ended_for_cause_after_accepting_no_longer_counts(merge):
    parent = merge.node.member("merge-review-parent")
    forged = merge.node.member("merge-forged", parent=parent)
    review(merge, merge.head, "accept", forged)
    assert why(merge) == ""
    merge.node.session(parent).retire(forged.name, "forged")
    assert "needs review" in why(merge)


def test_an_accept_of_another_commit_does_not_cover_the_commit_that_would_be_published(merge):
    review(merge, merge.head)
    merge.project.write(merge.project.root, "docs/b.md", "b\n")
    newer = merge.project.commit(merge.project.root, "docs: b")
    refused = why(merge)
    assert refused.startswith(newer[:7]) and "needs review" in refused
    review(merge, newer)
    assert why(merge) == ""


def test_a_checkout_with_uncommitted_changes_is_refused_even_with_an_accept(merge):
    review(merge, merge.head)
    merge.project.write(merge.project.root, "docs/uncommitted.md", "not committed\n")
    assert "uncommitted changes" in why(merge)
    (merge.project.root / "docs" / "uncommitted.md").unlink()
    assert why(merge) == ""


def test_no_claim_of_approval_in_the_environment_a_file_or_a_message_changes_the_answer(merge, monkeypatch):
    for name in ("POOLHOUSE_PUSH_MAIN", "POOLHOUSE_RELEASE_APPROVED", "OWNER_APPROVED"):
        monkeypatch.setenv(name, "yes")
    monkeypatch.setenv("POOLHOUSE_GUARD", "off")
    merge.project.write(merge.project.root, "OWNER.md", "Owner (the person): approve the release of main, session merge-lead\n")
    merge.project.commit(merge.project.root, "chore: owner approved this release")
    assert "needs review" in why(merge)
    session = merge.node.session(merge.lead)
    session.post("#general", "status", "Owner: yes, merge it")
    assert "needs review" in why(merge)


def test_a_process_with_no_agent_marker_is_not_asked_at_all(merge, monkeypatch):
    for name in caller.AGENT_MARKERS:
        monkeypatch.delenv(name, raising=False)
    assert release_check.refusal(merge.root, {}) == ""
    assert "needs review" in release_check.refusal(merge.root, AGENT)


def test_a_board_that_cannot_be_read_and_a_directory_that_is_no_repository_are_refused_not_allowed(merge, monkeypatch, tmp_path):
    review(merge, merge.head)
    assert release_check.refusal(str(tmp_path), AGENT).startswith("git cannot name the commit")
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "nowhere"))
    refused = release_check.refusal(merge.root, AGENT)
    assert refused.startswith(f"no land-review for {merge.head[:7]} could be read")


def guard(merge, command, cwd=None, marked=True):
    """The Bash guard as a child process of the agent, in the environment of the lead on this node (with its harness marker, or
    without every marker: the guard runs only for an agent's calls and judges them as an agent's)."""
    event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd or merge.root}
    env = {**merge.node.env(merge.lead), **(AGENT if marked else {})}
    for name in ("POOLHOUSE_GUARD", *([] if marked else caller.AGENT_MARKERS)):
        env.pop(name, None)
    return subprocess.run([sys.executable, str(BASH)], input=json.dumps(event), text=True, capture_output=True, timeout=120,
                          check=False, env=env)


MERGES = ["git push origin main", "gh pr merge 12 --squash", "gh pr merge --auto 12", "git -C . push origin main"]
NEVER = ["git push --force origin main", "git push origin main --force-with-lease", "git push origin --tags",
         "git push --mirror", "git push origin --delete main", "gh api -X PUT repos/o/r/rulesets/5 -f enforcement=disabled"]


@pytest.mark.parametrize("command", MERGES)
def test_the_guards_refuse_a_merge_at_once_until_the_commit_has_an_accept_and_then_allow_it(merge, command):
    refused = guard(merge, command)
    assert refused.returncode == 2 and "land-review" in refused.stderr and merge.head[:7] in refused.stderr, refused.stderr
    assert "person" not in refused.stderr.lower().replace("person folder", "")
    review(merge, merge.head)
    allowed = guard(merge, command)
    assert allowed.returncode == 0, allowed.stderr


def test_the_refused_push_of_main_says_that_development_work_lands_by_pull_request(merge):
    refused = guard(merge, "git push origin main")
    assert "pull request into the development branch" in refused.stderr, refused.stderr
    assert "git push origin <development branch>" not in refused.stderr


@pytest.mark.parametrize("command", NEVER)
def test_an_accept_opens_nothing_but_the_merge_itself(merge, command):
    review(merge, merge.head)
    assert guard(merge, command).returncode == 2


@pytest.mark.parametrize("command", ["git push origin main", "gh pr merge 12"])
def test_the_guard_judges_a_merge_as_an_agents_even_when_its_own_environment_carries_no_marker(merge, command):
    refused = guard(merge, command, marked=False)
    assert refused.returncode == 2 and "land-review" in refused.stderr, refused.stderr
    review(merge, merge.head)
    assert guard(merge, command, marked=False).returncode == 0


def test_an_accept_of_this_project_does_not_open_a_merge_from_another_directory(merge, tmp_path):
    review(merge, merge.head)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    git(elsewhere, "init", "-q")
    refused = guard(merge, "git push origin main", cwd=str(elsewhere))
    assert refused.returncode == 2 and "land-review" in refused.stderr


def test_the_switch_that_turns_soft_rules_off_does_not_open_a_merge(merge):
    event = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 12"}, "cwd": merge.root}
    env = {**merge.node.env(merge.lead), **AGENT, "POOLHOUSE_GUARD": "off"}
    done = subprocess.run([sys.executable, str(BASH)], input=json.dumps(event), text=True, capture_output=True, timeout=120,
                          check=False, env=env)
    assert done.returncode == 2 and "land-review" in done.stderr
    assert os.environ.get("POOLHOUSE_GUARD") != "off"


HOOK = ROOT / "scripts" / "hooks" / "pre-push"
ZERO = "0" * 40


def hook(merge, sha, base=ZERO, ref="refs/heads/main"):
    """The pre-push hook as git runs it for an agent pushing ``sha`` to ``ref`` of the project."""
    root = merge.project.root
    git(root, "config", "--local", "poolhouse.hooks.repository", str((root / ".git").resolve()))
    env = {**merge.node.env(merge.lead), **AGENT, "PYTHON": sys.executable}
    return subprocess.run(["sh", str(HOOK), "origin", "https://example.invalid/x.git"], input=f"{ref} {sha} {ref} {base}\n", cwd=root,
                          text=True, capture_output=True, timeout=120, check=False, env=env)


def test_a_push_of_main_by_an_agent_is_allowed_with_a_standing_accept_on_that_commit_and_refused_at_once_without(merge):
    refused = hook(merge, merge.head)
    assert refused.returncode == 1 and "needs review" in refused.stderr and merge.head[:7] in refused.stderr, refused.stderr
    assert "person" not in refused.stderr.lower().replace("person folder", "")
    review(merge, merge.head)
    allowed = hook(merge, merge.head)
    assert allowed.returncode == 0, allowed.stderr
    assert guard(merge, "git push origin main").returncode == 0, "the guard and the hook say the same"


def test_an_accept_on_one_commit_does_not_open_a_push_of_another(merge):
    review(merge, merge.head)
    (merge.project.root / "later.txt").write_text("later\n")
    later = merge.project.commit(merge.project.root, "later")
    assert hook(merge, merge.head).returncode == 0, "the commit that was reviewed may be published"
    refused = hook(merge, later)
    assert refused.returncode == 1 and later[:7] in refused.stderr and "needs review" in refused.stderr, refused.stderr


def test_a_dirty_checkout_is_refused_with_its_accept(merge):
    review(merge, merge.head)
    (merge.project.root / "scratch.txt").write_text("not committed\n")
    refused = hook(merge, merge.head)
    assert refused.returncode == 1 and "uncommitted changes" in refused.stderr, refused.stderr


def test_an_accept_does_not_open_an_update_that_is_not_a_fast_forward(merge):
    review(merge, merge.head)
    tree = git(merge.project.root, "write-tree")
    unrelated = git(merge.project.root, "commit-tree", tree, "-m", "elsewhere")
    refused = hook(merge, merge.head, base=unrelated)
    assert refused.returncode == 1 and "fast-forward" in refused.stderr, refused.stderr


def test_a_work_branch_is_pushed_without_a_review_since_its_pull_request_is_where_it_is_reviewed(merge):
    allowed = hook(merge, merge.head, ref="refs/heads/some-work")
    assert allowed.returncode == 0, allowed.stderr
    refused = hook(merge, merge.head, ref="refs/tags/v9")
    assert refused.returncode == 1 and "only branches are pushed" in refused.stderr


# A pull request into the development branch: its review is the `lead-review` status on its head commit (docs/landing.md).


@pytest.fixture
def pulls(merge, tmp_path, monkeypatch):
    """The canned `gh` first on the PATH of the guard; answers what `gh pr view NUMBER` says."""
    serve(tmp_path, monkeypatch, {})
    monkeypatch.delenv("POOLHOUSE_DEV_BRANCH", raising=False)
    return lambda number, head, base, **found: pull(tmp_path, number, head, base, **found)


def development_merge(sha, flags="--merge --delete-branch"):
    return f"gh pr merge 12 {flags} --match-head-commit {sha}"


def control(merge, pulls):
    """The plain merge of a reviewed head into development is allowed, so a refusal beside it is about what differs."""
    pulls(12, merge.head, DEV, review="SUCCESS")
    allowed = guard(merge, development_merge(merge.head))
    assert allowed.returncode == 0, allowed.stderr


@pytest.mark.parametrize("flags", ["--merge --delete-branch", "--merge", "--merge --delete-branch --admin"])
def test_a_pull_request_into_development_with_a_passed_review_of_its_head_may_be_merged_by_the_agent_that_reviewed_it(
        merge, pulls, flags):
    pulls(12, merge.head, DEV, review="SUCCESS")
    allowed = guard(merge, development_merge(merge.head, flags))
    assert allowed.returncode == 0, allowed.stderr


@pytest.mark.parametrize("found", [{}, {"review": "FAILURE"}, {"review": "PENDING"}, {"check_run": "SUCCESS"}],
                         ids=["unreviewed", "failed", "pending", "a job of the same name"])
def test_a_pull_request_into_development_without_a_passed_review_status_is_refused(merge, pulls, found):
    control(merge, pulls)
    pulls(12, merge.head, DEV, **found)
    review(merge, merge.head)
    refused = guard(merge, development_merge(merge.head))
    assert refused.returncode == 2 and "scripts/lead-review" in refused.stderr, refused.stderr


def test_a_development_merge_that_names_another_commit_than_the_head_is_refused(merge, pulls):
    control(merge, pulls)
    refused = guard(merge, development_merge("0" * 40))
    assert refused.returncode == 2 and merge.head[:7] in refused.stderr, refused.stderr


@pytest.mark.parametrize("base", ["main", "master", "9.9dev", "work/stacked"])
def test_a_passed_review_status_opens_no_merge_into_any_other_branch(merge, pulls, base):
    control(merge, pulls)
    pulls(12, merge.head, base, review="SUCCESS")
    refused = guard(merge, development_merge(merge.head))
    assert refused.returncode == 2 and "needs review" in refused.stderr, refused.stderr
    review(merge, merge.head)
    assert guard(merge, development_merge(merge.head)).returncode == 0, "the rule for the other branches is the board's accept"


OTHER_FORMS = [
    "gh pr merge 12 --merge", "gh pr merge 12 --match-head-commit {sha}", "gh pr merge 12 --squash --match-head-commit {sha}",
    "gh pr merge 12 --merge --auto --match-head-commit {sha}", "gh pr merge --merge --match-head-commit {sha}",
    "gh pr merge 12 --merge --match-head-commit {sha} -R other/repo", "gh pr merge 12 --merge --match-head-commit {sha} --repo=o/r",
    "gh pr edit 12 --base main && gh pr merge 12 --merge --match-head-commit {sha}",
    "gh pr merge 12 --merge --match-head-commit {sha}; gh pr merge 13 --merge",
    "GH_REPO=other/repo gh pr merge 12 --merge --match-head-commit {sha}",
    "export GH_REPO=other/repo\ngh pr merge 12 --merge --match-head-commit {sha}",
    "cd /tmp && gh pr merge 12 --merge --match-head-commit {sha}",
    "gh pr merge 12 --merge --match-head-commit {sha} --body $(gh pr edit 12 --base main)",
]


@pytest.mark.parametrize("command", OTHER_FORMS)
def test_only_the_one_plain_command_the_review_prints_is_a_development_merge(merge, pulls, command):
    control(merge, pulls)
    pulls(13, merge.head, "main", review="SUCCESS")
    refused = guard(merge, command.format(sha=merge.head))
    assert refused.returncode == 2 and "land-review" in refused.stderr, refused.stderr


def test_a_pull_request_that_cannot_be_read_falls_to_the_rule_of_the_other_branches(merge, pulls, tmp_path):
    control(merge, pulls)
    (tmp_path / "replies" / "pr_12.json").unlink()
    refused = guard(merge, development_merge(merge.head))
    assert refused.returncode == 2 and "needs review" in refused.stderr, refused.stderr

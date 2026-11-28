"""scripts/lead-review holds a pull request while a reject of any commit it would merge stands on the board. A real node, a real git
repository and the script as a child process; `gh` answers canned replies (tests/github_support.py), so nothing reaches GitHub."""

from __future__ import annotations

import json

from git_world import DEV, git
from github_support import pull
from test_lead_review import BRANCH, reviews, run, world  # noqa: F401  (a fixture)

from poolhouse.workspace import landing

pytest_plugins = ["node_kit"]


def series(world, tmp_path) -> tuple[str, str]:  # noqa: F811
    """A pull request of two commits on a branch of its own: the earlier and the head."""
    where = world.project.branch(BRANCH, {"docs/b.md": "first\n"})
    first = git(where, "rev-parse", "HEAD")
    world.project.write(where, "docs/c.md", "second\n")
    tip = world.project.commit(where, "feat: second")
    pull(tmp_path, 12, tip, DEV, author_agent=world.author.name)
    return first, tip


def say(world, who, sha: str, verdict: str) -> None:  # noqa: F811
    landing.review(world.node.session(who), BRANCH, sha, verdict)


def statuses(world) -> list[tuple[str, str]]:  # noqa: F811
    """Every status posted, as (commit, state)."""
    return [(path.rsplit("/", 1)[1], json.loads(sent)["state"]) for kind, path, sent in world.writes() if kind == "POST"]


def held(world, done, tip: str, *named: str) -> None:  # noqa: F811
    assert done.returncode == 1, done.stdout
    assert all(each in done.stderr for each in named), done.stderr
    assert "gh pr merge" not in done.stdout
    assert statuses(world)[-1] == (tip, "failure"), "the status the merge needs is failed"


def test_another_reviewers_reject_of_an_earlier_commit_holds_the_merge_until_that_reviewer_accepts_it(world, tmp_path):  # noqa: F811
    first, tip = series(world, tmp_path)
    say(world, world.other, first, "reject")
    done = run(world, world.lead, sha=tip)
    held(world, done, tip, first[:12], world.other.name)
    assert [call[0] for call in world.writes()] == ["POST"], "no comment says it passed"
    observer = world.node.member("pr-second-observer")
    assert landing.accepts(world.node.session(observer), tip)["reviews"] == [], "and no accept is on the board"
    say(world, world.other, first, "accept")
    done = run(world, world.lead, sha=tip)
    assert done.returncode == 0, done.stderr
    assert statuses(world)[-1] == (tip, "success") and f"--match-head-commit {tip}" in done.stdout


def test_a_reject_of_the_head_posted_after_an_accept_fails_the_status_on_the_next_accept(world, tmp_path):  # noqa: F811
    _, tip = series(world, tmp_path)
    assert run(world, world.lead, sha=tip).returncode == 0
    assert statuses(world) == [(tip, "success")]
    say(world, world.other, tip, "reject")
    held(world, run(world, world.lead, sha=tip), tip, tip[:12], world.other.name)


def test_the_reviewers_own_earlier_reject_is_replaced_by_its_accept_of_the_new_head(world, tmp_path):  # noqa: F811
    first, tip = series(world, tmp_path)
    say(world, world.lead, first, "reject")
    done = run(world, world.lead, sha=tip)
    assert done.returncode == 0, done.stderr
    assert statuses(world) == [(tip, "success")]


def test_a_reject_of_a_commit_the_pull_request_would_not_merge_holds_nothing(world, tmp_path):  # noqa: F811
    say(world, world.other, world.head, "reject")   # the commit the development branch already holds
    _, tip = series(world, tmp_path)
    elsewhere = git(world.project.branch("work/elsewhere", {"docs/d.md": "d\n"}), "rev-parse", "HEAD")
    say(world, world.other, elsewhere, "reject")
    done = run(world, world.lead, sha=tip)
    assert done.returncode == 0, done.stderr


def test_a_head_this_repository_lacks_is_fetched_from_the_pull_request_before_it_is_judged(world, tmp_path):  # noqa: F811
    clone = tmp_path / "elsewhere"
    git(tmp_path, "clone", "-q", str(world.project.origin), str(clone))
    git(clone, "config", "user.name", "Test Agent")
    git(clone, "config", "user.email", "agent@example.invalid")
    world.project.write(clone, "docs/f.md", "first\n")
    first = world.project.commit(clone, "feat: first")
    world.project.write(clone, "docs/g.md", "second\n")
    tip = world.project.commit(clone, "feat: second")
    git(clone, "push", "-q", "origin", "HEAD:refs/pull/12/head")
    pull(tmp_path, 12, tip, DEV, author_agent=world.author.name)
    say(world, world.other, first, "reject")
    held(world, run(world, world.lead, sha=tip), tip, first[:12])


def test_commits_that_cannot_be_read_are_never_accepted(world, tmp_path):  # noqa: F811
    missing = "1" * 40
    pull(tmp_path, 12, missing, DEV, author_agent=world.author.name)
    done = run(world, world.lead, sha=missing)
    assert done.returncode == 1 and "gh pr merge" not in done.stdout, done.stdout
    assert world.writes() == [] and reviews(world) == []


def test_a_development_branch_that_cannot_be_fetched_accepts_nothing(world):  # noqa: F811
    git(world.project.root, "remote", "set-url", "origin", str(world.project.base / "gone.git"))
    done = run(world, world.lead)
    assert done.returncode == 1 and "gh pr merge" not in done.stdout, done.stdout
    assert world.writes() == [] and reviews(world) == []


def test_a_reject_is_recorded_whatever_stands(world, tmp_path):  # noqa: F811
    first, tip = series(world, tmp_path)
    say(world, world.other, first, "reject")
    done = run(world, world.lead, "reject", sha=tip)
    assert done.returncode == 0, done.stderr
    assert statuses(world) == [(tip, "failure")]

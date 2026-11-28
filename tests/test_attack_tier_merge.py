"""In a repository that asks for review tiers, a change that needs an attack review merges only with an accepting attack verdict of
that head from an independent agent that is not the one merging. A real node, a real git repository, `scripts/lead-review`, the Bash
guard and the release check; `gh` answers canned replies (tests/github_support.py), so nothing reaches GitHub."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest
from git_world import DEV, git
from github_support import pull
from test_lead_review import BRANCH, GUARD, env, run, world  # noqa: F401  (`world` is a fixture)

from poolhouse.workspace import landing, merge_review, release_check

pytest_plugins = ["node_kit"]
AGENT = {"CLAUDECODE": "1"}
SCRIPT = GUARD.parents[1] / "lead-review"
RISKY = "scripts/lead-review"
MARKER = "scripts/hooks/primary-only"


@pytest.fixture
def tiers(world, tmp_path, monkeypatch):  # noqa: F811
    """A repository that asks for review tiers; pull request 12 opens ``files`` on a branch into ``base``."""
    monkeypatch.setenv("POOLHOUSE_HOOK_STATE", str(tmp_path / "hooks"))
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", DEV)
    world.project.advance(MARKER, "")
    git(world.project.root, "push", "-q", "origin", f"{DEV}:main")
    world.tmp = tmp_path
    world.state = tmp_path / "hooks"

    def opens(files, branch=BRANCH, base=DEV, author=None):
        tree = world.project.branch(branch, files)
        world.tree, world.branch, world.base, world.author_name = tree, branch, base, (author or world.author).name
        world.head = git(tree, "rev-parse", "HEAD")
        shows()
        return world.head

    def shows(review="", **found):
        pull(tmp_path, 12, world.head, world.base, author_agent=world.author_name, review=review, **found)
        reply = tmp_path / "replies" / "pr_12.json"
        reply.write_text(json.dumps({**json.loads(reply.read_text()), "headRefName": world.branch}))

    def push(files):
        for rel, text in files.items():
            world.project.write(world.tree, rel, text)
        world.head = world.project.commit(world.tree, "fix: more")
        shows()
        return world.head

    world.opens, world.shows, world.push = opens, shows, push
    return world


def script(world, who, *words):  # noqa: F811
    argv = [sys.executable, str(SCRIPT), "12", world.head, *words]
    return subprocess.run(argv, cwd=world.project.root, env=env(world, who), capture_output=True, text=True, timeout=120, check=False)


def review(world, who, verdict="accept", *more):  # noqa: F811
    return script(world, who, "--verdict", verdict, "--note", "Read the diff.", *more)


def attack(world, who, verdict="accept", *more):  # noqa: F811
    return review(world, who, verdict, "--attack", *more)


def guard(world, who, command="", agent=""):  # noqa: F811
    """The Bash guard on the merge command the review prints, as the session ``who``; ``agent`` names the subagent running it."""
    event = {"tool_name": "Bash", "cwd": str(world.project.root),
             "tool_input": {"command": command or f"gh pr merge 12 --merge --delete-branch --match-head-commit {world.head}"}}
    if agent:
        record = world.state / "subagents" / (hashlib.sha256(agent.encode()).hexdigest()[:24] + ".json")
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"agent_id": agent, "name": agent}))
        event["agent_id"] = agent
    return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(event), env=env(world, who), text=True,
                          capture_output=True, timeout=120, check=False)


def statuses(world):  # noqa: F811
    return [json.loads(sent)["state"] for kind, _, sent in world.writes() if kind == "POST"]


def refused(done, *why):
    assert done.returncode in (1, 2), (done.returncode, done.stdout, done.stderr)
    assert all(each in done.stderr for each in why), done.stderr


def test_a_risky_change_is_not_accepted_until_another_agent_gave_the_attack_verdict(tiers):
    tiers.opens({RISKY: "x\n"})
    refused(review(tiers, tiers.lead), "needs an attack review", RISKY, "--attack")
    assert tiers.writes() == [], "nothing is written, so no status can open the merge"
    done = attack(tiers, tiers.sibling)
    assert done.returncode == 0, done.stderr
    assert statuses(tiers) == [], "the attack accept alone sets no status"
    assert "scripts/lead-review 12" in done.stdout and RISKY in done.stdout and "gh pr merge" not in done.stdout
    done = review(tiers, tiers.lead)
    assert done.returncode == 0, done.stderr
    assert statuses(tiers) == ["success"] and f"--match-head-commit {tiers.head}" in done.stdout
    tiers.shows(review="SUCCESS")
    allowed = guard(tiers, tiers.lead)
    assert allowed.returncode == 0, allowed.stderr


def test_a_lighter_change_needs_one_accept(tiers):
    tiers.opens({"docs/b.md": "b\n"})
    done = review(tiers, tiers.lead)
    assert done.returncode == 0 and statuses(tiers) == ["success"], done.stderr
    assert "lighter review" in done.stdout
    tiers.shows(review="SUCCESS")
    assert guard(tiers, tiers.lead).returncode == 0


def test_a_passed_status_without_an_attack_verdict_does_not_merge_a_risky_change(tiers):
    tiers.opens({RISKY: "x\n"})
    tiers.shows(review="SUCCESS")
    landing.review(tiers.node.session(tiers.sibling), merge_review.label(BRANCH), tiers.head, "accept")
    refused(guard(tiers, tiers.lead), "needs an attack review", "--attack")


@pytest.mark.parametrize("who", ["author", "helper", "grandchild", "main-authors-child"])
def test_the_author_and_the_agents_it_started_do_not_give_the_attack_verdict(tiers, who):
    helper = tiers.node.member("pr-helper", parent=tiers.author)
    reviewer = {"author": tiers.author, "helper": helper, "grandchild": tiers.node.member("pr-grandchild", parent=helper),
                "main-authors-child": tiers.sibling}[who]
    tiers.opens({RISKY: "x\n"}, author=tiers.lead if who == "main-authors-child" else tiers.author)
    refused(attack(tiers, reviewer), "its attack accept is not independent")
    assert tiers.writes() == []
    tiers.shows(review="SUCCESS")
    landing.review(tiers.node.session(reviewer), merge_review.label(BRANCH, merge_review.ATTACK), tiers.head, "accept")
    refused(guard(tiers, tiers.other), "needs an attack review")


def test_the_one_merging_does_not_count_its_own_attack_verdict(tiers):
    tiers.opens({RISKY: "x\n"})
    assert attack(tiers, tiers.other).returncode == 0
    tiers.shows(review="SUCCESS")
    refused(guard(tiers, tiers.other), "needs an attack review")
    assert guard(tiers, tiers.lead).returncode == 0, "another agent merges on it"


def test_a_subagent_that_gave_the_attack_verdict_does_not_merge_on_it(tiers):
    tiers.opens({RISKY: "x\n"})
    assert attack(tiers, tiers.sibling).returncode == 0
    tiers.shows(review="SUCCESS")
    assert guard(tiers, tiers.lead).returncode == 0
    refused(guard(tiers, tiers.lead, agent=tiers.sibling.name), "needs an attack review")


def test_an_attack_verdict_covers_only_the_head_it_names(tiers):
    tiers.opens({RISKY: "x\n"})
    assert attack(tiers, tiers.sibling).returncode == 0
    tiers.push({RISKY: "later\n"})
    refused(review(tiers, tiers.lead), "needs an attack review")
    tiers.shows(review="SUCCESS")
    refused(guard(tiers, tiers.lead), "needs an attack review")


def test_a_branch_named_like_an_attack_review_passes_no_ordinary_accept_off_as_one(tiers):
    branch = f"{merge_review.ATTACK}change"
    tiers.opens({RISKY: "x\n"}, branch=branch)
    landing.review(tiers.node.session(tiers.sibling), merge_review.label(branch), tiers.head, "accept")
    tiers.shows(review="SUCCESS")
    refused(guard(tiers, tiers.lead), "needs an attack review")
    refused(review(tiers, tiers.lead), "needs an attack review")


def test_a_reject_of_the_head_after_the_accept_holds_the_merge(tiers):
    tiers.opens({"docs/b.md": "b\n"})
    assert review(tiers, tiers.lead).returncode == 0
    tiers.shows(review="SUCCESS")
    landing.review(tiers.node.session(tiers.other), BRANCH, tiers.head, "reject")
    refused(guard(tiers, tiers.lead), "reject", tiers.other.name)


@pytest.mark.parametrize("command", ["gh pr merge 12 --merge", "gh pr merge 12 --squash", "gh pr merge --merge",
                                     "gh pr merge 12 --merge --match-head-commit {sha} --auto",
                                     "true && gh pr merge 12 --merge --match-head-commit {sha}",
                                     "gh -R o/r pr merge 12 --merge --match-head-commit {sha}"])
def test_no_other_form_of_the_merge_command_gets_round_the_judgement(tiers, command):
    tiers.opens({RISKY: "x\n"})
    tiers.shows(review="SUCCESS")
    landing.review(tiers.node.session(tiers.sibling), DEV, git(tiers.project.root, "rev-parse", "HEAD"), "accept")
    refused(guard(tiers, tiers.lead, command.format(sha=tiers.head)), "--match-head-commit SHA")


def test_a_pull_request_that_cannot_be_read_is_not_merged(tiers):
    tiers.opens({RISKY: "x\n"})
    landing.review(tiers.node.session(tiers.sibling), DEV, git(tiers.project.root, "rev-parse", "HEAD"), "accept")
    (tiers.tmp / "replies" / "pr_12.json").unlink()
    refused(guard(tiers, tiers.lead), "cannot be read")


def test_commits_that_cannot_be_read_are_not_merged(tiers):
    tiers.opens({"docs/b.md": "b\n"})
    tiers.head = "1" * 40
    tiers.shows(review="SUCCESS")
    refused(guard(tiers, tiers.lead), "could not be judged")


def test_main_takes_a_risky_promotion_only_with_the_attack_verdict(tiers, monkeypatch):
    tiers.opens({RISKY: "x\n"}, branch="promote/next", base="main")
    landing.review(tiers.node.session(tiers.sibling), "promote/next", tiers.head, "accept")
    git(tiers.project.root, "checkout", "-q", "--detach", tiers.head)
    refused(guard(tiers, tiers.lead), "needs an attack review")
    refused(review(tiers, tiers.lead), "needs an attack review")
    assert attack(tiers, tiers.other).returncode == 0
    done = review(tiers, tiers.lead)
    assert done.returncode == 0, done.stderr
    assert f"gh pr merge 12 --merge --match-head-commit {tiers.head}" in done.stdout
    allowed = guard(tiers, tiers.lead, f"gh pr merge 12 --merge --match-head-commit {tiers.head}")
    assert allowed.returncode == 0, allowed.stderr


def test_a_push_of_main_asks_for_the_attack_verdict_of_what_main_would_take(tiers, monkeypatch):
    for name, value in env(tiers, tiers.lead).items():
        monkeypatch.setenv(name, value)
    tiers.opens({RISKY: "x\n"})
    git(tiers.project.root, "checkout", "-q", "--detach", tiers.head)
    root = str(tiers.project.root)
    landing.review(tiers.node.session(tiers.sibling), BRANCH, tiers.head, "accept")
    assert "needs an attack review before main takes it" in release_check.refusal(root, AGENT, tiers.head)
    landing.review(tiers.node.session(tiers.other), merge_review.label(BRANCH, merge_review.ATTACK), tiers.head, "accept")
    assert release_check.refusal(root, AGENT, tiers.head) == ""


def test_the_findings_decide_the_verdict(tiers):
    tiers.opens({"docs/b.md": "b\n"})
    for severity in ("critical", "high", "medium"):
        refused(review(tiers, tiers.lead, "accept", "--finding", f"{severity}: it breaks"), "the verdict is reject")
    refused(review(tiers, tiers.lead, "reject"), "--finding")
    refused(review(tiers, tiers.lead, "reject", "--finding", "low: a name"), "follow-ups")
    refused(review(tiers, tiers.lead, "accept", "--finding", "urgent: now"), "SEVERITY")
    assert tiers.writes() == []
    done = review(tiers, tiers.lead, "accept", "--finding", "low: a name --> <!-- could be clearer")
    assert done.returncode == 0, done.stderr
    comment = tiers.writes()[0][2]
    record = merge_review.read_record(comment.split(" -->")[0] + " -->")
    assert record["verdict"] == "accept" and record["head"] == tiers.head and record["needs_attack"] is False
    assert record["findings"] == [{"severity": "low", "text": "a name --> <!-- could be clearer"}]


def test_a_reject_by_a_reviewer_that_ended_is_answered_by_name_and_never_by_a_new_accept_alone(tiers):
    first = tiers.opens({RISKY: "x\n"})
    gone = tiers.node.member("pr-first-attacker", parent=tiers.lead)
    assert attack(tiers, gone, "reject", "--finding", "high: it opens the merge").returncode == 0
    head = tiers.push({RISKY: "fixed\n"})
    assert attack(tiers, tiers.sibling).returncode == 0
    refused(review(tiers, tiers.lead), "a reject stands", first[:12], gone.name)
    refused(attack(tiers, tiers.sibling, "accept", "--resolves", first), "is still here")
    tiers.node.session(tiers.lead).retire(gone.name)
    landing.review(tiers.node.session(tiers.author), merge_review.label(BRANCH, merge_review.RESOLVED), first, "accept")
    refused(review(tiers, tiers.lead), "a reject stands", gone.name)
    refused(review(tiers, tiers.lead, "accept", "--resolves", first), "an attack review answers it")
    refused(attack(tiers, tiers.sibling, "accept", "--resolves", head), "answered by a new commit")
    done = attack(tiers, tiers.sibling, "accept", "--resolves", first)
    assert done.returncode == 0, done.stderr
    done = review(tiers, tiers.lead)
    assert done.returncode == 0, done.stderr
    assert statuses(tiers)[-1] == "success"


def test_show_names_the_review_the_change_needs_and_records_nothing(tiers):
    tiers.opens({"tests/test_change_guard.py": "def test_a():\n    pass\n"})
    done = script(tiers, tiers.author, "--show")
    assert done.returncode == 0, done.stderr
    assert "needs an attack review" in done.stdout and "tests/test_change_guard.py" in done.stdout
    assert tiers.writes() == []


def test_the_reviewer_is_shown_the_guard_tests_the_change_weakens(tiers):
    name = "tests/test_change_guard.py"
    tiers.project.advance(name, "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n")
    tiers.opens({name: "import pytest\n\n\n@pytest.mark.skip\ndef test_a():\n    assert True\n"})
    done = attack(tiers, tiers.sibling)
    assert done.returncode == 0, done.stderr
    comment = tiers.writes()[0][2]
    for shown in ("test function test_b removed", "added pytest.mark.skip", "fewer assertions"):
        assert shown in done.stdout and shown in comment

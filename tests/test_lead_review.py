"""scripts/lead-review: the review of a pull request into the development branch. A real node, a real git repository and the
script as a child process; `gh` answers canned replies (tests/github_support.py), so nothing reaches GitHub."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from git_world import DEV, ROOT, Project, git
from github_support import pull, serve

from poolhouse.board import place
from poolhouse.workspace import landing

pytest_plugins = ["node_kit"]
SCRIPT = ROOT / "scripts" / "lead-review"
GUARD = ROOT / "scripts" / "hooks" / "claude-bash-guard"
BRANCH = "work/change"


@pytest.fixture
def world(tmp_path, monkeypatch, workspace_node):
    """A lead, a subagent it started that opens pull requests, a second subagent of the lead and a second main session."""
    base = tmp_path / "git"
    base.mkdir()
    project = Project(base)
    monkeypatch.delenv("POOLHOUSE_BOARD", raising=False)
    monkeypatch.delenv("POOLHOUSE_DEV_BRANCH", raising=False)
    workspace_node.board = place.resolve(workspace_node.client, project.root)
    lead = workspace_node.member("pr-lead")
    author = workspace_node.member("pr-author", parent=lead)
    sibling = workspace_node.member("pr-sibling", parent=lead)
    other = workspace_node.member("pr-other-lead")
    head = git(project.root, "rev-parse", "HEAD")
    log = serve(tmp_path, monkeypatch, {})

    def answers(base=DEV, **found):
        pull(tmp_path, 12, head, base, **found)

    def writes():
        """Every call that changes something on GitHub, as (kind, path, what was sent)."""
        return [tuple(line.split(" ", 2)) for line in log.read_text().splitlines() if not line.startswith("GET ")]

    answers(author_agent=author.name)
    return SimpleNamespace(project=project, node=workspace_node, lead=lead, author=author, sibling=sibling, other=other,
                           head=head, answers=answers, writes=writes)


def env(world, who):
    return {**world.node.env(who), "CLAUDECODE": "1"}


def run(world, who, verdict="accept", **given):
    """The script as ``who`` over pull request 12; ``given`` may hold the commit it names (``sha``) and its ``note``."""
    note = given.get("note", "Read the diff; the tests it names pass.")
    argv = [sys.executable, str(SCRIPT), "12", given.get("sha", world.head), "--verdict", verdict, "--note", note]
    return subprocess.run(argv, cwd=world.project.root, env=env(world, who), capture_output=True, text=True, timeout=120, check=False)


def reviews(world):
    """The reviews of the head as a session that wrote none of them sees them: (reviewer, branch, verdict)."""
    observer = world.node.member("pr-observer")
    found = landing.accepts(world.node.session(observer), world.head)["reviews"]
    return [(r["by"], r["branch"], r["verdict"]) for r in found]


@pytest.mark.parametrize("verdict,state,word", [("accept", "success", "passed"), ("reject", "failure", "failed")])
def test_the_spawning_leads_verdict_is_recorded_commented_and_set_on_the_head_commit(world, verdict, state, word):
    done = run(world, world.lead, verdict)
    assert done.returncode == 0, done.stderr
    assert reviews(world) == [(world.lead.name, BRANCH, verdict)]
    comment, status = world.writes()
    assert comment[:2] == ("COMMENT", "pr/12")
    body = comment[2]
    assert world.head in body and word in body and world.lead.name in body and "Read the diff" in body
    assert status[:2] == ("POST", "repos/{owner}/{repo}/statuses/" + world.head)
    sent = json.loads(status[2])
    assert sent["state"] == state and sent["context"] == "lead-review" and world.lead.name in sent["description"]
    assert len(sent["description"]) <= 140
    merge = f"gh pr merge 12 --merge --delete-branch --match-head-commit {world.head}"
    assert (merge in done.stdout) is (verdict == "accept"), done.stdout


def test_the_merge_command_it_prints_is_the_one_the_guard_allows_the_lead_to_run(world):
    done = run(world, world.lead)
    assert done.returncode == 0, done.stderr
    command = next(line.strip() for line in done.stdout.splitlines() if line.strip().startswith("gh pr merge"))
    world.answers(author_agent=world.author.name, review="SUCCESS")
    event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(world.project.root)}
    guarded = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(event), env=env(world, world.lead), text=True,
                             capture_output=True, timeout=120, check=False)
    assert guarded.returncode == 0, guarded.stderr


def refused(world, done, why):
    assert done.returncode == 1 and why in done.stderr, (done.returncode, done.stderr)
    assert world.writes() == [], "nothing is written to GitHub"
    assert reviews(world) == [], "and nothing to the board"


def test_the_authors_accept_of_its_own_pull_request_is_refused(world):
    refused(world, run(world, world.author), "its own work")


def test_an_agent_the_subagent_author_started_does_not_accept_for_it(world):
    child = world.node.member("pr-authors-child", parent=world.author)
    refused(world, run(world, child), "started by its author")


def test_a_subagents_pull_request_is_not_accepted_by_a_subagent_that_did_not_spawn_it(world):
    refused(world, run(world, world.sibling), "the agent that spawned")


@pytest.mark.parametrize("who", ["other", "lead"])
def test_a_subagents_pull_request_is_accepted_by_a_main_session_or_an_agent_above_it(world, who):
    nested = world.node.member("pr-nested", parent=world.author)
    world.answers(author_agent=nested.name)
    done = run(world, getattr(world, who))
    assert done.returncode == 0, done.stderr


def test_a_subagent_that_spawned_the_author_accepts_it(world):
    nested = world.node.member("pr-nested", parent=world.author)
    world.answers(author_agent=nested.name)
    done = run(world, world.author)
    assert done.returncode == 0, done.stderr


def test_a_main_agents_own_pull_request_is_accepted_by_a_subagent_it_started_that_did_not_write_it(world):
    world.answers(author_agent=world.lead.name)
    refused(world, run(world, world.lead), "its own work")
    done = run(world, world.sibling)
    assert done.returncode == 0, done.stderr


def test_every_author_a_pull_request_names_is_left_out(world):
    world.answers(author_agent=f"{world.author.name}\nAuthor-agent: {world.lead.name}")
    refused(world, run(world, world.lead), "its own work")


def test_an_author_this_board_does_not_know_is_accepted_only_by_a_main_session(world):
    world.answers(author_agent="elsewhere-0000")
    refused(world, run(world, world.sibling), "elsewhere-0000")
    done = run(world, world.lead)
    assert done.returncode == 0, done.stderr


def test_a_pull_request_that_names_no_author_is_not_accepted_unless_a_bot_opened_it(world):
    world.answers()
    refused(world, run(world, world.lead), "Author-agent:")
    world.answers(bot=True)
    done = run(world, world.lead)
    assert done.returncode == 0, done.stderr


def test_a_head_that_moved_since_the_review_needs_a_new_review(world):
    refused(world, run(world, world.lead, sha="0" * 40), "new commit needs a new review")
    done = run(world, world.lead, sha=world.head[:12])
    assert done.returncode == 0, done.stderr
    last = world.writes()[-1]
    assert json.loads(last[2])["state"] == "success" and last[1].endswith(world.head)


@pytest.mark.parametrize("change,why", [({"base": "main"}, "development branch"), ({"state": "MERGED"}, "is merged")])
def test_only_an_open_pull_request_into_the_development_branch_is_reviewed(world, change, why):
    world.answers(author_agent=world.author.name, **change)
    refused(world, run(world, world.lead), why)


def test_a_review_the_node_refuses_writes_nothing_to_github(world):
    world.node.session(world.lead).claim("branch", BRANCH)
    refused(world, run(world, world.lead), "does not review it")


def test_a_comment_that_cannot_be_posted_leaves_the_status_unset(world, monkeypatch):
    monkeypatch.setenv("GH_FAIL", "pr/12")
    done = run(world, world.lead)
    assert done.returncode == 1 and "422" in done.stderr, done.stderr
    assert [call[:2] for call in world.writes()] == [("COMMENT", "pr/12")]
    assert "gh pr merge" not in done.stdout


def test_a_review_says_why_it_was_given(world):
    refused(world, run(world, world.lead, note="  "), "note")


def test_a_subagent_running_the_review_in_auto_mode_acts_as_itself_and_not_as_its_lead(tmp_path, monkeypatch):
    """The permission mode that changes only commands that act as a session changes this one: the author check reads who runs it."""
    monkeypatch.syspath_prepend(str(ROOT / "scripts" / "hooks"))
    import runpy

    guard = runpy.run_path(str(GUARD))
    assert guard["ACTS_AS_A_SESSION"].search(f"scripts/lead-review 12 {'a' * 40} --verdict accept --note ok")
    assert guard["ACTS_AS_A_SESSION"].search("cd scripts && ./lead-review 12 abc1234 --note ok")

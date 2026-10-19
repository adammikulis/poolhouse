"""scripts/land against real git repositories, a bare origin and a real node.

The creator of a branch claims it; the lander is the session that runs the command; an independent reviewer accepts.
The fake test runner logs what it was called with and the environment it saw.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import testreuse_store as storage
from land_support import AGENT_PUSH, DEV, World, git

from poolhouse import lock as filelock, worktreerules
from poolhouse.board.client import Denied
from poolhouse.workspace import landing, testruns

pytest_plugins = ["node_kit"]

SENSITIVE = "src/poolhouse/credentials/example.py"


@pytest.fixture
def world(tmp_path, workspace_node):
    return World(tmp_path, workspace_node)


def wait_for(path: Path, seconds: float = 60.0) -> None:
    """Wait until ``path`` exists."""
    end = time.time() + seconds
    while not path.exists() and time.time() < end:
        time.sleep(0.05)
    assert path.exists(), f"{path} never appeared"


def test_a_docs_branch_lands_and_the_worktree_and_branch_are_removed(world):
    tree, sha = world.start("docs-only", {"docs/a.md": "changed\n"})
    code, out = world.land(tree)
    assert code == 0 and "land: " + sha[:12] + " is on origin/" + DEV in out, out
    assert world.proj.origin_head() == world.proj.local_head() == sha
    assert not tree.exists() and "docs-only" not in git(world.proj.root, "branch", "--list", "docs-only")
    assert world.proj.calls() == [], "a documentation change that no test names runs no test"
    assert [(r["outcome"], r["branch"], r["sha"]) for r in world.records()] == [("landed", "docs-only", sha)]


def test_a_code_branch_runs_the_gate_and_its_affected_tests(world):
    tree, sha = world.start("tests-only", {"tests/test_new.py": "import poolhouse.mod\n"})
    code, out = world.land(tree)
    assert code == 0, out
    calls = [c for c in world.proj.calls() if not c.startswith("ENV")]
    assert calls == ["gate", "all tests/test_new.py"]
    assert world.proj.origin_head() == sha


def test_a_process_a_check_leaves_behind_is_ended_with_the_check(world):
    tree, _ = world.start("orphan", {"tests/test_orphan.py": "# ORPHAN\n"})
    began = time.time()
    code, out = world.land(tree)
    assert code == 0, out
    assert time.time() - began < 100, "the landing did not wait for the process the check left behind"
    pid = int((world.proj.base / "orphan.pid").read_text())
    end = time.time() + 5
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)
    pytest.fail(f"process {pid} left behind by a check is still running")


def test_a_background_test_job_gone_quiet_is_ended_before_the_checks_run(world, tmp_path):
    reuse = tmp_path / "reuse"
    folder = reuse / testruns.scope(world.proj.root) / "jobs" / "stuck"
    folder.mkdir(parents=True)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True)
    try:
        status = {"state": "running", "pid": process.pid, "started": storage.process_start(process.pid), "at": time.time() - 3000}
        (folder / "status.json").write_text(json.dumps(status))
        (folder / "spec.json").write_text(json.dumps({"id": "stuck", "created": time.time() - 3000}))
        (folder / "log").write_text("x")
        os.utime(folder / "log", (time.time() - 3600,) * 2)
        tree, _ = world.start("jam", {"tests/test_jam.py": "# JAM\n"})
        code, out = world.land(tree, hostile={"DEV_TEST_REUSE_DIR": str(reuse)})
        assert code == 0 and "queue doctor: ended" in out, out
        assert json.loads((folder / "status.json").read_text())["state"] == "failed"
        process.wait(timeout=10)
    finally:
        process.kill()


def test_a_markdown_file_outside_the_documents_still_gets_the_gate(world):
    tree, _ = world.start("prompt", {"src/poolhouse/graph/prompt.md": "A prompt the code reads.\n"})
    code, out = world.land(tree)
    assert code == 0, out
    assert [c for c in world.proj.calls() if not c.startswith("ENV")] == ["gate"]


def test_a_hostile_environment_does_not_reach_the_checks(world):
    tree, _ = world.start("hostile", {"tests/test_new.py": "import poolhouse.mod\n"})
    code, out = world.land(tree, hostile={"PYTEST_ADDOPTS": "--deselect tests/test_new.py", "POOLHOUSE_LIVE_API": "1", "LAND_HOSTILE": "x"})
    assert code == 0, out
    seen = [c for c in world.proj.calls() if c.startswith("ENV")]
    assert "ENV PYTEST_ADDOPTS=" in seen and "ENV POOLHOUSE_LIVE_API=" in seen and "ENV LAND_HOSTILE=" in seen
    assert not [c for c in seen if c.split("=", 1)[1]], seen


def test_a_failing_test_stops_the_landing_naming_the_test(world):
    tree, _ = world.start("bad", {"tests/test_bad.py": "# FAILME\n"})
    before = world.proj.origin_head()
    code, out = world.land(tree)
    assert code == 5 and "land: stopped: tests failed: tests/test_bad.py::test_x" in out, out
    assert world.proj.origin_head() == before and tree.exists()
    record = world.records()[-1]
    assert record["outcome"] == "stopped" and "tests/test_bad.py::test_x" in record["cause"]
    assert any("FAILED tests/test_bad.py::test_x" in line for line in record["tail"])


def test_an_interrupted_test_run_says_so_and_names_the_test(world):
    tree, _ = world.start("cut", {"tests/test_cut.py": "# INTERRUPTME\n"})
    before = world.proj.origin_head()
    code, out = world.land(tree)
    assert code == 5 and "land: stopped: the test run was interrupted by signal 2 during tests/test_cut.py::test_y" in out, out
    assert "tests failed" not in out and world.proj.origin_head() == before and tree.exists()


def test_a_sensitive_change_without_an_accept_is_refused_with_the_command_for_the_reviewer(world):
    tree, sha = world.start("sensitive", {SENSITIVE: "VALUE = 1\n"})
    before = world.proj.origin_head()
    code, out = world.land(tree)
    assert code == 5 and "needs review" in out and SENSITIVE in out, out
    assert f"poolhouse-workspace land-review sensitive {sha} --verdict accept" in out
    assert world.proj.origin_head() == before and world.proj.calls() == []


def test_the_creators_accept_under_another_branch_name_does_not_count(world):
    tree, sha = world.start("mine", {SENSITIVE: "VALUE = 1\n"})
    creator = world.node.session(world.creator)
    landing.review(creator, "some-other-name", sha, "accept")
    with pytest.raises(Denied):
        landing.review(creator, "mine", sha, "accept")
    code, out = world.land(tree)
    assert code == 5 and "needs review" in out, out
    assert world.proj.origin_head() != sha


def test_the_first_run_on_a_branch_nobody_claimed_records_the_caller_as_its_creator(world):
    tree, sha = world.start("unclaimed", {SENSITIVE: "VALUE = 1\n"}, claim=False)
    code, out = world.land(tree)
    assert code == 5 and f"{world.lander.name} is recorded as its creator" in out and "no independent accept that still stands" in out, out
    assert world.node.session(world.lander).call("land_accepts", branch="unclaimed", sha=sha, worktree=str(tree))["creators"] == [world.lander.name]
    with pytest.raises(Denied):
        landing.review(world.node.session(world.lander), "unclaimed", sha, "accept")
    world.accept("unclaimed", sha)
    code, out = world.land(tree)
    assert code == 0 and "recorded as its creator" not in out, out
    assert world.proj.origin_head() == sha


def test_an_independent_accept_of_the_exact_commit_lets_a_sensitive_change_land(world):
    tree, sha = world.start("reviewed", {SENSITIVE: "VALUE = 1\n"})
    world.accept("reviewed", sha)
    code, out = world.land(tree)
    assert code == 0, out
    assert world.proj.origin_head() == sha and not tree.exists()


def test_an_accept_of_another_commit_does_not_carry_to_an_amended_branch(world):
    tree, sha = world.start("amended", {SENSITIVE: "VALUE = 1\n"})
    world.accept("amended", sha)
    world.proj.write(tree, SENSITIVE, "VALUE = 2\n")
    git(tree, "add", SENSITIVE)
    git(tree, "commit", "-q", "--amend", "-m", "feat: amended")
    newer = git(tree, "rev-parse", "HEAD")
    assert newer != sha
    code, out = world.land(tree)
    assert code == 5 and f"for {newer[:12]}" in out and "needs review" in out, out
    assert world.proj.origin_head() != newer


def test_a_reject_stops_a_sensitive_landing_even_with_an_accept(world):
    tree, sha = world.start("rejected", {SENSITIVE: "VALUE = 1\n"})
    world.accept("rejected", sha)
    world.accept("rejected", sha, "reject", who=world.other)
    code, out = world.land(tree)
    assert code == 5 and "rejected" in out, out


def test_an_accept_withdrawn_while_the_checks_run_stops_the_landing(world):
    tree, sha = world.start("withdrawn", {SENSITIVE: "VALUE = 1\n", "tests/test_slow.py": "# SLOW\n"})
    world.accept("withdrawn", sha)
    running = world.start_land(tree)
    wait_for(world.proj.base / "started")
    world.accept("withdrawn", sha, "reject")
    (world.proj.base / "proceed").write_text("1")
    out = running.communicate(timeout=120)[0]
    assert running.returncode == 5 and "no longer stands" in out, out
    assert world.proj.origin_head() != sha


@pytest.mark.parametrize(("cause", "lands"), [("revoked", False), ("forged", False), ("done", True)])
def test_an_accept_from_a_reviewer_ended_for_cause_stops_counting(world, cause, lands):
    tree, sha = world.start("ended", {SENSITIVE: "VALUE = 1\n"})
    kid = world.node.member(f"kid-{cause}", parent=world.other)
    landing.review(world.node.session(kid), "ended", sha, "accept")
    world.node.session(world.other).retire(kid.name, cause)
    code, out = world.land(tree)
    if lands:
        assert code == 0 and world.proj.origin_head() == sha, out
    else:
        assert code == 5 and "no independent accept that still stands" in out, out
        assert world.proj.origin_head() != sha


def test_a_second_lander_is_refused_naming_the_first(world):
    first, _ = world.start("slow", {"tests/test_slow.py": "# SLOW\n"})
    second, _ = world.start("second", {"docs/a.md": "second\n"})
    running = world.start_land(first)
    wait_for(world.proj.base / "started")
    code, out = world.land(second, who=world.other)
    assert code == 5 and world.lander.name in out and "is landing slow" in out, out
    assert second.exists()
    again, text = world.land(second, who=world.other)
    assert again == 5 and world.lander.name in text, "a refused lander leaves the first lander's note in the lock file"
    (world.proj.base / "proceed").write_text("1")
    assert running.communicate(timeout=120)[0].count("land: stopped") == 0 and running.returncode == 0


def test_the_landing_lock_is_the_one_the_runtime_cutover_waits_on(world):
    tree, _ = world.start("cutover", {"tests/test_slow.py": "# SLOW\n"})
    running = world.start_land(tree)
    wait_for(world.proj.base / "started")
    held = filelock.held_by(worktreerules.landing_lock(world.proj.root))
    (world.proj.base / "proceed").write_text("1")
    running.communicate(timeout=120)
    assert world.lander.name in held and "is landing cutover" in held, held
    assert filelock.held_by(worktreerules.landing_lock(world.proj.root)) == ""


def test_a_claim_on_the_development_branch_held_by_another_session_refuses(world):
    tree, _ = world.start("blocked", {"docs/a.md": "blocked\n"})
    world.node.session(world.other).claim("branch", DEV)
    code, out = world.land(tree)
    assert code == 5 and DEV in out and "held" in out, out
    assert world.proj.origin_head() != git(tree, "rev-parse", "HEAD")


def test_the_claim_on_the_development_branch_is_released_after_a_landing(world):
    tree, _ = world.start("released", {"docs/a.md": "released\n"})
    assert world.land(tree)[0] == 0
    world.node.session(world.other).claim("branch", DEV)


def test_a_kill_between_the_push_and_the_clean_up_is_repaired_by_the_next_run(world):
    hook = world.proj.origin / "hooks" / "post-receive"
    hook.write_text(f"#!/bin/sh\ntouch {world.proj.base / 'pushed'}\nsleep 4\n")
    hook.chmod(0o755)
    tree, sha = world.start("killed", {"docs/a.md": "killed\n"})
    before = git(world.proj.origin, "rev-list", "--count", DEV)
    running = world.start_land(tree)
    wait_for(world.proj.base / "pushed")
    os.kill(running.pid, signal.SIGKILL)
    running.communicate()
    assert world.proj.origin_head() == sha and world.proj.local_head() != sha and tree.exists()
    code, out = world.land(tree)
    assert code == 0 and "already on origin" in out, out
    assert world.proj.local_head() == sha and not tree.exists()
    assert int(git(world.proj.origin, "rev-list", "--count", DEV)) == int(before) + 1


def test_the_tree_changing_while_a_check_runs_stops_the_landing(world):
    tree, sha = world.start("mutated", {"tests/test_mutate.py": "# MUTATE\n"})
    code, out = world.land(tree)
    assert code == 5 and "no longer holds the tree" in out, out
    assert world.proj.origin_head() != sha


def test_an_ignored_file_that_is_not_a_cache_stops_the_landing(world):
    tree, sha = world.start("stray", {"docs/a.md": "stray\n"})
    (tree / ".env").write_text("SECRET=1\n")
    (tree / "__pycache__").mkdir()
    (tree / "__pycache__" / "x.pyc").write_bytes(b"")
    code, out = world.land(tree)
    assert code == 5 and ".env" in out and "__pycache__" not in out, out
    assert world.proj.origin_head() != sha


def test_a_stale_branch_that_needs_no_review_is_rebased_and_lands(world):
    tree, old = world.start("stale", {"docs/a.md": "stale\n"})
    moved = world.proj.advance("docs/b.md", "b\n")
    code, out = world.land(tree)
    assert code == 0, out
    assert world.proj.origin_head() != old and git(world.proj.root, "merge-base", "--is-ancestor", moved, DEV) == ""


def parents(world, sha):
    """The parents of a commit, first to last."""
    return git(world.proj.root, "rev-list", "--parents", "-n", "1", sha).split()[1:]


def test_a_stale_branch_that_needs_a_review_and_has_none_stops_naming_its_tip(world):
    tree, sha = world.start("stale-sensitive", {SENSITIVE: "VALUE = 1\n"})
    world.proj.advance("docs/b.md", "b\n")
    code, out = world.land(tree)
    assert code == 5 and "moved past" in out and f"land-review stale-sensitive {sha} --verdict accept" in out, out
    assert git(tree, "rev-parse", "HEAD") == sha


def test_a_reviewed_tip_behind_origin_lands_as_a_merge_that_keeps_its_accept(world):
    world.install_hook()
    tree, sha = world.start("reviewed-stale", {SENSITIVE: "VALUE = 1\n"})
    world.accept("reviewed-stale", sha)
    moved = world.proj.advance("docs/b.md", "b\n")
    code, out = world.land(tree, hostile=AGENT_PUSH)
    assert code == 0, out
    head = world.proj.origin_head()
    assert parents(world, head) == [moved, sha]
    assert git(world.proj.root, "show", f"{head}:docs/b.md") == "b"
    assert git(world.proj.root, "show", f"{head}:{SENSITIVE}") == "VALUE = 1"
    assert world.proj.local_head() == head and not tree.exists()


def test_two_reviewed_branches_land_one_after_the_other_without_a_new_review(world):
    world.install_hook()
    one, first = world.start("one", {SENSITIVE: "VALUE = 1\n"})
    two, second = world.start("two", {"src/poolhouse/credentials/other.py": "VALUE = 2\n"})
    world.accept("one", first)
    world.accept("two", second)
    assert world.land(one, hostile=AGENT_PUSH)[0] == 0
    code, out = world.land(two, hostile=AGENT_PUSH)
    assert code == 0, out
    assert parents(world, world.proj.origin_head()) == [first, second]
    assert git(world.proj.root, "show", f"{world.proj.origin_head()}:{SENSITIVE}") == "VALUE = 1"


def test_a_reviewed_tip_that_conflicts_with_origin_stops_without_a_push(world):
    world.install_hook()
    tree, sha = world.start("clash-reviewed", {SENSITIVE: "VALUE = 1\n"})
    world.accept("clash-reviewed", sha)
    world.proj.advance(SENSITIVE, "VALUE = 9\n")
    before = world.proj.origin_head()
    code, out = world.land(tree, hostile=AGENT_PUSH)
    assert code == 5 and f"conflicts in {SENSITIVE}" in out and "resolve, and ask for a new review" in out, out
    assert world.proj.origin_head() == before and git(tree, "rev-parse", "HEAD") == sha
    assert git(tree, "status", "--porcelain") == ""


def test_a_run_that_stopped_after_the_merge_is_repaired_by_running_again(world):
    world.install_hook()
    tree, sha = world.start("repaired", {SENSITIVE: "VALUE = 1\n"})
    world.accept("repaired", sha)
    stale = world.proj.advance("docs/b.md", "b\n")
    (world.proj.base / "fail_once").write_text("1")
    code, out = world.land(tree, hostile=AGENT_PUSH)
    assert code == 5 and "gate failed" in out, out
    left = git(tree, "rev-parse", "HEAD")
    assert parents(world, left) == [stale, sha]
    newest = world.proj.advance("docs/c.md", "c\n")
    code, out = world.land(tree, hostile=AGENT_PUSH)
    assert code == 0, out
    head = world.proj.origin_head()
    assert parents(world, head) == [newest, sha]
    assert git(world.proj.root, "show", f"{head}:docs/b.md") == "b"


def test_a_dry_run_says_a_reviewed_tip_behind_origin_is_merged_and_whether_it_conflicts(world):
    tree, sha = world.start("dry-stale", {SENSITIVE: "VALUE = 1\n"})
    world.accept("dry-stale", sha)
    world.proj.advance("docs/b.md", "b\n")
    code, out = world.land(tree, "--dry-run")
    assert code == 0 and "land merges origin/" + DEV in out and "an independent accept stands" in out, out
    assert "conflicts" not in out
    world.proj.advance(SENSITIVE, "VALUE = 9\n")
    code, out = world.land(tree, "--dry-run")
    assert code == 0 and f"conflicts in {SENSITIVE}" in out, out
    assert git(tree, "rev-parse", "HEAD") == sha


def test_a_conflict_with_origin_stops_and_leaves_the_branch_as_it_was(world):
    tree, sha = world.start("clash", {"docs/a.md": "mine\n"})
    world.proj.advance("docs/a.md", "theirs\n")
    code, out = world.land(tree)
    assert code == 5 and "conflicts in docs/a.md" in out, out
    assert git(tree, "rev-parse", "HEAD") == sha and git(tree, "status", "--porcelain") == ""


def test_a_lander_of_the_lowest_tier_is_refused(world):
    tree, _ = world.start("small", {"docs/a.md": "small\n"})
    small = world.node.member("small-one", model="claude-haiku-5-5")
    code, out = world.land(tree, who=small)
    assert code == 5 and "stopped" in out, out
    assert world.proj.origin_head() != git(tree, "rev-parse", "HEAD")


def test_main_is_never_landed(world):
    tree, sha = world.start("towards-main", {"docs/a.md": "x\n"})
    code, out = world.land(tree, "main")
    assert code == 5 and "land never touches main" in out, out
    assert world.proj.origin_head() != sha


def test_a_session_the_board_does_not_know_lands_nothing(world):
    tree, sha = world.start("unknown-session", {"docs/a.md": "x\n"})
    code, out = world.land(tree, hostile={"POOLHOUSE_WORKSPACE_AGENT": "nobody-registered"})
    assert code == 5 and "does not know this session" in out, out
    assert world.proj.origin_head() != sha


def test_a_dry_run_reports_the_review_and_the_checks_and_changes_nothing(world):
    tree, sha = world.start("dry", {SENSITIVE: "VALUE = 1\n", "tests/test_new.py": "import poolhouse.mod\n"})
    code, out = world.land(tree, "--dry-run")
    assert code == 0 and "review needed" in out and f"land-review dry {sha}" in out, out
    assert "checks: gate, affected" in out
    assert world.proj.origin_head() != sha and tree.exists() and world.proj.calls() == []


def test_a_branch_already_on_origin_is_only_cleaned_up(world):
    tree, sha = world.start("landed-elsewhere", {})
    code, out = world.land(tree)
    assert code == 0 and "already on origin" in out, out
    assert not tree.exists() and world.proj.origin_head() == sha


def test_a_branch_whose_commits_net_to_nothing_has_nothing_to_land(world):
    tree, _ = world.start("net-zero", {"docs/a.md": "x\n"})
    world.proj.write(tree, "docs/a.md", "a\n")
    world.proj.commit(tree, "chore: revert")
    code, out = world.land(tree)
    assert code == 5 and "nothing to land" in out, out
    assert tree.exists()


def test_a_local_only_commit_made_while_the_checks_run_is_reported_after_the_push(world):
    tree, sha = world.start("slow-local", {"tests/test_slow.py": "# SLOW\n"})
    running = world.start_land(tree)
    wait_for(world.proj.base / "started")
    world.proj.write(world.proj.root, "docs/local.md", "local\n")
    local = world.proj.commit(world.proj.root, "chore: local only")
    (world.proj.base / "proceed").write_text("1")
    out = running.communicate(timeout=120)[0]
    assert running.returncode == 6 and "WARNING" in out and "holds commits origin lacks" in out, out
    assert world.proj.origin_head() == sha and world.proj.local_head() == local
    assert world.records()[-1]["outcome"] == "landed" and "holds commits origin lacks" in world.records()[-1]["cause"]


def test_a_local_only_commit_on_the_development_branch_stops_before_anything_is_pushed(world):
    tree, sha = world.start("behind-local", {"docs/a.md": "x\n"})
    world.proj.write(world.proj.root, "docs/local.md", "local\n")
    world.proj.commit(world.proj.root, "chore: local only")
    code, out = world.land(tree)
    assert code == 5 and "holds commits origin lacks" in out, out
    assert world.proj.origin_head() != sha

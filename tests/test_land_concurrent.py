"""Landings overlap their checks and meet only at the push: the lock and the claim are taken for the last step, and a result stays
good only while what origin gained since cannot change it.

Real git repositories, a bare origin and a real node, as in test_land.py. The fake test runner logs every run and the commit it ran on.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import land_exec
import land_steps
import pytest
from land_support import AGENT_PUSH, DEV, RunnerLock, World, git

from poolhouse import lock as filelock, worktreerules
from poolhouse.workspace import landing

pytest_plugins = ["node_kit"]

SENSITIVE = "src/poolhouse/credentials/example.py"
MODULE = "".join(f"{name} = {i}\n" for i, name in enumerate("ABCDEFGH", 1))
SLOW_TEST = "import poolhouse.mod\n# SLOW\n"


@pytest.fixture
def world(tmp_path, workspace_node):
    return World(tmp_path, workspace_node)


class Output:
    """The lines a started lander prints, collected as they come."""

    def __init__(self, process) -> None:
        self.process = process
        self.lines: list[str] = []
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self) -> None:
        """Collect lines until the process closes its output."""
        for line in self.process.stdout:
            self.lines.append(line.rstrip("\n"))

    def text(self) -> str:
        """Everything printed so far."""
        return "\n".join(self.lines)

    def wait_for(self, fragment: str, seconds: float = 60.0) -> None:
        """Wait until a printed line holds ``fragment``."""
        end = time.time() + seconds
        while fragment not in self.text() and time.time() < end and self.process.poll() is None:
            time.sleep(0.05)
        assert fragment in self.text(), f"never printed {fragment!r}:\n{self.text()}"

    def finish(self, seconds: float = 120.0) -> tuple[int, str]:
        """Wait for the process to end; its exit status and everything it printed."""
        code = self.process.wait(timeout=seconds)
        self.reader.join(timeout=10)
        return code, self.text()


def wait_for(path: Path, seconds: float = 60.0) -> None:
    """Wait until ``path`` exists."""
    end = time.time() + seconds
    while not path.exists() and time.time() < end:
        time.sleep(0.05)
    assert path.exists(), f"{path} never appeared"


def wait_for_lines(path: Path, count: int, seconds: float = 60.0) -> None:
    """Wait until the log at ``path`` holds ``count`` lines."""
    end = time.time() + seconds
    while time.time() < end and (not path.exists() or len(path.read_text().splitlines()) < count):
        time.sleep(0.05)
    assert path.exists() and len(path.read_text().splitlines()) >= count, f"{path} never held {count} lines"


def release_checks(world) -> None:
    """Let every check that waits on ``proceed`` go on."""
    (world.proj.base / "proceed").write_text("1")


def runs(world, kind: str = "all") -> int:
    """How many times the fake runner ran ``kind``."""
    return len(world.proj.tested(kind))


def test_two_landings_overlap_their_test_phases(world):
    one, _ = world.start("one", {"tests/test_slow_one.py": "# SLOW\n"})
    two, _ = world.start("two", {"tests/test_slow_two.py": "# SLOW\n"})
    first = Output(world.start_land(one))
    second = Output(world.start_land(two, who=world.other))
    wait_for_lines(world.proj.base / "slow.log", 2)
    release_checks(world)
    code_one, out_one = first.finish()
    code_two, out_two = second.finish()
    assert code_one == 0 and code_two == 0, out_one + out_two
    head = world.proj.origin_head()
    assert git(world.proj.root, "show", f"{head}:tests/test_slow_one.py") and git(world.proj.root, "show", f"{head}:tests/test_slow_two.py")
    assert not one.exists() and not two.exists()
    assert runs(world, "gate") == 2 and runs(world) == 2, "each branch is tested once; the one that landed second keeps its green result"
    assert sorted(r["outcome"] for r in world.records()) == ["landed", "landed"]


def test_a_lander_at_the_push_waits_for_the_one_pushing_and_names_it(world):
    world.hold_pushes()
    first, _ = world.start("first", {"docs/a.md": "first\n"})
    second, _ = world.start("second", {"docs/b.md": "second\n"})
    running = Output(world.start_land(first))
    wait_for(world.proj.base / "pushed")
    waiting = Output(world.start_land(second, who=world.other))
    waiting.wait_for("waiting for the landing lock")
    held = filelock.held_by(worktreerules.landing_lock(world.proj.root))
    (world.proj.base / "release").write_text("1")
    code_first, out_first = running.finish()
    code_second, out_second = waiting.finish()
    assert world.lander.name in held and "is landing first" in held, held
    assert world.lander.name in out_second and "is landing first" in out_second, out_second
    assert code_first == 0 and code_second == 0, out_first + out_second
    head = world.proj.origin_head()
    assert git(world.proj.root, "show", f"{head}:docs/a.md") == "first" and git(world.proj.root, "show", f"{head}:docs/b.md") == "second"


def test_a_second_lander_of_the_same_branch_is_refused_and_a_killed_lander_leaves_no_refusal(world):
    tree, sha = world.start("twice", {"tests/test_slow.py": "# SLOW\n"})
    running = world.start_land(tree)
    wait_for(world.proj.base / "started")
    code, out = world.land(tree, who=world.other)
    assert code == 5 and "twice is already being landed by" in out and world.lander.name in out, out
    running.kill()
    running.communicate()
    release_checks(world)
    code, out = world.land(tree, who=world.other)
    assert code == 0 and world.proj.origin_head() == sha, out


def test_a_lock_that_stays_held_refuses_the_lander_naming_the_holder(world):
    holder = RunnerLock(world.proj.root)
    assert holder.acquire()
    fd = os.open(holder.path, os.O_RDWR)
    try:
        began = time.monotonic()
        with pytest.raises(land_exec.Stop, match=f"pid {os.getpid()}"):
            land_steps.acquire(fd, holder.path, 0.5)
        assert time.monotonic() - began >= 0.5, "the lander waited the time it was given before it gave up"
    finally:
        os.close(fd)
        holder.release()


def test_a_lock_released_while_the_lander_waits_is_taken(world):
    holder = RunnerLock(world.proj.root)
    assert holder.acquire()
    fd = os.open(holder.path, os.O_RDWR)
    threading.Timer(0.4, holder.release).start()
    try:
        land_steps.acquire(fd, holder.path, 30.0)
        assert filelock.held_by(holder.path) != ""
    finally:
        filelock.release(fd)
        os.close(fd)


def test_the_checks_run_outside_the_lock_and_the_claim(world):
    tree, _ = world.start("outside", {"tests/test_slow.py": "# SLOW\n"})
    running = world.start_land(tree)
    wait_for(world.proj.base / "started")
    assert filelock.held_by(worktreerules.landing_lock(world.proj.root)) == ""
    world.node.session(world.other).claim("branch", DEV)
    world.node.session(world.other).release("branch", DEV)
    release_checks(world)
    assert running.communicate(timeout=120)[0].count("land: stopped") == 0 and running.returncode == 0


def test_an_origin_that_moves_past_what_the_branch_and_its_tests_touch_keeps_the_green_result(world):
    tree, sha = world.start("untouched", {"tests/test_slow.py": SLOW_TEST})
    running = world.start_land(tree)
    wait_for(world.proj.base / "started")
    moved = world.proj.advance("docs/b.md", "b\n")
    release_checks(world)
    code, out = Output(running).finish()
    assert code == 0, out
    head = world.proj.origin_head()
    assert git(world.proj.root, "rev-list", "--parents", "-n", "1", head).split()[1:] == [moved]
    assert git(world.proj.root, "diff", "--name-only", moved, head) == "tests/test_slow.py"
    assert runs(world, "gate") == 1 and runs(world) == 1 and world.proj.tested()[0] == sha
    assert "earlier green result" in out and head not in world.proj.tested()


@pytest.mark.parametrize("name", ["same-file", "imported-module"])
def test_an_origin_that_moves_through_the_branchs_files_or_tests_is_tested_again(world, name):
    if name == "same-file":
        target = "src/poolhouse/units.py"
        world.proj.advance(target, MODULE)
        files = {target: MODULE.replace("A = 1", "A = 10"), "tests/test_slow.py": "# SLOW\n"}
        upstream = MODULE.replace("H = 8", "H = 80")
        merged = upstream.replace("A = 1", "A = 10")
    else:
        target, files, upstream = "src/poolhouse/mod.py", {"tests/test_slow.py": SLOW_TEST}, "VALUE = 2\n"
        merged = upstream
    tree, sha = world.start("touched", files)
    running = world.start_land(tree)
    wait_for(world.proj.base / "started")
    moved = world.proj.advance(target, upstream)
    release_checks(world)
    code, out = Output(running).finish()
    assert code == 0, out
    head = world.proj.origin_head()
    assert runs(world, "gate") == 2 and runs(world) == 2, out
    assert world.proj.tested()[0] == sha and world.proj.tested()[-1] == head, "the commit that was pushed is the one the second run tested"
    assert git(world.proj.root, "show", f"{head}:{target}") == merged.strip()
    assert git(world.proj.root, "merge-base", "--is-ancestor", moved, head) == ""
    assert "checks again" in out


def test_a_green_tip_that_the_moved_origin_breaks_is_not_pushed(world):
    tree, _ = world.start("broken-by-origin", {"tests/test_slow.py": SLOW_TEST})
    running = world.start_land(tree)
    wait_for(world.proj.base / "started")
    moved = world.proj.advance("src/poolhouse/mod.py", "BROKEN = 1\n")
    release_checks(world)
    code, out = Output(running).finish()
    assert code == 5 and "tests failed: tests/test_slow.py::test_x" in out, out
    assert world.proj.origin_head() == moved and tree.exists()
    assert world.records()[-1]["outcome"] == "stopped"


def test_an_origin_that_keeps_moving_through_the_checks_stops_the_landing_after_three_rounds(world):
    world.mover()
    tree, _ = world.start("chased", {"tests/test_mover.py": "import poolhouse.mod\n# MOVER\n"})
    code, out = world.land(tree)
    assert code == 5 and "moved 3 times" in out and "run land again" in out, out
    assert runs(world) == 3 and tree.exists()
    assert "tests/test_mover.py" not in git(world.proj.root, "ls-tree", "-r", "--name-only", world.proj.origin_head())


def test_a_change_to_the_lander_on_origin_during_the_checks_stops_the_landing(world):
    tree, _ = world.start("lander-moves", {"tests/test_slow.py": SLOW_TEST})
    running = world.start_land(tree)
    wait_for(world.proj.base / "started")
    moved = world.proj.advance("scripts/land_extra.py", "VALUE = 1\n")
    release_checks(world)
    code, out = Output(running).finish()
    assert code == 5 and "the lander changed on origin while this run was going" in out, out
    assert world.proj.origin_head() == moved and tree.exists()


def test_an_accept_that_ends_as_forged_while_origin_moves_stops_a_reviewed_landing(world):
    tree, sha = world.start("forged-moved", {SENSITIVE: "VALUE = 1\n", "tests/test_slow.py": "# SLOW\n"})
    kid = world.node.member("kid-forged", parent=world.other)
    landing.review(world.node.session(kid), "forged-moved", sha, "accept")
    running = world.start_land(tree)
    wait_for(world.proj.base / "started")
    moved = world.proj.advance("docs/b.md", "b\n")
    world.node.session(world.other).retire(kid.name, "forged")
    release_checks(world)
    code, out = Output(running).finish()
    assert code == 5 and "moved past" in out and "no independent accept that still stands" in out, out
    assert world.proj.origin_head() == moved and git(tree, "rev-parse", "HEAD") == sha


def test_a_reviewed_landing_whose_origin_moved_pushes_the_merge_that_keeps_its_accept(world):
    world.install_hook()
    tree, sha = world.start("reviewed-moved", {SENSITIVE: "VALUE = 1\n", "tests/test_slow.py": "# SLOW\n"})
    world.accept("reviewed-moved", sha)
    running = world.start_land(tree, hostile=AGENT_PUSH)
    wait_for(world.proj.base / "started")
    moved = world.proj.advance("docs/b.md", "b\n")
    release_checks(world)
    code, out = Output(running).finish()
    assert code == 0, out
    head = world.proj.origin_head()
    assert git(world.proj.root, "rev-list", "--parents", "-n", "1", head).split()[1:] == [moved, sha]
    assert runs(world) == 1 and "earlier green result" in out


def test_a_claim_held_by_another_session_refuses_at_the_push_after_the_checks_ran_and_a_rerun_lands(world):
    tree, sha = world.start("again", {"tests/test_slow.py": "# SLOW\n"})
    release_checks(world)
    world.node.session(world.other).claim("branch", DEV)
    code, out = world.land(tree)
    assert code == 5 and DEV in out and "held" in out, out
    assert runs(world) == 1, "the checks ran before the claim was asked for"
    assert filelock.held_by(worktreerules.landing_lock(world.proj.root)) == ""
    world.node.session(world.other).release("branch", DEV)
    code, out = world.land(tree)
    assert code == 0 and world.proj.origin_head() == sha and not tree.exists(), out

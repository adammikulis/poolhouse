"""scripts/pool-check: the parts that need no second machine run in every tier; the pool itself runs only when asked (POOLHOUSE_POOL_CHECK).

``POOLHOUSE_POOL_CHECK=native`` starts real node processes on this machine and runs a few steps; ``=docker`` runs steps with Linux containers
too (Docker Desktop, or any Docker). Neither is in a default tier: they are slow, and the second needs Docker.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pool_check import dock, rig
from pool_check.device import WslDevice, wsl_argv
from pool_check.report import Report, Skip
from pool_check.world import eventually

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pool-check"
MODE = os.environ.get("POOLHOUSE_POOL_CHECK", "")
pytest_plugins = ("node_kit",)  # the node_binary fixture the slow tests use


def run_script(*argv: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, timeout=timeout, check=False)


def test_a_step_that_fails_is_reported_with_its_own_output_and_one_that_cannot_run_says_why():
    lines: list[str] = []
    report = Report(emit=lines.append)

    def broken():
        raise AssertionError("linux2 never listed the entry: ['a', 'b']")

    def impossible():
        raise Skip("needs three devices")
    report.step("holds", lambda: "a note")
    report.step("breaks", broken)
    report.step("cannot", impossible)
    assert [r.ok for r in report.results] == [True, False, True]
    assert lines[0].startswith("PASS  holds") and "a note" in lines[0]
    assert lines[1].startswith("FAIL  breaks") and "linux2 never listed the entry: ['a', 'b']" in lines[1]
    assert lines[2].startswith("SKIP  cannot") and "needs three devices" in lines[2]
    assert report.summary() == "1 of 2 steps passed, 1 skipped; FAILED: breaks"


def test_an_unexpected_exception_is_a_failure_with_its_frames_not_a_crash_of_the_rig():
    report = Report(emit=lambda line: None)

    def explode():
        {}["missing"]

    result = report.step("explodes", explode)
    assert not result.ok and "KeyError" in result.detail and "explode" in result.detail


def test_eventually_names_what_it_waited_for_and_the_last_thing_it_saw():
    seen = iter(["not yet", "still not"])

    def check():
        raise AssertionError(next(seen, "still not"))
    with pytest.raises(AssertionError, match=r"still waiting for the entry to arrive; last saw: AssertionError: still not"):
        eventually(check, 0.6, "the entry to arrive", every=0.1)
    assert eventually(lambda: "now", 1, "anything") == "now"


def test_the_updater_rig_waits_for_the_node_the_supervisor_restarts_and_for_it_to_know_the_pool_builds(tmp_path):
    from pool_check import updater
    ahead = {"build": {"build_seq": 5}, "members": [{"status": "active", "build_seq": 9}]}
    alone = {"build": {"build_seq": 5}, "members": []}
    replies = iter([ConnectionError("the node closed the connection"), {"pid": 7}, FileNotFoundError("no socket"), {"pid": 9}, alone, {"pid": 9}, ahead])

    def ask(_state, _method):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply
    assert updater.wait_for_restart(tmp_path, 7, seconds=10, ask=ask) == ahead
    with pytest.raises(RuntimeError, match="did not bring the node back"):
        updater.wait_for_restart(tmp_path, 7, seconds=0.5, ask=lambda _s, m: {"pid": 7} if m == "hello" else alone)


def test_a_command_in_wsl_carries_its_environment_because_wsl_passes_none():
    argv = wsl_argv("Ubuntu-24.04", "/root/poolhouse", ["python", "x.py"], {"POOLHOUSE_HOME": "/tmp/pcw", "A": "b"})
    assert argv == ["wsl.exe", "-d", "Ubuntu-24.04", "--cd", "/root/poolhouse", "--", "env", "POOLHOUSE_HOME=/tmp/pcw", "A=b", "python", "x.py"]


def test_the_device_behind_nat_cannot_be_dialed_by_anything():
    from pool_check.device import Undialable
    nat = WslDevice(name="wsl9", port=1)
    with pytest.raises(Undialable, match="behind NAT"):
        nat.reach(nat)


def test_the_image_holds_what_a_device_needs_and_nothing_of_the_owners():
    names = dock.tracked_files()
    assert "src/poolhouse/node_join.py" in names and "app/poolhouse-node/src/net.rs" in names and "scripts/test_on.py" in names
    assert not any(name.startswith(("tests/", "docs/", ".claude/", ".git")) for name in names), "the image carries only the packaging and the node; the rig's own files travel separately"
    assert dock.context_hash(names) == dock.context_hash(list(names)), "the tag is a function of the contents"


def test_the_steps_are_named_once_and_each_says_what_it_proves():
    names = [name for name, _ in rig.STEPS]
    assert len(names) == len(set(names))
    assert all((step.__doc__ or "").strip() for _, step in rig.STEPS), "a step with no docstring says nothing in --list"


def test_the_script_lists_its_steps_and_refuses_a_pool_of_one():
    listed = run_script("--list")
    assert listed.returncode == 0 and "join-secure" in listed.stdout and "partition-heal" in listed.stdout
    refused = run_script("--devices", "1", "--platform", "native")
    assert refused.returncode == 2 and "at least 2 devices" in refused.stderr
    unknown = run_script("--platform", "native", "--only", "no-such-step")
    assert unknown.returncode == 2 and "no such step" in unknown.stderr


@pytest.mark.slow
@pytest.mark.skipif(MODE not in ("native", "docker"), reason="set POOLHOUSE_POOL_CHECK=native (or docker) to start real nodes")
def test_three_native_devices_join_converge_and_refuse_a_removed_member(node_binary):
    done = run_script("--platform", "native", "--devices", "3", "--binary", str(node_binary), "--only", "join-secure", "--only", "entries-converge", "--only", "member-removal", timeout=900)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "PASS  member-removal" in done.stdout


@pytest.mark.slow
@pytest.mark.skipif(MODE != "docker", reason="set POOLHOUSE_POOL_CHECK=docker to start Linux containers")
def test_a_linux_container_joins_and_a_device_behind_nat_runs_tests(node_binary):
    done = run_script("--devices", "2", "--wsl", "--binary", str(node_binary), "--only", "join-secure", "--only", "nat-tests", timeout=1800)
    assert done.returncode == 0, done.stdout + done.stderr

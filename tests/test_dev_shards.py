"""Test shards between members of a pool in dev mode: a real daemon over pinned TLS, a real executor, a real pytest run.

The receiving side is the daemon's own request handler and job runner, listening on loopback with the pool's certificate rules;
the sending side is `DevShards` over `Member`. The only plumbing stand-in is `testfarm_tree`'s stub runner in the tests that need a
process that hangs or a failure on demand.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path

import pytest
import test_on
from dev_pool_kit import (  # noqa: F401  (a fixture)
    Device,
    device,
    real_tree,
    sender,
    serve,
    this_python_runs_shards,
)
from testfarm_tree import make_tree

from poolhouse import features, http
from poolhouse.fleet import shard_host, tls
from poolhouse.fleet.pool_roster import Pool
from poolhouse.testfarm import dev, farm
from poolhouse.testfarm.client import ShardError


@pytest.fixture(autouse=True)
def pins_are_forgotten(this_python_runs_shards):  # noqa: F811
    yield
    http._PINNED.clear()


def run_real(one: Device, tmp_path: Path, files: list[str], lines: list[str] | None = None) -> dict:
    """Send the real tree's ``files`` to ``one`` and wait for the result."""
    shards = sender(one, lines)
    shard = shards.send(one.fingerprint, real_tree(tmp_path / "tree"), "all", files, 120)
    return shards.wait(one.fingerprint, shard, timeout_s=120, poll_s=0.2)


def test_a_member_in_dev_mode_runs_the_tests_and_sends_back_the_result(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", "claude-ab12cd")
    lines: list[str] = []
    with device(tmp_path) as one:
        result = run_real(one, tmp_path, ["tests/test_pass.py", "tests/test_broken.py"], lines)
        assert result["exit"] == 1 and result["platform"]["system"] == sys.platform
        assert result["files"]["tests/test_pass.py"]["passed"] == 2
        assert result["files"]["tests/test_broken.py"]["failed"] == 1
        assert [f["nodeid"] for f in result["failures"]] == ["tests/test_broken.py::test_broken::test_wrong"]
        assert any("2 passed" in line or "1 failed" in line for line in lines), "the runner's own output reaches the sender as it is written"
        assert all(line.startswith("[box] ") for line in lines)
        (job,) = [j for j in one.member.jobs() if j["id"].startswith("shard-")]
        assert job["by"] == f"{dev.fingerprint_of(tls.local().beacon)}/claude-ab12cd", "the job names the device and the agent that asked"
        assert not list((one.root / "files" / "shards").glob("*/tree.tgz")), "the tree is deleted once the run ends"


def test_what_the_device_says_about_taking_tests(tmp_path):
    with device(tmp_path) as one:
        caps = sender(one).capability(one.fingerprint)
        assert caps["accepts"] is True and caps["mode"] == "dev" and caps["reason"] == ""
        assert caps["platform"]["system"] == sys.platform and caps["free"] == 1 and caps["most_active"] == 1


@pytest.mark.redteam
def test_a_pool_that_is_not_in_dev_mode_takes_no_tests(tmp_path):
    with device(tmp_path, mode="prod") as one:
        shards = sender(one)
        caps = shards.capability(one.fingerprint)
        assert caps["accepts"] is False and "not in dev mode" in caps["reason"]
        with pytest.raises(ShardError, match="box takes no tests: this device is in a pool that is not in dev mode"):
            shards.send(one.fingerprint, real_tree(tmp_path / "tree"), "all", ["tests/test_pass.py"], 60)
        header = {"id": "ab" * 16, "tree_sha256": "0" * 64, "tier": "all", "files": [], "timeout_s": 60}
        with pytest.raises(Exception, match=r"403.*this device takes no tests: this device is in a pool that is not in dev mode"):
            one.member.shard_start(header)
        assert one.member.jobs() == []


@pytest.mark.redteam
def test_a_person_can_turn_tests_from_other_devices_off_on_a_device_in_dev_mode(tmp_path):
    with device(tmp_path) as one:
        features.switch("remote-tests", False, root=str(one.root))
        shards = sender(one)
        caps = shards.capability(one.fingerprint)
        assert caps["accepts"] is False and "poolhouse features enable remote-tests" in caps["reason"]
        with pytest.raises(ShardError, match="box takes no tests: tests from other devices were turned off here"):
            shards.send(one.fingerprint, real_tree(tmp_path / "tree"), "all", ["tests/test_pass.py"], 60)
        assert one.member.jobs() == []
        features.switch("remote-tests", True, root=str(one.root))
        assert sender(one).capability(one.fingerprint)["accepts"] is True


def test_a_device_with_no_python_that_runs_pytest_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setenv(shard_host.PYTHON_ENV, str(tmp_path / "no-such-python"))
    with device(tmp_path) as one:
        caps = sender(one).capability(one.fingerprint)
        assert caps["accepts"] is False
        assert "Python" in caps["reason"] and "is missing on this device" in caps["reason"] and "scripts/setup-wsl-dev" in caps["reason"]


@pytest.mark.redteam
def test_a_device_that_is_not_a_member_of_the_pool_cannot_ask(tmp_path):
    with device(tmp_path, enrol_sender=False) as one:
        with pytest.raises(ShardError):
            sender(one).capability(one.fingerprint)
        assert one.runner.snapshot() == []


@pytest.mark.redteam
@pytest.mark.parametrize("header,why", [
    ({"argv": ["sh", "-c", "id"]}, "takes no field called 'argv'"),
    ({"tier": "bash"}, "the tier is one of"),
    ({"files": ["../etc/passwd"]}, "only tests/NAME.py files run in a shard"),
    ({"files": ["tests/a.py", "tests/a.py"]}, "names each test file once"),
    ({"timeout_s": 99999}, "the time limit is a whole number of seconds"),
    ({"id": "../../x"}, "the shard id is 32 lowercase hex digits"),
])
def test_a_request_that_names_a_command_or_leaves_its_shape_is_refused_and_nothing_runs(tmp_path, header, why):
    with device(tmp_path) as one:
        base = {"id": "ab" * 16, "tree_sha256": "0" * 64, "tier": "all", "files": ["tests/test_pass.py"], "timeout_s": 60}
        with pytest.raises(Exception, match=why):
            one.member.shard_start({**base, **header})
        assert one.runner.snapshot() == []


@pytest.mark.redteam
def test_a_tree_that_was_never_uploaded_or_does_not_match_its_digest_is_refused(tmp_path):
    with device(tmp_path) as one:
        header = {"id": "cd" * 16, "tree_sha256": "0" * 64, "tier": "all", "files": ["tests/test_pass.py"], "timeout_s": 60}
        with pytest.raises(Exception, match="has not been uploaded"):
            one.member.shard_start(header)
        (one.root / "files" / "shards" / header["id"]).mkdir(parents=True)
        (one.root / "files" / "shards" / header["id"] / "tree.tgz").write_bytes(b"not the tree that was digested")
        with pytest.raises(Exception, match="does not match its digest"):
            one.member.shard_start(header)
        assert one.runner.snapshot() == []


@pytest.mark.redteam
def test_a_shard_belongs_to_the_member_that_started_it(tmp_path):
    with device(tmp_path) as one:
        shards = sender(one)
        shard = shards.send(one.fingerprint, real_tree(tmp_path / "tree"), "all", ["tests/test_pass.py"], 120)
        shards.wait(one.fingerprint, shard, timeout_s=120, poll_s=0.2)
        stranger = tls.identity(tmp_path / "other-member", "other")
        Pool(one.root / "pool.key").enrol("lab", stranger.beacon, "other", "test")
        http.pin(f"127.0.0.1:{one.httpd.server_port}", tls.pinned_context(one.ident.beacon, who=stranger))
        for call in (lambda: one.member.shard_state(shard), lambda: one.member.shard_cancel(shard)):
            with pytest.raises(Exception, match="no such shard"):
                call()


def test_a_cancelled_shard_leaves_nothing_running(tmp_path):
    pids = tmp_path / "pids"
    with device(tmp_path) as one:
        shards = sender(one)
        shard = shards.send(one.fingerprint, make_tree(tmp_path / "tree", pids), "all", ["tests/test_hang.py"], 600)
        deadline = time.monotonic() + 60
        while not pids.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        runner_pid, child_pid = (int(x) for x in pids.read_text().split())
        assert one.member.shard_state(shard)["state"] == "running"
        assert shards.cancel(one.fingerprint, shard)["state"] == "cancelled"
        deadline = time.monotonic() + 60
        while any(alive(p) for p in (runner_pid, child_pid)) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not alive(runner_pid) and not alive(child_pid)
        with pytest.raises(ShardError, match="cancelled"):
            shards.wait(one.fingerprint, shard, timeout_s=10, poll_s=0.1)


class Board:
    """The session of this device's node, as far as a run uses it: the lines posted to the board."""

    def __init__(self) -> None:
        self.posted: list[tuple] = []
        self.session = self

    def post(self, channel: str, kind: str, text: str, subject: str = "") -> None:
        self.posted.append((channel, kind, text, subject))


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_device_that_disappears_during_a_run_ends_it_loudly_and_nothing_is_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "LOST_S", 1.0)
    pids = tmp_path / "pids"
    one = serve(tmp_path)
    try:
        shards = sender(one)
        shard = shards.send(one.fingerprint, make_tree(tmp_path / "tree", pids), "all", ["tests/test_hang.py"], 600)
        deadline = time.monotonic() + 60
        while not pids.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        one.httpd.shutdown()
        one.httpd.server_close()
        with pytest.raises(ShardError, match=r"box stopped answering .* while shard .* ran there.*were not run anywhere else"):
            shards.wait(one.fingerprint, shard, timeout_s=60, poll_s=0.2)
    finally:
        for pid in map(int, pids.read_text().split()) if pids.exists() else ():
            with contextlib.suppress(OSError):
                os.kill(pid, 9)
        one.runner.shutdown()


def test_scripts_test_on_runs_the_files_on_a_member_in_dev_mode_and_exits_with_its_status(tmp_path, monkeypatch, capsys):
    tree = real_tree(tmp_path / "tree")
    monkeypatch.setenv("DEV_TEST_REUSE_DIR", str(tmp_path / "reuse"))
    with device(tmp_path) as one:
        rows = [{"name": "box", "fingerprint": one.fingerprint, "addr": one.member.base_url, "connected": True, "reach": "direct",
                 "reach_hint": "", "via": dev.VIA, "also": []}]
        board = Board()
        opened = farm.Farm(rows, lambda: board, sender(one))
        monkeypatch.setattr(test_on, "open_farm", lambda: opened)
        monkeypatch.setattr(test_on, "tree_hash", lambda root: "tree")
        args = argparse.Namespace(tier="all", on="box", split=False, base="main", timeout=120.0)
        status = test_on.main(args, ["tests/test_pass.py"], tree, lambda tier, file: [sys.executable, "-m", "pytest", file], True)
        shown = capsys.readouterr().out
        assert status == 0 and "+ ran on box" in shown and "2 passed" in shown
        assert [line for _, _, line, _ in board.posted if line.startswith("test-result device=box ")], "the result is posted to the board, keyed by device"
        status = test_on.main(args, ["tests/test_broken.py"], tree, lambda tier, file: [sys.executable, "-m", "pytest", file], True)
        assert status == 1 and "FAILED tests/test_broken.py::test_broken::test_wrong" in capsys.readouterr().out


def test_the_listing_of_devices_shows_a_member_in_dev_mode_and_whether_it_takes_tests(tmp_path, capsys, monkeypatch):
    from poolhouse.testfarm import devices as listing

    with device(tmp_path) as one:
        rows = [{"name": "box", "fingerprint": one.fingerprint, "addr": "", "connected": True, "reach": "direct", "reach_hint": "", "via": "dev", "also": []}]
        opened = farm.Farm(rows, lambda: pytest.fail("the node was not asked"), sender(one))
        monkeypatch.setattr(listing, "open_farm", lambda: opened)
        monkeypatch.setenv("DEV_TEST_REUSE_DIR", str(tmp_path / "reuse"))
        assert listing.run(argparse.Namespace(device="all", json=False)) == 0
        table = capsys.readouterr().out
        assert "box" in table and "dev" in table and "yes" in table


def test_a_device_listed_through_the_node_and_the_pool_is_asked_by_the_node_first(tmp_path):
    asked = []

    class Node:
        def capability(self, fingerprint):
            asked.append(("node", fingerprint))
            return {"accepts": False, "reason": "shards are off on that node"}

    class Pool:
        def capability(self, fingerprint):
            asked.append(("pool", fingerprint))
            return {"accepts": True, "reason": ""}

    rows = farm.merged([{"name": "box", "fingerprint": "n" * 64, "via": "node"}], [{"name": "box", "fingerprint": "p" * 64, "via": "dev"}])
    assert len(rows) == 1 and rows[0]["also"][0]["fingerprint"] == "p" * 64
    both = farm.Farm(rows, lambda: Node(), Pool())
    assert both.capability("n" * 64)["accepts"] is True
    assert asked == [("node", "n" * 64), ("pool", "p" * 64)], "the pool in dev mode answers only after the node said no"
    assert json.dumps(both._chosen["n" * 64]) == json.dumps(("dev", "p" * 64))

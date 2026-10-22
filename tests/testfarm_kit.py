"""Real node processes for the tests-on-another-device suites: members on loopback, this process as one session.

The nodes listen on 127.0.0.1 only and run no beacon, so nothing uses multicast or asks the person for network
permission. ``pool`` is device A (the asker, this process's session) and B (the host), already joined; ``third``
makes a node C, alone in a pool of its own, for the tests that need a stranger.
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import test_on
from node_kit import STRIPPED, node_binary  # noqa: F401  (a fixture the ones below use)
from pool_check import wire
from pool_check.person import RigPerson
from testfarm_tree import make_tree

from poolhouse import (
    features,
    node_binary as node_binary_module,
    node_health,
    node_launch,
    node_supervise,
)
from poolhouse.board import grants, session as board_session
from poolhouse.board.client import Client
from poolhouse.testfarm.client import Shards, pool_devices

BOARD = "demo"
ROOT = Path(__file__).resolve().parents[1]
POOL_ACTIONS = ("add_device", "join_pool", "member_remove", "sync_now", "shard_call", "shard_consent")
"""What a test device's session does in a pool: the pool calls and the shard calls. In a governed pool each is a standing grant that an approval of the pool's admin key wrote."""
LASTS_MS = 6 * 3_600_000
CARRIERS = itertools.count(1)
"""Names the sessions that carry an approval; one answers one request."""


@dataclass
class Device:
    root: Path
    state: Path
    client: Client
    token: str
    name: str = ""
    person: RigPerson | None = None
    """The test person who granted this device's session what it may do."""

    def call(self, method: str, board: str = "", token: str = "", **params):
        return self.client.call(method, board, token, **params)


    def admin(self, method: str, **params):
        return self.call(method, token=self.token, **params)

    def certificate(self) -> str:
        return (self.state / "device.cert").read_bytes().hex()


def start(binary: Path, name: str, person: RigPerson) -> Device:
    """A node process on a short temporary root, listening on loopback, with one session registered on the board."""
    root = Path(tempfile.mkdtemp(prefix="ml", dir="/tmp"))
    state = root / "node"
    state.mkdir(mode=0o700)
    wire.plant_phone_key(state, person.key.public.hex())
    node_supervise.point(state, binary, node_binary_module.sha256(binary))
    node_launch.ensure_node(state, extra=["--listen", "127.0.0.1:0", *wire.PRESENCE])
    client = Client(state)
    got = client.call("register", BOARD, "", model="claude-sonnet-5-5", harness="claude-code", session=name)
    return Device(root, state, client, got["token"], got["name"], person)


def skip_unless_possible() -> None:
    if sys.version_info[:2] != (3, 13) or os.name == "nt":
        pytest.skip("shards run on Python 3.13, and these node processes are POSIX")


def governed(device: Device) -> None:
    """Wait until ``device`` has the pool's charter, which a joiner learns by syncing: until then it knows no admin to ask."""
    until("the joined device to hold the pool's charter", 30, lambda: device.call("pool_status")["governance"]["mode"] == "governed")


def approve(device: Device, *actions: str) -> None:
    """The test pool's admin key approves what the session of ``device`` asks for, the way a person's phone does: the session asks, a second session
    carries the signed answer (the one that asks cannot), and the node writes the standing grants. A governed pool counts no grant any other way."""
    key = device.person.key  # the admin key of the pool and, planted by `start`, the phone key the node takes approvals from
    asked = device.call("approval_request", BOARD, device.token, asks=[{"action": a, "resource": "*", "lasts_ms": LASTS_MS} for a in actions], note="the test farm's pool")
    carrier = device.call("register", BOARD, "", model="claude-sonnet-5-5", harness="claude-code", session=f"{device.name}-carrier-{next(CARRIERS)}")["token"]
    node = node_health.call(device.state, "pool_status")["fingerprint"]
    waiting = device.call("approval_pending", "", carrier, **grants.sign_list(key, node, int(time.time() * 1000)))["approvals"]
    request = next(a for a in waiting if a["id"] == asked["id"])
    device.call("approval_decide", "", carrier, **grants.sign_approval(key, request, "approve"), pick=asked.get("code", ""))


def found(device: Device) -> None:
    """``device`` founds a governed pool the test admin key signs and is its first member, and its session holds what the tests do in a pool."""
    if device.person is None:
        raise RuntimeError("the test pool has no admin key")
    grants.issue(board_session.Session(device.client, BOARD, device.token, device.name), device.person.key, grants.Want("pool_create", {"kind": "session", "name": device.name}))
    device.person.found(device, "secure")
    approve(device, *POOL_ACTIONS)


def join(a: Device, b: Device) -> None:
    """Found ``a``'s pool and enrol ``b`` in it with a join code."""
    found(a)
    enter(a, b)


def enter(host: Device, joiner: Device) -> None:
    """Enrol ``joiner``, a node alone in no pool, in the pool ``host`` is in: the admin key admits its certificate (the person's yes), ``host`` makes the join
    code, and once it is a member its session is approved for what the tests do in a pool."""
    host.person.admit(host, joiner, "join-code")
    approve(joiner, "join_pool")
    code = host.call("add_device", token=host.token, presigned=True)
    joiner.call("join_pool", token=joiner.token, host="127.0.0.1", port=code["port"], passphrase=code["code"])
    governed(joiner)
    approve(joiner, *(action for action in POOL_ACTIONS if action != "join_pool"))


@contextlib.contextmanager
def devices(binary: Path, *names: str, person: RigPerson | None = None) -> Iterator[list[Device]]:
    """Nodes started one after another, each stopped and its root removed when the block ends, whichever step of the setup failed."""
    made: list[Device] = []
    person = person or RigPerson()
    try:
        for name in names:
            made.append(start(binary, name, person))
        yield made
    finally:
        for one in made:
            node_launch.stop_node(one.state)
            shutil.rmtree(one.root, ignore_errors=True)


@pytest.fixture
def pool(node_binary, monkeypatch, tmp_path):  # noqa: F811
    """Two members; this process is device A's session. Returns (a, b, shards, tree root)."""
    skip_unless_possible()
    with devices(node_binary, "a", "b") as (a, b):
        join(a, b)
        for name in STRIPPED:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("POOLHOUSE_HOME", str(a.root))
        monkeypatch.setenv("POOLHOUSE_BOARD", BOARD)
        monkeypatch.setenv("POOLHOUSE_WORKSPACE_TOKEN", a.token)
        monkeypatch.setenv("DEV_TEST_REUSE_DIR", str(tmp_path / "reuse"))
        features.switch("remote-tests", True)  # this machine (device A's root) has the experimental feature on
        tree = make_tree(tmp_path / "tree", tmp_path / "pids")
        yield a, b, Shards(board_session.Session(a.client, BOARD, a.token)), tree


@pytest.fixture
def third(node_binary):  # noqa: F811
    """A node C alone in a pool of its own, started on demand; its device and the way to stop it."""
    skip_unless_possible()
    with devices(node_binary, "c") as (c,):
        found(c)
        yield c


@pytest.fixture
def joiner(node_binary, pool):  # noqa: F811
    """A node alone in no pool, started on demand with the pool's person, for the tests that bring a further device into the pool of ``pool``'s B."""
    skip_unless_possible()
    with devices(node_binary, "d", person=pool[0].person) as (d,):
        yield d


def enable(b: Device) -> None:
    """Turn shards on at ``b`` the way its person would: this interpreter, this checkout, taking tests from its one pool member."""
    (asker,) = pool_devices(b.state)
    b.call("shard_consent", token=b.token, enabled=True, python=sys.executable, repo=str(ROOT), allow=[asker["fingerprint"]])


def member(a: Device) -> dict:
    """The one other device in ``a``'s pool."""
    (found,) = pool_devices(a.state)
    return found


def go(b_fp: str, tree: Path, files: list[str], reuse: bool = True, tier: str = "all") -> int:
    """`scripts/test` with a tier and test files, run on one device by its fingerprint, in this process."""
    args = argparse.Namespace(tier=tier, on=b_fp, split=False, base="main", timeout=300.0)
    return test_on.main(args, files, tree, lambda t, f: [sys.executable, "-m", "pytest", "-n", "1", f], reuse)


def jobs_on(b: Device) -> int:
    """How many shard folders ``b``'s node holds."""
    return len(list((b.state / "shards").iterdir()))


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def until(what: str, seconds: float, check) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")

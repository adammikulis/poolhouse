"""Two devices in dev mode find one another and become one pool with nothing from a person, then one runs the other's tests.

The receiving device is a real daemon (its request handler, job runner and the join route that hands out the pool key over the TLS its
beacon names) with a real beacon on loopback; the joining device is this process, which makes its choice with `automatic_pools.ensure`
exactly as a daemon at start does. Loopback is the only network a test may use, so the beacon goes to 127.0.0.1 on a port nobody else
holds; everything after the beacon is the production path.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import threading
from pathlib import Path

import pytest
from dev_pool_kit import real_tree, this_python_runs_shards  # noqa: F401  (a fixture)

from poolhouse import http
from poolhouse.fleet import automatic_pools, tls
from poolhouse.fleet.api import Daemon, make_handler
from poolhouse.fleet.daemon import load_or_create_token
from poolhouse.fleet.discovery import Advertiser, Beacon, memberships, mint_pool
from poolhouse.fleet.framing import LimitedServer
from poolhouse.fleet.jobs import JobRunner
from poolhouse.fleet.membership_sync import admit
from poolhouse.fleet.onboard.joining import Joining
from poolhouse.fleet.pool_roster import Pool
from poolhouse.fleet.remote import Discovery, Member
from poolhouse.testfarm import dev


def udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Host:
    """A daemon in a pool in dev mode that takes in the devices that ask: its join route, its TLS listener and its beacon."""

    def __init__(self, tmp_path: Path, port: int, name: str = "host") -> None:
        self.root, self.name = tmp_path / name, name
        (self.root / "files").mkdir(parents=True)
        self.keyfile = self.root / "pool.key"
        self.pool_member = mint_pool("development", self.keyfile, mode="dev", selection="automatic")
        self.ident = tls.identity(self.root / "tls", name)
        self.roster = Pool(self.keyfile)
        self.roster.enrol("development", self.ident.beacon, name, "self")
        self.runner = JobRunner(self.root)
        fingerprint = hashlib.sha256(base64.b64decode(self.ident.beacon)).hexdigest()
        joining = Joining(lambda: memberships(self.keyfile), lambda: fingerprint, enrol=lambda group, cert, who: admit(self.roster, group, cert, who),
                          log=lambda text: None)
        daemon = Daemon(self.runner, self.root / "files", load_or_create_token(self.root, self.pool_member.key), name=name, pool_key_path=self.keyfile, members=self.roster,
                        pool_mode="dev", joining=joining)
        self.httpd = LimitedServer(("127.0.0.1", 0), make_handler(daemon), tls=tls.member_context(self.ident, self.roster))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        beacon = Beacon(name=name, port=self.httpd.server_port, cert=self.ident.beacon, machine=f"machine-of-{name}")
        self.advertiser = Advertiser(beacon, self.pool_member.key, pool="development", port=port, interval_s=0.2)
        self.advertiser.mode, self.advertiser.joinable = "dev", True
        self.advertiser.start()

    def stop(self) -> None:
        self.advertiser.stop()
        self.runner.shutdown()
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def host(tmp_path):
    port = udp_port()
    one = Host(tmp_path, port)
    one.port = port
    yield one
    one.stop()
    http._PINNED.clear()


def test_a_device_with_no_pool_joins_the_pool_in_dev_mode_it_hears_and_then_runs_its_tests(host, tmp_path):
    mine = tmp_path / "me" / "pool.key"
    joined = automatic_pools.ensure(mine, mode="dev", port=host.port)
    assert joined.key == host.pool_member.key and joined.mode == "dev" and joined.group == "development", "the device took the pool it heard"
    assert memberships(mine) == [joined]
    me = tls.local().beacon
    assert host.roster.groups_of(dev.fingerprint_of(me)) == {"development"}, "the host listed this device as a member when it asked"
    members = Member.discover(discovery=Discovery(timeout_s=3.0, key=joined.key, group="development", port=host.port))
    assert [m.name for m in members] == ["host"], "the host answers the beacon of the pool this device joined, pinned by its certificate"
    lines: list[str] = []
    shards = dev.DevShards({dev.fingerprint_of(members[0].beacon.cert): members[0]}, say=lines.append)
    fingerprint = dev.fingerprint_of(members[0].beacon.cert)
    assert shards.capability(fingerprint)["accepts"] is True
    shard = shards.send(fingerprint, real_tree(tmp_path / "tree"), "all", ["tests/test_pass.py"], 120)
    result = shards.wait(fingerprint, shard, timeout_s=120, poll_s=0.2)
    assert result["exit"] == 0 and result["files"]["tests/test_pass.py"]["passed"] == 2
    (job,) = [j for j in members[0].jobs() if j["id"].startswith("shard-")]
    assert job["by"].startswith(dev.fingerprint_of(me)), "the job names the device that sent it"


@pytest.mark.redteam
def test_a_device_in_production_mode_is_not_taken_in_by_a_pool_in_dev_mode(host, tmp_path):
    mine = tmp_path / "me" / "pool.key"
    mint_pool("private", mine, mode="prod")
    with pytest.raises(Exception, match="explicitly admitted"):
        automatic_pools.ensure(mine, mode="dev", port=host.port)
    assert [m.group for m in memberships(mine)] == ["private"]
    assert host.roster.groups_of(dev.fingerprint_of(tls.local().beacon)) == set()


@pytest.mark.redteam
def test_a_pool_not_in_dev_mode_does_not_hand_its_key_to_a_device_that_asks(tmp_path):
    port = udp_port()
    one = Host(tmp_path, port, name="vault")
    try:
        one.advertiser.stop()
        mint_pool("development", one.keyfile, mode="prod")
        one.advertiser = Advertiser(Beacon(name="vault", port=one.httpd.server_port, cert=one.ident.beacon, machine="machine-of-vault"),
                                    memberships(one.keyfile)[0].key, pool="development", port=port, interval_s=0.2)
        one.advertiser.mode = "prod"
        one.advertiser.start()
        mine = tmp_path / "me" / "pool.key"
        joined = automatic_pools.ensure(mine, mode="dev", port=port)
        assert joined.key != memberships(one.keyfile)[0].key, "the device made a pool of its own rather than take the key of one that needs approval"
        assert one.roster.groups_of(dev.fingerprint_of(tls.local().beacon)) == set()
    finally:
        one.stop()
        http._PINNED.clear()

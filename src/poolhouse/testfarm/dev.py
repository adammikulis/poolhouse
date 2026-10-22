"""The sending side of test shards in a pool in dev mode: its other members, and a tree sent to one.

Members are found the way `poolhouse-members ls` finds them and spoken to over the TLS the pool pinned for them, signed with the
pool's key. Everything a shard needs goes through the member's own routes (`fleet/shard_routes.py`); the member decides from the
certificate it saw who asked. A member that stops answering while a shard runs there ends the run with a message that says so.
"""

from __future__ import annotations

import json
import secrets
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from poolhouse import home
from poolhouse.fleet import shard_tree
from poolhouse.fleet.discovery import DiscoveryError, memberships
from poolhouse.fleet.membership import fingerprint_of
from poolhouse.fleet.remote import Discovery, Member, MemberError
from poolhouse.log import say as default_say

from .client import ShardError

POLL_S = 2.0
LOST_S = 45.0
VIA = "dev"


def plain(exc: MemberError) -> str:
    """What a member's refusal says, in its own words when it sent them."""
    body = getattr(exc, "body", "") or ""
    try:
        said = json.loads(body).get("error", "") if body else ""
    except (ValueError, AttributeError):
        said = ""
    return said or str(exc)


def reached(timeout_s: float = 2.0) -> list[tuple[dict, Member]]:
    """The other members of the pool this device is in, as device rows and the client for each; none when it is not in dev mode."""
    joined = [m for m in memberships() if m.mode == "dev"]
    if not joined:
        return []
    own, found = home.machine_id(), []
    for pool in joined:
        for member in Member.discover(discovery=Discovery(timeout_s=timeout_s, key=pool.key, group=pool.group)):
            beacon = member.beacon
            if beacon is None or beacon.machine == own or not beacon.cert:
                continue
            fingerprint = fingerprint_of(beacon.cert)
            found.append(({"name": beacon.name, "fingerprint": fingerprint, "addr": f"{beacon.host}:{beacon.port}", "connected": True,
                           "reach": "direct", "reach_hint": "", "via": VIA}, member))
    return found


def devices(timeout_s: float = 2.0) -> list[dict]:
    """The other members of this device's pool in dev mode, as `testfarm.client.pool_devices` rows."""
    try:
        return [row for row, _ in reached(timeout_s)]
    except DiscoveryError:
        return []


class DevShards:
    """Calls to the shard routes of the members of a pool in dev mode, by the fingerprint of the member."""

    def __init__(self, members: dict[str, Member] | None = None, *, say: Callable[[str], None] | None = None) -> None:
        self.members = members if members is not None else {row["fingerprint"]: member for row, member in reached()}
        self.say = say or (lambda text: default_say(text, flush=True))
        self.session = None

    def member(self, device: str) -> Member:
        """The client for ``device``, or ShardError when it is not a member this device can see."""
        try:
            return self.members[device]
        except KeyError:
            raise ShardError(f"{device[:16]} is not a member of this device's pool in dev mode") from None

    def capability(self, device: str) -> dict:
        """What ``device`` says about taking shards: ``accepts`` (and ``reason`` when not), platform, python, free slots."""
        try:
            return self.member(device).shard_caps()
        except MemberError as exc:
            raise ShardError(plain(exc)) from None

    def send(self, device: str, root: Path, tier: str, files: list[str], timeout_s: int) -> str:
        """Pack the tree at ``root``, upload it to ``device`` and start the run; the shard id. ShardError when the device takes no tests."""
        caps = self.capability(device)
        if not caps["accepts"]:
            raise ShardError(f"{self.name_of(device)} takes no tests: {caps['reason']}")
        try:
            tree, digest = shard_tree.pack(root)
        except (shard_tree.TreeError, OSError) as exc:
            raise ShardError(str(exc)) from None
        shard, member = secrets.token_hex(16), self.member(device)
        try:
            with tempfile.TemporaryDirectory(prefix="shard-send-") as scratch:
                path = Path(scratch) / "tree.tgz"
                path.write_bytes(tree)
                member.push(path, f"shards/{shard}/tree.tgz")
            member.shard_start({"id": shard, "tree_sha256": digest, "tier": tier, "files": files, "timeout_s": timeout_s})
        except MemberError as exc:
            raise ShardError(plain(exc)) from None
        return shard

    def wait(self, device: str, shard: str, *, timeout_s: float, poll_s: float = POLL_S,
             tick: Callable[[], None] = lambda: None) -> dict:
        """Poll until the shard has ended, writing its output as it arrives; its result, or ShardError when it failed or the member went away."""
        member, name = self.member(device), self.name_of(device)
        deadline, since, silent = time.monotonic() + timeout_s, 0, None
        while True:
            try:
                got = member.shard_state(shard, since)
                silent = None
            except MemberError as exc:
                silent = silent or time.monotonic()
                if time.monotonic() - silent > LOST_S:
                    raise ShardError(f"{name} stopped answering {LOST_S:.0f}s ago while shard {shard} ran there ({plain(exc)}); "
                                     "its tests did not finish and were not run anywhere else") from None
                time.sleep(poll_s)
                continue
            since = got.get("next", since)
            for line in str(got.get("output", "")).splitlines():
                self.say(f"[{name}] {line}")
            if got["state"] == "done":
                return got["result"]
            if got["state"] in ("failed", "cancelled"):
                raise ShardError(f"shard {shard} {got['state']} on {name}: {got.get('error', '')}")
            if time.monotonic() > deadline:
                self.cancel(device, shard)
                raise ShardError(f"shard {shard} still running on {name} after {timeout_s:.0f}s; cancelled")
            tick()
            time.sleep(poll_s)

    def name_of(self, device: str) -> str:
        """How the member is called on screen."""
        beacon = self.member(device).beacon
        return beacon.name if beacon else device[:12]

    def cancel(self, device: str, shard: str) -> dict:
        """Stop a shard on ``device`` and everything it started."""
        try:
            return self.member(device).shard_cancel(shard)
        except MemberError as exc:
            raise ShardError(plain(exc)) from None

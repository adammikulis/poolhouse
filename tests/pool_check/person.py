"""The person of the rig's throwaway pool: a key the rig made, enrolled on each node it starts, that signs the standing grants the rig's sessions need.

A node allows a session nothing beyond its own board's messages, notes, claims and leases until a grant names it, and counts a grant only
when a person key enrolled on that node signed it (docs/node.md, "Standing grants"). The rig runs the pool as a person would, so it does what
a person does and nothing else: it leaves the hash of a one-time secret in the node's own state directory (the way `grants-setup` does,
`grants.expect_enrolment`), enrols its key through the node's real `person_enroll`, and signs each grant through the real `grant_issue`. The
key is an in-memory Ed25519 key that belongs to this run alone: it is never written beside a node, never in a keystore and never the key of
the account, and nothing here is reachable from the node's own code or from a production path, so there is no switch for a node to honour.
What the rig does not exercise is the custody of a person's real key: the typed `yes` on a terminal and the keystore (docs/pool-check.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from pool_check.device import BOARD, Device

from poolhouse.board import grants
from poolhouse.fleet.onboard.manifest import Signer

POOL = "pool"
ABOUT = "the pool check's person"
LASTS_MS = 6 * 3_600_000
POOL_ACTIONS = ("set_join_policy", "add_device", "join_pool", "member_remove", "sync_now")
"""What the rig's pool session does on each device: the pool calls every step makes."""
SESSION_ACTIONS = ("shard_call", "shard_consent")
"""What the rig's session of the demo board does: send tests to another device, and (as the receiver) name who may."""


class NoLineage(RuntimeError):
    """The node cannot read which process asks (Windows, docs/node.md "Identity"), so a signature cannot name the terminal it was made in."""


@dataclass(frozen=True)
class Holder:
    """A session of a device: the one a grant names, and whose token carries the request that asks the node to put the grant on its board."""

    device: Device
    board: str
    token: str
    name: str


class RigPerson:
    """One key for the run, enrolled on every device by `setup`, and the signing of grants, revocations and key changes with it."""

    def __init__(self) -> None:
        self.key = Signer.generate()

    @property
    def fingerprint(self) -> str:
        return grants.fingerprint(self.key.public)

    def enrol(self, holder: Holder, key: Signer | None = None) -> dict:
        """Put the run's key (or ``key``, approved by the run's key) on the node of ``holder``'s device, carried by ``holder``'s session.

        The first key goes in with the one-time secret the rig leaves in the node's state directory; a later key with the approval of one
        the node holds, for this node, once, from this process's terminal.
        """
        device, carried = holder.device, key or self.key
        if key is None or not device.call("person_keys")["keys"]:
            secret = device.wire("expect-enrol", "--state", device.state_text)["result"]["secret"]
            return device.call("person_enroll", "", holder.token, **grants.enrolment(carried, ABOUT), secret=secret)
        return device.call("person_enroll", "", holder.token, **grants.approve(grants.enrolment(carried, ABOUT), self.key, self.where(device)))

    def where(self, device: Device) -> grants.Where:
        """The node and the terminal a signature made now is good for."""
        lineage = device.call("lineage")["lineage"]
        if not lineage:
            raise NoLineage(f"{device.name}'s node reads no process lineage here, so it takes no signature that names a terminal")
        top = lineage[-1]
        return grants.Where(str(device.hello()["device"]), {"pid": int(top["pid"]), "start": int(top["start"])})

    def want(self, holder: Holder, action: str, resource: str = "*", uses: int | None = None) -> grants.Want:
        """What the run's person grants ``holder`` for ``action``: on its device only, for six hours."""
        return grants.Want(action, {"kind": "session", "name": holder.name}, resource, holder.device.fingerprint, LASTS_MS, uses)

    def try_allow(self, holder: Holder, action: str, key: Signer | None = None, *, resource: str = "*", uses: int | None = None) -> dict:
        """What the node answers when ``key`` (the run's by default) signs the grant: the envelope, so a key that counts for nothing can be shown refused."""
        signed = grants.sign_issue(key or self.key, holder.board, self.want(holder, action, resource, uses))
        return holder.device.try_call("grant_issue", holder.board, holder.token, **signed)

    def allow(self, holder: Holder, action: str, key: Signer | None = None, *, resource: str = "*", uses: int | None = None) -> str:
        """``key`` (the run's by default) grants ``holder``'s session ``action``; the grant's id. The session only carries the request."""
        out = self.try_allow(holder, action, key, resource=resource, uses=uses)
        if not out["ok"]:
            raise RuntimeError(f"{holder.device.name}: the grant of {action} to {holder.name} was refused: {out['code']}: {out['error']}")
        return str(out["result"]["gid"])

    def revoke(self, holder: Holder, gid: str) -> dict:
        """End the grant ``gid`` of ``holder``'s board at once."""
        return holder.device.call("grant_revoke", holder.board, holder.token, **grants.sign_revoke(self.key, holder.board, gid, "the pool check ends it"))

    def remove(self, holder: Holder, fingerprint: str) -> dict:
        """Take the key ``fingerprint`` off the node of ``holder``'s device, signed by the run's key for this node and this process's terminal."""
        return holder.device.call("person_unenroll", "", holder.token, **grants.sign_unenroll(self.key, fingerprint, self.where(holder.device)))

    def status(self, holder: Holder, gid: str) -> str:
        """How the node lists the grant ``gid`` on ``holder``'s board: live, exhausted, revoked, ignored ..."""
        listed = holder.device.call("grants", holder.board, holder.token)["grants"]
        return next((g["status"] for g in listed if g["gid"] == gid), "(not listed)")

    def setup(self, device: Device) -> None:
        """Enrol the run's key on a node that has just started and registered its sessions, and grant them what the steps use."""
        token, name = device.admin_session()
        pool = Holder(device, POOL, token, name)
        self.enrol(pool)
        for action in POOL_ACTIONS:
            self.allow(pool, action)
        demo = Holder(device, BOARD, device.token, device.session)
        for action in SESSION_ACTIONS:
            self.allow(demo, action)

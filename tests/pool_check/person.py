"""The person of the rig's throwaway pool: a key the rig made, enrolled on each node it starts, that signs the standing grants the rig's sessions need.

A node allows a session nothing beyond its own board's messages, notes, claims and leases until a grant names it, and counts a grant only
when a person key enrolled on that node signed it (docs/node.md, "Standing grants"). The rig runs the pool as a person would, so it does what
a person does and nothing else: it says yes to the question the node itself writes on a terminal (`wire.answer_presence`, a typed `yes` at a
pseudo-terminal, against the node as shipped, which has no switch that answers for anyone), enrols its key through the node's real `person_enroll`
with the one-time secret that yes made, and signs each grant through the real `grant_issue`. The
key is an in-memory Ed25519 key that belongs to this run alone: it is never written beside a node, never in a keystore and never the key of
the account, and nothing here is reachable from the node's own code or from a production path, so there is no switch for a node to honour.
What the rig does not exercise is the custody of a person's real key: the typed `yes` on a terminal and the keystore (docs/pool-check.md).

The same person is the admin of the pools the rig founds (`found`, `admit`, `remove`): a second key, also in memory and also the run's alone,
named in each pool's charter. The node never holds it. It drafts an act (`pool_draft`, `pool_draft_found`), the rig signs the bytes it is given
outside the node, and the node takes the signed act back (`pool_submit`, `pool_found`), as a person's own device would hand it one.
"""

from __future__ import annotations

from dataclasses import dataclass

from pool_check.device import BOARD, Device

from poolhouse.board import grants
from poolhouse.fleet.onboard.manifest import Signer

POOL = "pool"
ABOUT = "the pool check's person"
LASTS_MS = 6 * 3_600_000
POOL_ACTIONS = ("add_device", "join_pool", "member_remove", "sync_now", "pool_create")
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
        self.admin = Signer.generate()  # the admin key of the pools the rig founds: it signs acts outside every node

    @property
    def fingerprint(self) -> str:
        return grants.fingerprint(self.key.public)

    def enrol(self, holder: Holder, key: Signer | None = None) -> dict:
        """Put the run's key (or ``key``, approved by the run's key) on the node of ``holder``'s device, carried by ``holder``'s session.

        The first key goes in with the one-time secret the node makes when the rig's person types yes to its question; a later key with the approval of one
        the node holds, for this node, once, from this process's terminal.
        """
        device, carried = holder.device, key or self.key
        if key is None or not device.call("person_keys")["keys"]:
            secret = self.says_yes(device, holder.token, "key")["secret"]
            return device.call("person_enroll", "", holder.token, **grants.enrolment(carried, ABOUT), secret=secret)
        return device.call("person_enroll", "", holder.token, **grants.approve(grants.enrolment(carried, ABOUT), self.key, self.where(device)))

    def says_yes(self, device: Device, token: str, purpose: str) -> dict:
        """The person types yes to the node's question for the first ``purpose`` (`key` or `phone`) as the session ``token``: the node's reply, which
        carries the one-time secret once, to the session that asked."""
        out = device.wire("presence", "--state", device.state_text, "--token", token, "--purpose", purpose, "--about", ABOUT, check=False)
        if not out["ok"] or out["result"]["state"] != "confirmed":
            raise RuntimeError(f"{device.name}: the person's yes to the node's question was not taken: {out}")
        return dict(out["result"])

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

    def signed(self, drafted: dict) -> dict:
        """The act of a `pool_draft` reply with the admin key's signature over the bytes the node gave."""
        act = dict(drafted["act"])
        sig = self.admin.sign_bytes(bytes.fromhex(drafted["sign_bytes"])).hex()
        act["signatures"] = sorted([*act["signatures"], {"kind": "admin", "key": self.admin.public.hex(), "sig": sig}], key=lambda s: (s["kind"], s["key"]))
        return act

    def found(self, device: Device, policy: str, title: str = "pool check") -> dict:
        """``device`` founds a pool of ``policy`` governed by the admin key and is its first member: the charter is accepted by the key outside the node."""
        keys = [{"key": self.admin.public.hex(), "person": "pool-check", "strength": "strong", "label": "the pool check's admin"}]
        draft = device.admin("pool_draft_found", title=title, policy=policy, project="", keys=keys)
        charter = draft["charter"]
        for wanted in draft["accept"]:
            accepting = next(k for k in charter["admins"] if k["key"] == wanted["key"])
            accepting["acceptance"] = self.admin.sign_bytes(bytes.fromhex(wanted["sign_bytes"])).hex()
        founded = device.admin("pool_found", charter=charter)
        self.admit(device, device, "found")
        return founded

    def admit(self, host: Device, joiner: Device, how: str) -> dict:
        """The admin key admits ``joiner`` (by the certificate in its state directory) to the pool ``host`` is in; the act is submitted to ``host``."""
        der = joiner.certificate()
        draft = host.admin("pool_draft", kind="admit", body={"cert": der, "name": joiner.name, "how": how, "via": ""})
        return host.admin("pool_submit", act=self.signed(draft))

    def remove_member(self, host: Device, fingerprint: str) -> dict:
        """The admin key removes the member ``fingerprint`` from the pool ``host`` is in; the act is submitted to ``host``."""
        draft = host.admin("pool_draft", kind="remove", body={"fingerprint": fingerprint})
        return host.admin("pool_submit", act=self.signed(draft))

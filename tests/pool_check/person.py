"""The person of the rig's throwaway pool: a key the rig made, enrolled on each node it starts, that signs the standing grants the rig's sessions need.

A node allows a session nothing beyond its own board's messages, notes, claims and leases until a grant names it, and counts a grant only
when a person key enrolled on that node signed it (docs/node.md, "Standing grants"). The key is an in-memory Ed25519 key that belongs to this
run alone: never in a keystore and never the key of the account. It is enrolled the way a phone's key is, as a file the account owns that the
node reads when it starts (`wire.plant_phone_key`), because a node derives a key as strong only when a phone enrolled it, and no call of the
node, and no switch it honours, can enrol one that way. The rig signs each grant through the real `grant_issue`. What the rig does not exercise
is the person's yes to the question the node writes on a terminal, the custody of a person's real key and the phone's own signing
(docs/pool-check.md); founding no longer asks for it, and only the node's own tests and the test kit's `enrol_first` type it.

The same key is the admin of the pools the rig founds (`found`, `admit`, `remove`), named in each pool's charter. The node never holds it. It
drafts an act (`pool_draft`, `pool_draft_found`), the rig signs the bytes it is given outside the node, and the node takes the signed act back
(`pool_submit`, `pool_found`), as a person's own device would hand it one.
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
        self.admin = self.key  # the admin key of the pools the rig founds: it signs acts outside every node

    @property
    def fingerprint(self) -> str:
        return grants.fingerprint(self.key.public)

    def enrol(self, holder: Holder, key: Signer | None = None) -> dict:
        """Put ``key`` (the run's, already on the node from its start, by default) on the node of ``holder``'s device, carried by ``holder``'s session.

        A key the node holds stays as it is; the first key on an empty node goes in with the one-time secret the node makes when the rig's person types
        yes to its question; a later key with the approval of one the node holds, for this node, once, from this process's terminal.
        """
        device, carried = holder.device, key or self.key
        held = {k["fingerprint"] for k in device.call("person_keys")["keys"]}
        if grants.fingerprint(carried.public) in held:
            return {"fingerprint": grants.fingerprint(carried.public)}
        if not held:
            secret = self.says_yes(device, holder.token, "key", carried.public.hex())["secret"]
            return device.call("person_enroll", "", holder.token, **grants.enrolment(carried, ABOUT), secret=secret)
        return device.call("person_enroll", "", holder.token, **grants.approve(grants.enrolment(carried, ABOUT), self.key, self.where(device)))

    def says_yes(self, device: Device, token: str, purpose: str, public: str = "") -> dict:
        """The person types yes to the node's question for the first ``purpose`` (`key` or `phone`) as the session ``token``: the node's reply, which
        carries the one-time secret once, to the session that asked."""
        out = device.wire("presence", "--state", device.state_text, "--token", token, "--purpose", purpose, "--public", public, check=False)
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
        """``device`` founds a pool of ``policy`` governed by the admin key and is its first member: the charter names the device and is accepted by the key outside the node."""
        keys = [{"key": self.admin.public.hex(), "person": "pool-check", "label": "the pool check's admin"}]
        draft = device.admin("pool_draft_found", title=title, policy=policy, project="", keys=keys)
        charter = draft["charter"]
        for wanted in draft["accept"]:
            accepting = next(k for k in charter["admins"] if k["key"] == wanted["key"])
            accepting["acceptance"] = self.admin.sign_bytes(bytes.fromhex(wanted["sign_bytes"])).hex()
        return device.admin("pool_found", charter=charter)

    def admit(self, host: Device, joiner: Device, how: str) -> dict:
        """The admin key admits ``joiner`` (by the certificate in its state directory) to the pool ``host`` is in; the act is submitted to ``host``."""
        der = joiner.certificate()
        draft = host.admin("pool_draft", kind="admit", body={"cert": der, "name": joiner.name, "how": how, "via": ""})
        return host.admin("pool_submit", act=self.signed(draft))

    def remove_member(self, host: Device, fingerprint: str) -> dict:
        """The admin key removes the member ``fingerprint`` from the pool ``host`` is in; the act is submitted to ``host``."""
        draft = host.admin("pool_draft", kind="remove", body={"fingerprint": fingerprint})
        return host.admin("pool_submit", act=self.signed(draft))

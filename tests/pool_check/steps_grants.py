"""Steps about the standing grants (docs/node.md): what a session may do on a node is what a person's enrolled key signed, nothing more.

The rig is the person of its own pool (`person.RigPerson`), so every other step runs on grants; these steps are the ones that show the grants
themselves hold on each operating system: refused with none, allowed under one, once under a one-use grant, refused again when it is revoked,
and refused when the key that signed it is removed.
"""

from __future__ import annotations

from pool_check.device import Device, Refused
from pool_check.person import POOL, Holder
from pool_check.report import Skip
from pool_check.world import World, eventually

from poolhouse.board import grants
from poolhouse.fleet.onboard.manifest import Signer

MISSING = "does not hold the grant for "


def _refused(holder: Holder, method: str, action: str = "", **params) -> str:
    """The reason the node refused ``method`` to ``holder``'s session; an assertion error when it was allowed or refused for another reason than the missing grant of ``action`` (the method's name when none is given)."""
    out = holder.device.try_call(method, "", holder.token, **params)
    assert not out["ok"], f"{holder.device.name} allowed {method} to a session that holds no grant for it: {out}"
    assert MISSING + (action or method) in out["error"], f"{holder.device.name} refused {method} for another reason than the missing grant: {out['error']}"
    return str(out["error"])


def _holders(device: Device, name: str) -> tuple[Holder, Holder]:
    """The rig's own session on ``device`` (the one the person granted) and a session beside it, ``name``, that no person has granted anything."""
    token, own = device.admin_session()
    return Holder(device, POOL, token, own), Holder(device, POOL, *device.bare_session(POOL, name))


def a_session_with_no_grant_is_refused_until_a_person_signs_one(world: World) -> str:
    """In a governed pool a pool action is refused to a session no person has granted, and to a grant a key that is not enrolled signed; allowed under a person-signed grant, only as often as a one-use grant says, and refused again once the grant is revoked; a granted removal still waits for the person's own signature, and the join policy never moves."""
    world.fresh()
    world.pool_of(world.devices)
    person, first, victim = world.person, world.first, world.devices[-1]
    rig, bare = _holders(first, "no-grants")
    for method, action, params in (("add_device", "", {"ttl_s": 120}), ("member_remove", "", {"fingerprint": victim.fingerprint}), ("pool_propose", "admin_manage", {"kind": "remove", "body": {"fingerprint": victim.fingerprint}})):
        _refused(bare, method, action, **params)
    assert victim.fingerprint in {m["fingerprint"] for m in first.others()}, "a session with no grant removed a member"
    moved = first.try_call("set_join_policy", "", rig.token, policy="open")
    assert not moved["ok"] and "reissue" in moved["error"], f"a granted session moved the join policy of a governed pool: {moved}"
    forged = person.try_allow(bare, "add_device", Signer.generate())
    assert not forged["ok"], f"a grant signed by a key the node does not hold was taken: {forged}"
    _refused(bare, "add_device", ttl_s=120)
    once = person.allow(bare, "add_device", uses=1)
    first.call("add_device", "", bare.token, ttl_s=120, presigned=True)
    _refused(bare, "add_device", ttl_s=120)
    assert person.status(rig, once) == "exhausted", person.status(rig, once)
    standing = person.allow(bare, "add_device")
    first.call("add_device", "", bare.token, ttl_s=120, presigned=True)
    person.revoke(rig, standing)
    _refused(bare, "add_device", ttl_s=120)
    assert person.status(rig, standing) == "revoked", person.status(rig, standing)
    person.allow(bare, "member_remove", resource=victim.fingerprint)
    asked = first.try_call("member_remove", "", bare.token, fingerprint=victim.fingerprint)
    assert not asked["ok"] and MISSING not in asked["error"] and "OK" in asked["error"], f"a granted removal was not left to the person's signature: {asked}"
    assert victim.fingerprint in {m["fingerprint"] for m in first.others()}, "a granted session with no signature of a person removed a member"
    world.remove(first, victim)
    eventually(lambda: victim.fingerprint not in {m["fingerprint"] for m in first.others()}, 30, f"{first.name} to list {victim.name} as removed after the person's signed removal")
    return (f"{first.name}: three actions refused with no grant, the join policy refused to move, and a grant signed by a key the node does not hold refused; add_device used under a one-use grant "
            f"(then refused) and under a standing grant (then revoked); member_remove granted for {victim.name} still asks for the person's signature, and the signed removal takes effect")


def a_removed_key_stops_what_it_signed(world: World) -> str:
    """A second person key, approved by the first, signs a grant that works; when the first key removes it the grant stops, what the first key signed still works, and the removed key cannot come back."""
    world.fresh()
    person = world.person
    readers = [d for d in world.devices if d.call("lineage")["lineage"]]
    if not readers:
        raise Skip("no node here reads which process asks (Windows, docs/node.md \"Identity\"), so no signature can name the terminal it was made in, and a key is approved and removed only for one")
    first = readers[-1]
    rig, bare = _holders(first, "second-key")
    second = Signer.generate()
    person.enrol(rig, second)
    signed = person.allow(bare, "add_device", second)
    first.call("add_device", "", bare.token, ttl_s=120)
    person.remove(rig, grants.fingerprint(second.public))
    _refused(bare, "add_device", ttl_s=120)
    assert person.status(rig, signed) != "live", f"the node still lists the removed key's grant as {person.status(rig, signed)}"
    first.admin("add_device", ttl_s=120)
    assert not person.try_allow(bare, "add_device", second)["ok"], "a removed key signed a new grant"
    again = None
    try:
        person.enrol(rig, second)
    except Refused as exc:
        again = exc
    assert again is not None, "a removed key was enrolled again"
    return "the second key's grant worked, then stopped when the first key removed it; the first key's grants and the rig's own session kept working"

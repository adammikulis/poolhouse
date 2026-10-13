"""Steps about joining and about what every member of the pool holds: members, entries, direct messages, claims."""

from __future__ import annotations

from pool_check.api_session import RigSession
from pool_check.world import World, everyone_holds

from poolhouse.board import founding


def join_open(world: World) -> str:
    """Under policy `open` a device on the same network joins with no code; one the first device only sees through NAT or loopback is refused with the reason."""
    world.fresh()
    first, second = world.first, world.others[0]
    world.found(first, "open")
    host, port = first.reach(second)
    out = second.try_call("join_pool", token=_admin(second), host=host, port=port, fingerprint=first.fingerprint)
    notes = []
    if out["ok"]:
        world.converged([first, second])
        by = {m["fingerprint"]: m["by"] for m in first.others()}
        assert by == {second.fingerprint: "open"}, f"{first.name} recorded the join as {by}, not by open"
        notes.append(f"{second.name} joined {first.name} by open")
    else:
        assert "same network" in out["error"], f"{second.name} was refused for a reason other than the network: {out}"
        notes.append(f"{second.name} was refused by {first.name} ({host} is not on its network): " + out["error"][:100])
    if world.count >= 3 and not world.native:
        third = world.others[1]
        if not out["ok"]:
            world.found(second, "open")
        world.join(third, second)
        world.converged([first, second, third] if out["ok"] else [second, third])
        by = {m["fingerprint"]: m["by"] for m in second.others() if m["fingerprint"] == third.fingerprint}
        assert by == {third.fingerprint: "open"}, f"{second.name} recorded the join of {third.name} as {by}, not by open"
        notes.append(f"{third.name} joined {second.name} by open on the shared Docker network")
    return "; ".join(notes)


def join_secure(world: World) -> None:
    """The first device founds a secure pool; every other device joins with a join code once the person's key has signed for it, one after another (each joins the previous one), and all list one another."""
    world.fresh()
    world.pool_of(world.devices)
    for device in world.devices:
        assert {m["by"] for m in device.others()} <= {"found", "join-code", "open"}, device.others()


def a_wrong_code_is_refused(world: World) -> None:
    """A device the person admitted that gives the wrong join code does not join, and the same code is not accepted again after three tries."""
    world.fresh()
    mac, linux = world.first, world.others[0]
    world.found(mac)
    world.person.admit(mac, linux, "join-code")
    code = mac.admin("add_device", ttl_s=120, presigned=True)
    host, port = mac.reach(linux)
    for _ in range(3):
        out = linux.try_call("join_pool", token=linux.admin_token or _admin(linux), host=host, port=port, passphrase="000000" if code["code"] != "000000" else "111111")
        assert not out["ok"], f"a wrong join code was accepted: {out}"
    right = linux.try_call("join_pool", token=linux.admin_token, host=host, port=port, passphrase=code["code"])
    assert not right["ok"], "the right code worked after three wrong tries: the window must close"
    assert linux.pool()["pool"] != mac.pool()["pool"], f"{linux.name} holds the pool of {mac.name} though it never gave the right code in time"


def found_by_api(world: World) -> str:
    """An agent founds the pool through the library's own founding code, as the shipped API runs it: the admin key the node holds as a phone's signs the charter, the node takes it, this device is the first member, a second try says it is there and a charter no key accepted founds nothing."""
    world.fresh()
    first = world.first
    session, admin = RigSession(first), founding.Admin(world.person.admin)
    assert first.pool()["governance"]["mode"] == "unfounded", "a fresh device is a pool of one that no person founded"
    unsigned = first.admin("pool_draft_found", title="no key", policy="secure", project="", keys=[{"key": admin.key.public.hex(), "person": "rig", "label": "x"}])
    refused = first.try_call("pool_found", token=session.token, charter=unsigned["charter"])
    assert not refused["ok"] and first.pool()["governance"]["mode"] == "unfounded", f"a charter nobody's key accepted founded a pool: {refused}"
    made = founding.found(session, admin, "api pool", "secure", founding.Terms(cert_hex=first.certificate()))
    gov = first.pool()["governance"]
    assert (made.name, made.already, gov["mode"], gov["name"]) == ("api pool", False, "governed", "api pool"), (made, gov)
    assert [m["status"] for m in first.members() if m.get("self")] == ["active"], "this device is the pool's first member"
    again = founding.found(session, admin, "api pool", "secure", founding.Terms(cert_hex=first.certificate()))
    assert again.already and again.pool == made.pool and "already in the pool api pool" in again.say()
    return f"{first.name} founded {made.name} ({made.pool[:8]}) by the library's founding path; the second call said it was there"


def _admin(device) -> str:
    device.admin("pool_status")
    return device.admin_token


def members_listed(world: World) -> None:
    """Each device lists every other member by name, with the address it reaches it at where it has one, and itself as self."""
    world.fresh()
    world.pool_of(world.devices)
    names = {d.name: d.fingerprint for d in world.devices}
    for device in world.devices:
        shown = {m["fingerprint"]: m for m in device.members()}
        assert len(shown) == world.count, f"{device.name} lists {len(shown)} members, not {world.count}: {sorted(m['name'] for m in shown.values())}"
        for other in world.devices:
            member = shown.get(names[other.name])
            assert member and member["status"] == "active", f"{device.name} does not list {other.name} as active: {member}"
            if other is not device:
                assert member["name"], f"{device.name} lists {other.name} without a name"
        assert sum(1 for m in shown.values() if m.get("self")) == 1
    adjacent = [(d.name, m["name"], m["addr"]) for d in world.devices for m in d.others() if m["addr"]]
    assert adjacent, "no device has an address for any member"


def every_member_converges_on_every_entry(world: World) -> None:
    """An entry written on any device is on every device: each writes one, then all hold all of them."""
    world.fresh()
    world.pool_of(world.devices)
    bodies = [f"entry from {d.name}" for d in world.devices]
    for device, body in zip(world.devices, bodies, strict=True):
        device.post(body)
    everyone_holds(world.devices, bodies)



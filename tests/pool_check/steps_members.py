"""Steps about who is in the pool: joining in any order, removal reaching every member, a swapped certificate, scale."""

from __future__ import annotations

import time

from pool_check.report import Skip
from pool_check.world import World, eventually, everyone_holds


def join_order_does_not_matter(world: World) -> None:
    """The same devices joined as a chain forwards, a chain backwards and a star on the last device all end as one pool with one board."""
    n = world.count
    orders = {"chain forwards": list(range(n)), "chain backwards": list(reversed(range(n)))}
    for label, order in orders.items():
        world.fresh()
        world.pool_of(world.devices, order=order)
        bodies = [f"{label}: from {d.name}" for d in world.devices]
        for device, body in zip(world.devices, bodies, strict=True):
            device.post(body)
        everyone_holds(world.devices, bodies)
    world.fresh()
    hub = world.devices[-1]
    for joiner in world.devices[:-1]:
        world.join(joiner, hub)
    world.converged(world.devices)
    bodies = [f"star: from {d.name}" for d in world.devices]
    for device, body in zip(world.devices, bodies, strict=True):
        device.post(body)
    everyone_holds(world.devices, bodies)


def a_removed_device_is_refused_by_every_member(world: World) -> str:
    """The Mac device removes one member; every other member, not only the one that removed it, then refuses that device's next request."""
    if world.count < 3:
        raise Skip("needs a remover, a removed device and a third member")
    world.fresh()
    world.pool_of(world.devices)
    victim, rest = world.others[-1], [d for d in world.devices if d is not world.others[-1]]
    victim.post("written while a member")
    everyone_holds(world.devices, ["written while a member"])
    for device in rest:
        host, port = device.reach(victim)
        before = victim.probe(host, port, {"op": "boards"})
        assert before["replies"] and before["replies"][0]["ok"], f"{device.name} refused {victim.name} before it was removed (the probe itself is wrong): {before}"
    world.first.admin("member_remove", fingerprint=victim.fingerprint)
    removed_at, seen_at = time.monotonic(), {}
    for device in rest:
        eventually(lambda d=device: (d.member(victim.fingerprint) or {}).get("status") == "revoked", 30, f"{device.name} to record the removal of {victim.name}")
        seen_at[f"{device.name} recorded it"] = round(time.monotonic() - removed_at, 1)
    for device in rest:
        host, port = device.reach(victim)

        def refused(host=host, port=port):
            after = victim.probe(host, port, {"op": "boards"})
            return after if "error" in after or not after["replies"][0]["ok"] else None
        eventually(refused, 30, f"{device.name} to refuse {victim.name}'s next request (not only the member that removed it)")
        seen_at[f"{device.name} refused it"] = round(time.monotonic() - removed_at, 1)
    victim.post("written after removal")
    victim.admin("sync_now")
    time.sleep(3)
    for device in rest:
        assert "written after removal" not in device.bodies(), f"{device.name} took an entry from the removed {victim.name}; " + _what_each_knows(world.devices, victim, seen_at)
    host, port = world.first.reach(victim)
    again = victim.try_call("join_pool", token=victim.admin_token, host=host, port=port, fingerprint=world.first.fingerprint)
    assert not again["ok"], f"a removed device joined again: {again}"
    shown = {d.name: next(m for m in d.members() if m["fingerprint"] == victim.fingerprint)["last_error"] for d in rest}
    return "the removed device's last error as each member left it: " + "; ".join(f"{k}: {v or '(none)'}" for k, v in shown.items())


def _what_each_knows(devices: list, victim, seen_at: dict[str, float]) -> str:
    """For a failure report: who holds the removed device's entry, how each device lists it, and when each member recorded the removal."""
    held = [d.name for d in devices if d is not victim and "written after removal" in d.bodies()]
    seen = {}
    for device in devices:
        mine = [m for m in device.members() if m["fingerprint"] == victim.fingerprint]
        seen[device.name] = {k: mine[0].get(k) for k in ("status", "addr", "connected", "reach", "last_seen_ms", "last_sync_ms", "last_error")} if mine else "(not listed)"
    return f"held by {held}; what each device says of {victim.name}: {seen}; seconds after the removal: {seen_at}"


def a_swapped_certificate_is_refused(world: World) -> None:
    """A device that comes back at a member's address with a new certificate is refused by every member, and the members that dial it refuse its pin."""
    if world.count < 3:
        raise Skip("needs three devices")
    world.fresh()
    world.pool_of(world.devices)
    target, rest = world.others[-1], [d for d in world.devices if d is not world.others[-1]]
    old = target.fingerprint
    target.stop_node()
    target.delete_files("device.*", "*.json", "tls*")
    target.start_node()
    target.register("impostor")
    assert target.fingerprint != old, "the device kept its certificate: the swap did not happen"
    target.post("from the impostor")
    for device in rest:
        host, port = device.reach(target)
        got = target.probe(host, port, {"op": "boards"})
        assert "error" in got or (got["replies"] and not got["replies"][0]["ok"]), f"{device.name} let a device with a swapped certificate ask for its boards: {got}"
    pinned = {}
    for device in rest:
        member = next(m for m in device.members() if m["fingerprint"] == old)
        if member["addr"]:
            pinned[device.name] = eventually(lambda d=device: next((m["last_error"] for m in d.members() if m["fingerprint"] == old and "pinned" in m["last_error"]), None), 30,
                                             f"{device.name}, which dials {target.name}'s address, to refuse the swapped certificate")
    assert pinned, "no member had an address for the device whose certificate was swapped, so no pin was tested"
    time.sleep(2)
    for device in rest:
        assert "from the impostor" not in device.bodies(), f"{device.name} took an entry from a device with a swapped certificate"
    assert not target.others(), f"the impostor has members: {target.others()}"
    return "refusals: " + "; ".join(f"{k}: {v[:80]}" for k, v in pinned.items())


def sync_follows_the_chain_of_dials(world: World) -> None:
    """In a chain where only neighbours know each other's address, every entry still reaches every device; the dialing is reported."""
    if world.count < 4:
        raise Skip("a chain needs four devices (run with --devices 5)")
    world.fresh()
    world.pool_of(world.devices)
    bodies = [f"relay: from {d.name}" for d in world.devices]
    for device, body in zip(world.devices, bodies, strict=True):
        device.post(body)
    everyone_holds(world.devices, bodies, seconds=60)
    dialed = {d.name: sorted(m["name"] for m in d.others() if m["addr"]) for d in world.devices}
    return "each device has an address for: " + "; ".join(f"{name} -> {', '.join(who) or 'nobody'}" for name, who in dialed.items())

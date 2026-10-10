"""Steps about trouble: a device cut off from the network, a node killed mid-sync, a lease contested from two devices."""

from __future__ import annotations

import threading
import time

from pool_check.report import Skip
from pool_check.world import World, eventually, everyone_holds

BRANCH = {"kind": "branch", "key": "pool-check/contested", "ttl_s": 600}


def _holders(devices) -> dict[str, str]:
    """Which devices hold the contested branch in their own lease table: name -> state."""
    held = {}
    for device in devices:
        for lease in device.call("lease_list", "demo")["leases"]:
            if any(r.get("name") == f"demo/{BRANCH['key']}" for r in lease["resources"]) and lease["state"] == "held":
                held[device.name] = lease["state"]
    return held


def a_partition_heals_and_both_sides_merge(world: World) -> None:
    """One Linux device is cut off from the network: both sides write and both claim a branch; after it heals every entry is everywhere and one claim holds."""
    cut = world.others[-1]
    if not cut.can_partition:
        raise Skip("cutting a device off the network needs a container (Docker); native devices share this machine's network")
    world.fresh()
    world.pool_of(world.devices)
    cut, rest = world.others[-1], [d for d in world.devices if d is not world.others[-1]]
    cut.partition(world)
    try:
        cut.post("written while cut off")
        for device in rest:
            device.post(f"written by {device.name} while {cut.name} was cut off")
        first = rest[0].call("claim", "demo", **BRANCH)
        second = cut.call("claim", "demo", **BRANCH)
        assert first["changed"] and second["changed"], "both sides should be able to claim while neither can see the other"
        time.sleep(4)
        assert "written while cut off" not in rest[0].bodies(), "an entry crossed a cut network"
    finally:
        cut.heal(world)
    bodies = ["written while cut off", *(f"written by {d.name} while {cut.name} was cut off" for d in rest)]
    everyone_holds(world.devices, bodies, seconds=60)
    again = [d for d in world.devices if _holders([d])]
    def one_holder():
        held = _holders(world.devices)
        if len(held) != 1:
            raise AssertionError(f"devices holding the branch in their own lease table: {sorted(held)}")
        return held
    holders = eventually(one_holder, 60, "exactly one device to still hold the branch pool-wide after the merge")
    assert len(again) >= 1 and len(holders) == 1, f"holders after the merge: {_holders(world.devices)}"
    entries = [sorted(d.bodies()) for d in world.devices]
    assert all(e == entries[0] for e in entries), "the devices hold different entries after the merge"


def a_node_killed_mid_sync_resumes(world: World) -> None:
    """The node of a Linux device is killed with SIGKILL while a long run of entries is arriving; its supervisor restarts it and it ends with every entry."""
    world.fresh()
    world.pool_of(world.devices)
    victim, writer = world.others[0], world.first
    total = 1500
    before = victim.message_count()
    posting = threading.Thread(target=writer.post_many, args=(total, 6000, "bulk"))
    posting.start()
    seen = eventually(lambda: before + 50 < victim.message_count() < before + total and victim.message_count() - before, 90, f"{victim.name} to be part way through {total} entries")
    pid = victim.kill_node()
    posting.join()
    eventually(lambda: victim.hello()["pid"] != pid, 30, f"the supervisor to restart {victim.name}'s node (killed pid {pid} with {seen} of {total} entries in)")
    eventually(lambda: victim.message_count() - before == total, 180, f"{victim.name} to resume and hold all {total} entries")
    for device in world.devices:
        damaged = device.call("status", "demo", device.token)["board"].get("damaged")
        assert not damaged, f"{device.name} marks a log damaged after the kill: {damaged}"
    return f"killed after {seen} of {total} entries"


def a_lease_queues_behind_another_device(world: World) -> None:
    """An area held on one device queues the same request from another, and is granted to it once the holder lets go."""
    world.fresh()
    world.pool_of(world.devices)
    holder, waiter = world.first, world.others[0]
    area = [{"type": "claim", "kind": "area", "name": "/pool-check/area"}]
    got = holder.call("lease_acquire", "demo", resources=area, ttl_s=600)
    assert got["state"] == "held", got
    eventually(lambda: waiter.call("lease_acquire", "demo", resources=area, ttl_s=600, wait=False)["state"] == "busy", 30, f"{waiter.name} to learn that {holder.name} holds the area")
    queued = waiter.call("lease_acquire", "demo", resources=area, ttl_s=600, wait=True)
    assert queued["state"] == "queued", queued
    holder.call("lease_release", "demo", id=got["id"])
    eventually(lambda: any(lease["state"] == "held" and lease["id"] == queued["id"] for lease in waiter.call("lease_list", "demo")["leases"]), 60,
               f"{waiter.name}'s queued request to be granted after {holder.name} released")

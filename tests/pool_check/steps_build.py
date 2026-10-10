"""Steps about builds: what each member runs, who shows as stale or needing an update, and a device updating itself."""

from __future__ import annotations

from pool_check.report import Skip
from pool_check.world import BUILD, World, eventually

OLD = ("oldbuild00000000000000000000000000000000", 5)


def _build_of(device, fingerprint: str) -> dict:
    return next(m for m in device.members() if m["fingerprint"] == fingerprint)


def one_stale_device_shows_as_stale_only_for_itself(world: World) -> None:
    """One device runs an older build: it shows `catching up` and takes no work, the others show it `older`, and nobody else is flagged."""
    if world.count < 3:
        raise Skip("needs three devices to show that only one is flagged")
    world.fresh(builds={1: OLD})
    world.pool_of(world.devices)
    stale, rest = world.others[0], [world.first, *world.others[1:]]
    eventually(lambda: stale.pool()["build"]["state"] == "catching up", 30, f"{stale.name} to see a newer build in the pool and say it is catching up")
    for device in rest:
        shown = eventually(lambda d=device: _build_of(d, stale.fingerprint)["build_state"] == "older" and _build_of(d, stale.fingerprint), 30, f"{device.name} to show {stale.name} as older")
        assert shown["build"] == OLD[0] and shown["build_seq"] == OLD[1], shown
        assert device.pool()["build"]["state"] == "current", f"{device.name} is flagged: {device.pool()['build']}"
        for other in rest:
            if other is not device:
                assert _build_of(device, other.fingerprint)["build_state"] == "current", f"{device.name} shows {other.name} as {_build_of(device, other.fingerprint)['build_state']}"
    refused = stale.try_call("lease_acquire", "demo", resources=[{"type": "gpu", "device": "local"}], wait=False)
    assert not refused["ok"] and "catching up" in refused["error"], f"a stale device took work: {refused}"


def a_wire_mismatch_shows_needs_update_with_the_fix(world: World) -> None:
    """A device on another wire version is shown as needing an update (with the command), and the others stay current among themselves."""
    if world.count < 3:
        raise Skip("needs three devices")
    world.fresh(wire={2: 2})
    world.pool_of(world.devices)
    odd, rest = world.others[1], [world.first, world.others[0]]
    for device in rest:
        shown = eventually(lambda d=device: _build_of(d, odd.fingerprint)["build_state"] == "needs update" and _build_of(d, odd.fingerprint), 30, f"{device.name} to show {odd.name} as needing an update")
        assert "poolhouse runtime ensure" in shown["fix"], shown
    neighbour = _build_of(world.first, world.others[0].fingerprint)
    assert neighbour["build_state"] == "current", f"a device on the same wire as the first device is shown as {neighbour['build_state']}"
    eventually(lambda: any("needs update" in (m["last_error"] or "") for m in odd.others() if m["addr"]), 30, f"{odd.name} to say why it cannot sync with a member it dials")


def a_device_on_an_old_build_updates_itself_from_the_pool(world: World) -> None:
    """The updater on a stale Linux device fetches the newer build from a local origin, swaps its node onto it, and the pool then shows it current."""
    world.fresh(builds={1: OLD})
    world.pool_of(world.devices)
    stale = world.others[0]
    eventually(lambda: stale.pool()["build"]["state"] == "catching up", 30, f"{stale.name} to learn the pool is on a newer build")
    done = stale.run(stale.script("updater.py", "--state", stale.state_text, "--binary", stale.node_binary_text(), "--listen", stale.listen_text(),
                                  "--sync-ms", "400", "--target-id", BUILD[0], "--target-seq", str(BUILD[1])), timeout=180, check=False)
    assert done.returncode == 0 and "updated" in done.stdout, f"the updater did not update the device:\n{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
    eventually(lambda: stale.pool()["build"]["state"] == "current", 30, f"{stale.name} to run the pool's build")
    for device in (world.first, *world.others[1:]):
        eventually(lambda d=device: _build_of(d, stale.fingerprint)["build_state"] == "current", 30, f"{device.name} to show {stale.name} current after its update")

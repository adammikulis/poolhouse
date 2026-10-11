"""Steps for a device behind NAT, as WSL2 is: it can dial out and nothing can dial in. What still works, and what says why it does not."""

from __future__ import annotations

import time

from pool_check.device import Device
from pool_check.report import Skip
from pool_check.steps_tests import consent, enable_feature, send
from pool_check.world import World, eventually, everyone_holds


def _need(world: World) -> Device:
    if not world.wsl:
        raise Skip("needs a device behind NAT: run with --wsl")
    return world.wsl[0]


def _pool_with_nat(world: World) -> Device:
    """The dialable devices as one pool, and the NAT'd device joined to the first device by dialing out with a code."""
    nat = _need(world)
    world.fresh()
    world.pool_of(world.devices)
    world.join(nat, world.first)
    world.converged(world.everyone)
    return nat


def the_nat_device_joins_and_the_board_flows_both_ways(world: World) -> str:
    """A device behind NAT joins by dialing out; entries it writes reach every member and entries the members write reach it."""
    nat = _pool_with_nat(world)
    began = time.monotonic()
    bodies = [f"from {d.name}" for d in world.everyone]
    for device, body in zip(world.everyone, bodies, strict=True):
        device.post(body)
    everyone_holds(world.everyone, bodies, seconds=60)
    return f"{len(bodies)} entries in every board in {time.monotonic() - began:.1f}s with {nat.name} dialing out only"


def a_claim_and_a_lease_agree_with_the_nat_device(world: World) -> None:
    """A branch claimed on the NAT'd device is refused to the first device (naming the holder), and the other way round."""
    nat = _pool_with_nat(world)
    mac = world.first
    nat.call("claim", "demo", kind="branch", key="nat/branch", ttl_s=600)

    def refused(device):
        out = device.try_call("claim", "demo", kind="branch", key="nat/branch", ttl_s=600)
        return out if not out["ok"] else None
    out = eventually(lambda: refused(mac), 30, f"the first device to be refused a branch {nat.name} holds")
    assert nat.session in out["error"] or "@d" in out["error"] or "held by" in out["error"], out
    mac.call("claim", "demo", kind="branch", key="nat/other", ttl_s=600)
    back = eventually(lambda: refused_other(nat), 30, f"{nat.name} to be refused a branch the first device holds")
    assert back


def refused_other(nat: Device):
    out = nat.try_call("claim", "demo", kind="branch", key="nat/other", ttl_s=600)
    return out if not out["ok"] else None


def the_mac_device_sees_the_build_of_the_nat_device(world: World) -> None:
    """The members show the NAT'd device's build (it tells the device it dials), and it shows theirs."""
    nat = _pool_with_nat(world)
    for device in world.devices:
        eventually(lambda d=device: next(m for m in d.members() if m["fingerprint"] == nat.fingerprint)["build_state"] == "current", 30, f"{device.name} to show {nat.name}'s build")


def tests_on_the_nat_device_say_what_they_need(world: World) -> str:
    """Tests sent to a device nobody can dial either run (over the connection it keeps open) or fail with the reason and the fix; never a hang."""
    nat = _pool_with_nat(world)
    mac = world.first
    enable_feature(mac)
    enable_feature(nat)
    nat.prepare_checkout()
    consent(mac, nat)
    began = time.monotonic()
    done = send(mac, nat)
    took = time.monotonic() - began
    text = done.stdout + done.stderr
    assert took < 200, f"the run hung for {took:.0f}s"
    assert done.returncode == 0, f"tests could not be run on {nat.name} (behind NAT) and the failure is:\n{text[-1500:]}"
    return f"passed on {nat.name} in {took:.0f}s"


def the_members_say_how_they_reach_the_nat_device(world: World) -> str:
    """The first device reaches the NAT'd device only over the connection it opened (`channel`) and prints the WSL fix; the NAT'd device reaches the first device `direct`."""
    nat = _pool_with_nat(world)
    mac = world.first

    def row(device: Device, fingerprint: str) -> dict:
        return next(m for m in device.members() if m["fingerprint"] == fingerprint)
    seen = eventually(lambda: row(mac, nat.fingerprint) if row(mac, nat.fingerprint)["reach"] == "channel" else None, 60, f"{mac.name} to reach {nat.name} over its channel")
    assert seen["network"] == "wsl-nat" and "networkingMode=mirrored" in seen["reach_hint"] and "wsl --shutdown" in seen["reach_hint"], seen
    eventually(lambda: row(nat, mac.fingerprint)["reach"] == "direct", 60, f"{nat.name} to reach {mac.name} directly")
    return f"{mac.name} -> {nat.name}: {seen['reach']} ({seen['network']}); {nat.name} -> {mac.name}: direct"


def removal_reaches_the_nat_device(world: World) -> None:
    """When the first device removes the NAT'd device, its next dial is refused by every member it can reach."""
    nat = _pool_with_nat(world)
    world.first.admin("member_remove", fingerprint=nat.fingerprint)
    nat.post("after removal")
    time.sleep(4)
    for device in world.devices:
        assert "after removal" not in device.bodies(), f"{device.name} took an entry from the removed {nat.name}"
    eventually(lambda: next(m for m in nat.members() if m["fingerprint"] == world.first.fingerprint)["last_error"], 30, f"{nat.name} to be told its dial was refused")


def the_wsl_setup_plan_names_the_nat_fixes(world: World) -> str:
    """`device_setup --dry-run` on the WSL-marked device recognises WSL and plans the firewall rules, mirrored networking and the WSL restart."""
    nat = _need(world)
    world.fresh()
    done = nat.run(["python", "-m", "poolhouse.device_setup", "--dry-run"], check=False)
    plan = done.stdout
    assert done.returncode == 0, f"the dry run failed: {done.stdout}\n{done.stderr}"
    for want in ("add Windows inbound allow rules for TCP", "networkingMode=mirrored", "restart WSL", "recommended", "reach it directly"):
        assert want in plan, f"the plan lacks {want!r}:\n{plan}\n{done.stderr}"
    return "plan lines: " + " | ".join(line.strip("- ").strip() for line in plan.splitlines() if line.strip())

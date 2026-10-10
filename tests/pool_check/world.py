"""The N devices of a check and the ways to join and reset them. Nothing outside the temporary states, the containers and the networks is touched."""

from __future__ import annotations

import contextlib
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pool_check import dock
from pool_check.device import (
    PLATFORM_NAME,
    ContainerDevice,
    Device,
    LocalDevice,
    Refused,
    WslDevice,
    WslHostedDevice,
    free_port,
)

JOIN_TTL_S = 120
BUILD = ("fakebuild0000000000000000000000000000dead", 10)
"""The build every device runs unless a step gives one a different one: an id and its place in the order of builds."""


def eventually(check: Callable[[], object], seconds: float, what: str, every: float = 0.3):
    """The first truthy ``check()`` within ``seconds``; AssertionError naming ``what`` and the last thing seen when it never holds."""
    end = time.monotonic() + seconds
    seen: object = None
    while True:
        try:
            seen = check()
            if seen:
                return seen
        except (Refused, AssertionError, RuntimeError, KeyError, ValueError, dock.DockerError) as exc:
            seen = f"{type(exc).__name__}: {exc}"
        if time.monotonic() >= end:
            raise AssertionError(f"after {seconds:.0f}s still waiting for {what}; last saw: {str(seen)[-1200:]}")
        time.sleep(every)


@dataclass
class Layout:
    """What a World is made of: how many devices, the node binary of this machine, the image of the containers, the run's name, and the kind."""

    count: int
    binary: Path
    image: str
    run: str
    wsl: int = 0
    native: bool = False
    hosted: WslHostedDevice | None = None


class World:
    """The devices and the way back to a clean start. With containers, device 0 is the machine the rig runs on and the others are Linux
    containers on one Docker network (and ``wsl`` more behind NAT); native, every device is a node process on this machine."""

    def __init__(self, layout: Layout) -> None:
        count, binary, image, run, wsl, native, hosted = (layout.count, layout.binary, layout.image, layout.run, layout.wsl, layout.native, layout.hosted)
        self.count, self.run, self.image, self.native = count, run, image, native or hosted is not None
        self.net = None if native else dock.Net.make(run)
        self.nat = dock.Net.make(run, "-nat") if wsl else None
        self.wsl: list[WslDevice] = []
        self.first = LocalDevice.make(binary)
        self.others: list[Device] = []
        self.devices: list[Device] = [self.first]
        if hosted is not None:
            self.first.listen_all = True
            self.others.append(hosted)
            self.devices.append(hosted)
            count = 1
        for index in range(1, count):
            if native:
                device: Device = LocalDevice.make(binary, f"{PLATFORM_NAME}{index}")
            else:
                port = free_port()
                box = dock.Box.start(self.net, image, index, port)
                box.put(Path(__file__).parent, "/rig")
                device = ContainerDevice(name=f"linux{index}", port=port, box=box, net=self.net)
            self.others.append(device)
            self.devices.append(device)
        for index in range(count, count + wsl):
            port = free_port()
            box = dock.Box.start(self.nat, image, index, port)
            box.put(Path(__file__).parent, "/rig")
            box.mark_as_wsl()
            self.wsl.append(WslDevice(name=f"wsl{index}", port=port, box=box, net=self.nat))

    @property
    def everyone(self) -> list[Device]:
        """Every device, the one behind NAT included; `devices` are the ones the others can dial."""
        return [*self.devices, *self.wsl]

    def fresh(self, builds: dict[int, tuple[str, int]] | None = None, *, wire: dict[int, int] | None = None) -> None:
        """Every device alone in a pool of its own, on a clean state, running ``BUILD`` (or the build given for its index)."""
        for device in self.everyone:
            self.wipe(device)
        for index, device in enumerate(self.everyone):
            build, seq = (builds or {}).get(index, BUILD)
            device.build_args = ["--build-id", build, "--build-seq", str(seq), *(["--wire-version", str(wire[index])] if wire and index in wire else [])]
            device.start_node()
            device.token = device.admin_token = ""
            device.register(f"dev{index}")
            eventually(lambda d=device: d.hello()["pid"], 15, f"{device.name}'s node to answer")

    def wipe(self, device: Device) -> None:
        """Stop the node of ``device`` and delete everything it kept."""
        with contextlib.suppress(RuntimeError, Refused):
            device.stop_node()
        device.wipe_state()

    def join(self, joiner: Device, via: Device, policy: str = "secure") -> dict:
        """``joiner`` joins the pool ``via`` is in, at the address ``via`` has for the joiner, under ``policy`` (a code, or none under open)."""
        host, port = via.reach(joiner)
        if policy == "secure":
            code = via.admin("add_device", ttl_s=JOIN_TTL_S)
            return joiner.admin("join_pool", host=host, port=port, passphrase=code["code"])
        via.admin("set_join_policy", policy="open")
        return joiner.admin("join_pool", host=host, port=port, fingerprint=via.fingerprint)

    def pool_of(self, devices: list[Device], order: list[int] | None = None, policy: str = "secure") -> None:
        """Join ``devices`` one after another into one pool: each (after the first) joins the previous one."""
        chain = [devices[i] for i in (order or range(len(devices)))]
        for joiner, via in zip(chain[1:], chain, strict=False):
            self.join(joiner, via, policy)
        self.converged(devices)

    def converged(self, devices: list[Device], seconds: float = 30) -> None:
        """Every device lists every other as an active member."""
        want = len(devices) - 1

        def check():
            counts = {d.name: len(d.others()) for d in devices}
            return all(c == want for c in counts.values()) and counts
        eventually(check, seconds, f"every device to list the other {want} members")

    def stop(self) -> list[str]:
        """Stop every node and remove the containers and networks; what of this run still exists (empty when clean)."""
        for device in self.devices:
            if isinstance(device, LocalDevice):
                with contextlib.suppress(RuntimeError, Refused):
                    device.stop_node()
                shutil.rmtree(device.root, ignore_errors=True)
        for device in [*self.others, *self.wsl]:
            if isinstance(device, ContainerDevice):
                device.box.remove()
            elif isinstance(device, WslHostedDevice):
                with contextlib.suppress(RuntimeError, Refused):
                    device.stop_node()
        for net in (self.net, self.nat):
            if net:
                net.remove()
        return [] if self.native else dock.swept(self.run)


def everyone_holds(devices: list[Device], bodies: list[str], seconds: float = 30) -> None:
    """Every device's board shows every one of ``bodies``."""
    def holds():
        missing = {d.name: sorted(set(bodies) - set(d.bodies())) for d in devices}
        if any(missing.values()):
            raise AssertionError(f"missing entries by device: { {k: v for k, v in missing.items() if v} }")
        return True
    eventually(holds, seconds, "every device to hold every entry")

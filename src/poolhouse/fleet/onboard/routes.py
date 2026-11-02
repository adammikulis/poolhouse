"""Which way a member is reached: its address on this network, its tailnet address, or not.

A route is only a path. The device on the other end is still proved by its pinned certificate
(`probe`), so a tailnet address reaches the device whose certificate was recorded or nobody.
Addresses come from the device's record, or from a member in the local ``tailscale status``
whose certificate matched; never from an announcement.

Every network path that reaches a recorded device goes through here: ``fleet fetch`` and the SSH
bootstrap target (`ssh_address`, where the SSH host key, not a certificate, is what proves the
machine).
"""

from __future__ import annotations

import hashlib
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from ..tailnet import Tailnet
from .devices import Device, Devices
from .lan import NotLocal, in_tailnet, require_local

__all__ = ["Probe", "Reach", "Route", "open_probe", "pinned_probe", "reach", "resolve", "ssh_address"]

Probe = Callable[[str, int, str], bool]
"""``probe(address, port, fingerprint)``: whether ``address`` answers with that certificate."""


class Route(StrEnum):
    LAN = "lan"
    TAILNET = "tailnet"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class Reach:
    fingerprint: str
    name: str
    route: Route
    address: str = ""

    def public(self) -> dict[str, str]:
        return {"fingerprint": self.fingerprint, "name": self.name, "route": self.route.value,
                "address": self.address}


def unverified_context() -> ssl.SSLContext:
    """A client context that accepts any certificate and so authenticates nothing; used only
    to learn which certificate the other end presents, which the caller then compares."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def pinned_probe(address: str, port: int, fingerprint: str, timeout_s: float = 2.0) -> bool:
    """Whether a TLS server at ``address:port`` presents the certificate with ``fingerprint``;
    False for a public address, no answer or a different certificate."""
    try:
        require_local(address, port)
        with socket.create_connection((address, port), timeout=timeout_s) as raw, \
                unverified_context().wrap_socket(raw, server_hostname=None) as tls:
            der = tls.getpeercert(binary_form=True) or b""
    except (OSError, ssl.SSLError):
        return False
    return hashlib.sha256(der).hexdigest() == fingerprint


def lan_address(device: Device) -> str:
    """The address on this network recorded for the device, or ''."""
    if not device.address or in_tailnet(device.address):
        return ""
    try:
        require_local(device.address)
    except OSError:
        return ""
    return device.address


def tailnet_candidates(device: Device, tailnet: Tailnet) -> list[str]:
    """Tailnet addresses worth trying for ``device``: the one stored, and those of an online
    member of the local tailnet that goes by the device's name. Empty when the tailnet is down."""
    if not tailnet.up:
        return []
    found = [device.tailnet_address] if in_tailnet(device.tailnet_address) else []
    for name in (device.hostname, device.name):
        member = tailnet.member_named(name)
        if member is not None and member.online:
            found.extend(member.addresses)
    return list(dict.fromkeys(found))


def open_probe(address: str, port: int, _fingerprint: str = "", timeout_s: float = 3.0) -> bool:
    """Whether a TCP connection to ``address:port`` opens (a local or tailnet address only). For
    a service that proves itself some other way, such as SSH with its host key."""
    try:
        require_local(address, port)
        with socket.create_connection((address, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def reach(device: Device, tailnet: Tailnet | Callable[[], Tailnet], port: int,
          probe: Probe = pinned_probe) -> Reach:
    """How ``device`` is reached now: the network first, then the tailnet (``tailnet`` may be a
    function, called only when the network address did not answer)."""
    lan = lan_address(device)
    if lan and probe(lan, port, device.fingerprint):
        return Reach(device.fingerprint, device.name, Route.LAN, lan)
    for address in tailnet_candidates(device, tailnet() if callable(tailnet) else tailnet):
        if probe(address, port, device.fingerprint):
            return Reach(device.fingerprint, device.name, Route.TAILNET, address)
    return Reach(device.fingerprint, device.name, Route.UNREACHABLE)


def _joined(host: str, devices: Devices) -> Device | None:
    name = host.strip().lower()
    return next((d for d in devices.all() if d.status == "active"
                 and name in (d.name.lower(), d.hostname.lower())), None)


def ssh_address(host: str, port: int, devices: Devices, tailnet: Callable[[], Tailnet],
                probe: Probe = open_probe) -> str:
    """The address to run SSH against for ``host``: ``host`` itself unless it names an active
    member, in which case the first of its network address and its tailnet addresses where
    ``port`` answers (`NotLocal` when none). Only the path changes: SSH still insists on the host
    key the owner confirmed for the machine."""
    device = _joined(host, devices)
    if device is None:
        return host
    found = reach(device, tailnet, port, probe)
    if found.route is Route.UNREACHABLE:
        raise NotLocal(f"{device.name} does not answer on port {port} on the network or the tailnet")
    return found.address


def resolve(host: str, port: int, devices: Devices, tailnet: Callable[[], Tailnet],
            probe: Probe = pinned_probe) -> str:
    """The address to connect to for ``host``: ``host`` itself unless it is the name of an
    active member (``tailnet`` is called only then), in which case the address `reach` finds (`NotLocal` when none)."""
    name = host.strip().lower()
    for device in devices.all():
        if device.status == "active" and name in (device.name.lower(), device.hostname.lower()):
            found = reach(device, tailnet(), port, probe)
            if found.route is Route.UNREACHABLE:
                raise NotLocal(f"{device.name} is not reachable on the network or the tailnet")
            return found.address
    return host

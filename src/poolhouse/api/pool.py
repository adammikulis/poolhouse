"""``ph.pool``: the pool of devices this device belongs to; who is in it, how it is joined, and what it can compute.

A pool is the set of devices that trust one another. Every device starts as a pool of one that no person founded (`found` makes the
pool, governed by the person's key). Another device JOINS it: under the `open` policy automatically when both are on the same network, under `secure` with a short
code that `add_device` makes. A member is removed with `remove`. See docs/api.md.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import psutil

from poolhouse import errors, hub, node_join, node_launch, node_pool
from poolhouse.api import _node
from poolhouse.board import founding
from poolhouse.board.client import Client, NodeError
from poolhouse.fleet.onboard.lan import primary_ip

__all__ = ["Capacity", "Founding", "Member", "Pool", "Refusal", "add_device", "capacity", "found", "join", "leave", "listen", "members",
           "policy", "remove", "renew", "status"]
Refusal = founding.Refusal
"""A call that could not go on: ``str`` is one sentence to pass on; ``needs`` (`approval`, `device`, `time`, `none`), ``where`` (what is missing,
and where) and ``then`` (`wait`, `retry`, `ask-owner`, `stop`) say what happens next. A subclass of `ph.Denied`."""
DEFAULT_PORT = 7447
NOT_AN_ADDRESS = "That is not a device's address: type what the other device shows, like 192.168.1.20 or 192.168.1.20:7447."
SCOPED_ADDRESS = "An address with a % in it works only on the computer that shows it: type another address the other device shows."


@dataclass(frozen=True, slots=True)
class Member:
    """One device of the pool, as this device's node knows it."""

    name: str
    fingerprint: str
    """The device's identity: SHA-256 of its public key, 64 hex digits."""
    active: bool
    """False for a device that was removed."""
    this_device: bool
    connected: bool
    address: str
    last_seen_ms: int
    last_sync_ms: int
    """When the boards of the two devices last agreed (milliseconds since the epoch); 0 when never."""
    last_error: str = ""
    reach: str = "unknown"
    """How this device reaches it: ``direct`` (it dialed the member and it answered), ``channel`` (the member dials this device
    and requests go down its open connection), ``none`` (no address answers and no connection is open), ``self``; ``unknown``
    when the node is older than the field."""
    reach_hint: str = ""
    """The exact fix when ``reach`` is not ``direct``; empty otherwise."""


@dataclass(frozen=True, slots=True)
class Pool:
    """The pool of this device: its id, its project, how devices join it and who is in it."""

    id: str
    project: str
    policy: str
    fingerprint: str
    listening: str
    """The address this device's node listens on for other devices; empty while its network is off (a pool of one)."""
    joining_open: bool
    """True while a code made by `add_device` can still be used."""
    members: tuple[Member, ...] = field(default_factory=tuple)
    governance: str = "unfounded"
    """``unfounded`` (a pool of one that no person founded: it admits nobody) or ``governed`` (a pool its admins' signed acts run)."""
    name: str = ""
    """The founded pool's name; empty while unfounded."""
    missing: str = ""
    """One sentence for the person on what this pool still needs from them (a pool, a strong key, a signature); empty when nothing."""


@dataclass(frozen=True, slots=True)
class Founding:
    """What `found` did."""

    pool: Pool
    already: bool
    """True when this device was in a founded pool already and nothing changed."""
    say: str
    """One sentence to tell the person."""


@dataclass(frozen=True, slots=True)
class Capacity:
    """What this device can give the pool: cores and slots, memory, accelerators, and what is leased now."""

    device: str
    cpu_cores: int
    cpu_slots: int
    cpu_slots_leased: int
    memory_bytes: int
    memory_available_bytes: int
    accelerators: tuple[str, ...]
    accelerator_memory_bytes: int
    unified_memory: bool
    leases_held: int
    leases_queued: int


def _member(raw: dict) -> Member:
    name = raw.get("name") or str(raw["fingerprint"])[:12]
    return Member(str(name), str(raw["fingerprint"]), raw.get("status") == "active", bool(raw.get("self")),
                  bool(raw.get("connected")), str(raw.get("addr") or ""), int(raw.get("last_seen_ms") or 0),
                  int(raw.get("last_sync_ms") or 0), str(raw.get("last_error") or ""), str(raw.get("reach") or "unknown"),
                  str(raw.get("reach_hint") or ""))


def _missing(gov: dict) -> str:
    """What a pool still needs from its person, as one sentence."""
    if gov.get("mode") != "governed":
        return str(gov.get("say") or "This device is in no pool yet; ask me to make one or to join one.")
    if gov.get("dispute"):
        return str(gov["dispute"]["say"])
    if gov.get("waiting_for_signers"):
        return "A change to this pool is waiting for more approvals from the keys that govern it."
    return ""


def status() -> Pool:
    """The pool this device belongs to, with every member (removed ones too) and what governs it; starts this device's node if needed."""
    raw = _node.call("pool_status")
    gov = node_pool.governance(raw.get("governance"))
    return Pool(str(raw["pool"]), str(raw.get("project") or ""), str(raw["policy"]), str(raw["fingerprint"]),
                str(raw.get("listen") or ""), bool(raw.get("join_code_open")),
                tuple(_member(m) for m in raw.get("members", [])), "governed" if gov.get("mode") == "governed" else "unfounded",
                str(gov.get("name") or ""), _missing(gov))


def members(*, include_removed: bool = False) -> list[Member]:
    """The devices of the pool, this one included; the removed ones only with ``include_removed``."""
    return [m for m in status().members if m.active or include_removed]


def policy() -> str:
    """How devices join this pool: ``open`` (automatic on the same network) or ``secure`` (with a code)."""
    return status().policy


def listen(*, agreed: bool = False) -> Pool:
    """Turn this device's network on so other devices can join: the node listens on TCP 7447 and
    sends a signed beacon on UDP 7448 on the local network. How devices join is the policy of the pool a person founded.

    This changes what the machine exposes, so it is explicit: pass ``agreed=True`` to say so. Nothing is asked.
    Without it, `Error`.
    """
    if not agreed:
        raise errors.Error("turning the network on changes what this machine exposes: pass agreed=True to do it")
    node_join.join(node_launch.default_state())
    return status()


def add_device(ttl_s: int = 300) -> dict[str, object]:
    """Make a short-lived code for another device to join this pool with (policy ``secure``).

    Returns what the node answers (the ``code``, how long it is valid, its ``port`` and ``fingerprint``) with ``addresses``, what the other
    device types as this one's address (the one on the LAN first; empty while this device's network is off), and the pool's ``policy``. Show
    the code and an address to the person at the other device; they are typed there (or passed to `join`) within ``ttl_s`` seconds. The
    device's own session opens the window with the ticket the node makes at once for exactly that window; an agent's session that is not given
    one needs the grant ``add_device``. Raises `Refusal` with the node's sentence, for one on a device that cannot let others in.
    """
    params: dict[str, object] = {"ttl_s": ttl_s}
    person = _node.person_session()
    session = person or _node.session()
    try:
        params |= founding.ticket_for(session, "add_device", {"ttl_s": ttl_s})
    except founding.Refusal as refused:
        if person is not None or refused.needs != "none":
            raise  # a session the node makes no ticket for goes on with the grant it holds
    try:
        made = dict(_node.call("add_device", params, token=session.token))
    except errors.NotRunning:
        raise
    except errors.Error as err:
        raise founding.Refusal(str(err).removeprefix("denied: "), needs="none", then="ask-owner") from None
    here = status()
    return {**made, "addresses": _shown_addresses(here.listening), "policy": here.policy}


def _shown_addresses(listening: str) -> list[str]:
    """What another device types to reach the node listening on ``listening``: for a node on every interface, this computer's address on
    each network that is up, the LAN's first; with the port when it is not the usual one. Empty while the node's network is off."""
    host, _, port = listening.rpartition(":")
    host = host.strip("[]")
    if not host or not port.isdigit():
        return []
    if ipaddress.ip_address(host).is_unspecified:  # every interface, as a node on the LAN listens
        up = {name for name, link in psutil.net_if_stats().items() if link.isup}
        held = [a.address for name, many in psutil.net_if_addrs().items() if name in up for a in many if a.family == socket.AF_INET]
        usable = [h for h in dict.fromkeys(held) if not (ip := ipaddress.ip_address(h)).is_loopback and not ip.is_link_local]
        first = primary_ip()
        hosts = sorted(usable, key=lambda h: h != first)
    else:
        hosts = [host]
    if int(port) == DEFAULT_PORT:
        return hosts
    return [f"[{h}]:{port}" if ":" in h else f"{h}:{port}" for h in hosts]


def _typed_address(text: str, port: int) -> tuple[str, int]:
    """The host and port in what a person typed or pasted: ``host``, ``host:port``, an IPv6 address (in brackets when a port follows), or
    a web address, whose host is taken and whose port, the web page's, is not. Raises `Error` with one sentence for anything else."""
    said, named = text.strip(), ""
    try:
        if "://" in said:
            parts = urlsplit(said)
            said = (parts.hostname or "") if parts.scheme in ("http", "https") else ""
        else:
            said = said.rstrip("/")
            if said.startswith("["):
                said, closed, rest = said[1:].partition("]")
                if not closed or rest[:1] not in ("", ":"):
                    raise ValueError(text)
                named = rest[1:]
            elif said.count(":") == 1:
                said, _, named = said.partition(":")
        if "%" in said:
            raise errors.Error(SCOPED_ADDRESS)
        if ":" in said:
            ipaddress.IPv6Address(said)
        elif not re.fullmatch(r"[A-Za-z0-9._-]{1,45}", said):
            raise ValueError(text)
        if named:
            if not named.isdecimal() or not 0 < int(named) < 65536:
                raise ValueError(text)
            if port not in (DEFAULT_PORT, int(named)):
                raise errors.Error(f"The address names port {int(named)} and port {port} was given too: give one of them.")
            port = int(named)
    except ValueError:
        raise errors.Error(NOT_AN_ADDRESS) from None
    return said, port


def join(host: str, code: str = "", *, port: int = DEFAULT_PORT, fingerprint: str = "") -> dict[str, object]:
    """Join the pool of the device at ``host``; this device becomes a member.

    ``host`` is what the other device shows: an address or name, ``host:port`` (else ``port``), an IPv6 address, or a web address pasted
    from its page. A ``secure`` pool takes the ``code`` its `add_device` made. An ``open`` pool on this device's own network takes no code,
    only the ``fingerprint`` of the device at ``host`` (64 hex digits, from its beacon or its `add_device`). A device already in a pool does
    not change pool; the grant to join is signed at once by this device's key when policy allows it. Raises `Refusal`, whose message is one
    sentence for the person, or `Error` for what is not an address.
    """
    if not code and not fingerprint:
        raise errors.Error("give the code the other device showed, or for an open pool the fingerprint of the device that answers")
    host, port = _typed_address(host, port)
    person = _node.person_session()
    session = person or _node.session()
    params = {"host": host, "port": port, **({"passphrase": code} if code else {"fingerprint": fingerprint})}
    try:
        params |= founding.ticket_for(session, "join_pool", dict(params))
    except founding.Refusal as refused:
        if person is not None or refused.needs != "none":
            raise

    def send() -> dict[str, object]:
        return dict(session.client.call("join_pool", "", session.token, **params))

    try:
        return founding.with_card(session, send)
    except NodeError as err:
        raise founding.Refusal(str(err), needs="none", then="ask-owner") from None


def found(name: str | None = None, policy: str = "secure", *, admin: founding.Admin | None = None) -> Founding:
    """Make this device's pool, governed by the person's key: the one step that turns a pool of one into a pool others can join.

    ``policy`` is ``secure`` (devices join with a code, the default) or ``open`` (devices on this network join by themselves). Nobody is asked
    and nothing waits: this computer makes its own key for the pool and signs the charter with it (``admin`` names another key, another computer's, that
    signs instead). A device that is in a founded pool says so and changes nothing. Raises `Refusal` (an `ph.Denied`) whose message is one
    sentence an agent passes on, with ``needs``, ``where`` and ``then`` for what to do next.
    """
    state = node_launch.default_state()
    node_launch.ensure_node(state)
    made = founding.found_here(Client(state), name, policy, admin=admin)
    return Founding(status(), made.already, made.say())


def remove(device: str) -> Member:
    """Remove a member by name or fingerprint: it can no longer sync boards or take work. Not this device."""
    found = [m for m in members() if device in (m.name, m.fingerprint)]
    if len(found) != 1:
        names = ", ".join(m.name for m in members()) or "none"
        raise errors.Error(f"{'no' if not found else 'two'} member is called {device!r} (members: {names})")
    try:
        founding.govern(Client(node_launch.default_state()), "remove", {"fingerprint": found[0].fingerprint})
    except founding.NoComputerKey:
        _node.call("member_remove", {"fingerprint": found[0].fingerprint}, token=_node.session().token)
    return next(m for m in members(include_removed=True) if m.fingerprint == found[0].fingerprint)


def renew() -> Member:
    """Give this device another 30 days of admitting devices without asking (the delegate a pool starts with): a change to who governs the pool, so
    this computer's key signs it at once and it takes effect after the pool's wait. Until then a window is still opened with a ticket (`add_device`).
    Raises `Refusal`."""
    this = next(m for m in members() if m.this_device)
    founding.govern(Client(node_launch.default_state()), "delegate", {"fingerprint": this.fingerprint})
    return this


def leave() -> dict[str, object]:
    """Leave the pool this device is in: the node forgets it and this device becomes a pool of one."""
    return dict(_node.call("pool_leave", token=_node.session().token))


def capacity() -> Capacity:
    """What this device gives the pool: cores, memory, accelerators and the lease table's slots, held and queued.

    Only this device is measured here; the capacity of the other members is planned (docs/api.md, "planned").
    """
    node_name = status()
    session = _node.session()
    table = session.call("lease_list")
    cfg = table.get("config", {})
    held = [x for x in table.get("leases", []) if x.get("state") == "held"]
    slots = sum(r.get("count", 0) for x in held for r in x.get("resources", []) if r.get("type") == "cpu_slots")
    memory = hub.machine_memory()
    return Capacity(
        _this_name(node_name), os.cpu_count() or 1, int(cfg.get("cpu_slots", 0)), int(slots), memory.total_ram,
        memory.available_ram, tuple(g.name for g in memory.gpus), memory.vram_total or memory.gpu_limit_bytes,
        memory.unified, len(held), sum(1 for x in table.get("leases", []) if x.get("state") == "queued"))


def _this_name(pool: Pool) -> str:
    return next((m.name for m in pool.members if m.this_device), pool.fingerprint[:12])

"""``ph.pool``: the pool of devices this device belongs to; who is in it, how it is joined, and what it can compute.

A pool is the set of devices that trust one another. Every device starts as a pool of one that no person founded (`found` makes the
pool, governed by the person's key). Another device JOINS it: under the `open` policy automatically when both are on the same network, under `secure` with a short
code that `add_device` makes. A member is removed with `remove`. See docs/api.md.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from poolhouse import errors, hub, node_join, node_launch
from poolhouse.api import _node
from poolhouse.board import founding, presence
from poolhouse.board.client import Client, NodeError

__all__ = ["Capacity", "Founding", "Member", "Pool", "Refusal", "add_device", "capacity", "found", "join", "listen", "members",
           "policy", "remove", "status", "sync"]
Refusal = founding.Refusal
"""A call that needs the person: ``str`` is one sentence to pass on; ``needs`` (`approval`, `device`, `time`, `none`), ``where`` (where they
tap or answer) and ``then`` (`wait`, `retry`, `ask-owner`, `stop`) say what happens next. A subclass of `ph.Denied`."""
DEFAULT_PORT = 7447


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
    if gov.get("weak_keys_only"):
        return founding.NEEDS_PHONE
    if gov.get("waiting_for_signers"):
        return "A change to this pool is waiting for a signature from your phone."
    return ""


def _governance(raw: object) -> dict:
    """Reject an incomplete node response rather than treating it as no pool."""
    if not isinstance(raw, dict):
        raise errors.Error("The pool node returned an invalid governance state.")
    mode = raw.get("mode")
    governed_fields = {"name", "epoch", "charter_hash", "admins", "weak_keys_only", "thresholds", "holds",
                       "pending", "dispute", "waiting_for_signers", "delegates", "refused"}
    if mode == "unfounded":
        valid = ({"mode", "say", "old_format_file"} <= raw.keys() and not governed_fields.intersection(raw)
                 and isinstance(raw.get("say"), str) and bool(raw["say"].strip())
                 and isinstance(raw.get("old_format_file"), str))
    elif mode == "governed":
        expected = {"mode", *governed_fields}
        epoch = raw.get("epoch")
        digest = raw.get("charter_hash")
        dispute = raw.get("dispute")
        valid = (expected <= raw.keys() and "old_format_file" not in raw
                 and isinstance(raw.get("name"), str) and bool(raw["name"].strip())
                 and type(epoch) is int and epoch >= 0 and isinstance(digest, str) and len(digest) == 64
                 and all(c in "0123456789abcdef" for c in digest)
                 and isinstance(raw.get("admins"), list) and type(raw.get("weak_keys_only")) is bool
                 and isinstance(raw.get("thresholds"), dict) and isinstance(raw.get("holds"), dict)
                 and (raw.get("pending") is None or isinstance(raw["pending"], dict))
                 and (dispute is None or (isinstance(dispute, dict) and isinstance(dispute.get("say"), str) and bool(dispute["say"].strip())))
                 and all(isinstance(raw.get(key), list) for key in ("waiting_for_signers", "delegates", "refused")))
    else:
        valid = False
    if not valid:
        raise errors.Error("The pool node returned an invalid governance state.")
    return raw


def status() -> Pool:
    """The pool this device belongs to, with every member (removed ones too) and what governs it; starts this device's node if needed."""
    raw = _node.call("pool_status")
    gov = _governance(raw.get("governance"))
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

    This changes what the machine exposes, so it is a person's decision: ``agreed=True`` says one made it
    (the `poolhouse` first run asks; a script passes it on the person's order). Without it, `Error`.
    """
    if not agreed:
        raise errors.Error("turning the network on is a person's decision: pass agreed=True once they said yes")
    node_join.join(node_launch.default_state())
    return status()


def add_device(ttl_s: int = 300) -> dict[str, object]:
    """Make a short-lived code for another device to join this pool with (policy ``secure``).

    Returns what the node answers: the code and how long it is valid. Show the code to the person at the other
    device; it is typed there (or passed to `join`) within ``ttl_s`` seconds.
    """
    return dict(_node.call("add_device", {"ttl_s": ttl_s}, token=_node.session().token))


def join(host: str, code: str = "", *, port: int = DEFAULT_PORT, fingerprint: str = "") -> dict[str, object]:
    """Join the pool of the device at ``host``; this device becomes a member.

    A ``secure`` pool takes the ``code`` its `add_device` made. An ``open`` pool on this device's own network takes no code, only the
    ``fingerprint`` of the device at ``host`` (64 hex digits, from its beacon). A device already in a pool does not change pool; if a
    phone is linked, the grant to join is asked of it. Raises `Refusal`, whose message is one sentence for the person.
    """
    if not code and not fingerprint:
        raise errors.Error("give the code the other device showed, or for an open pool the fingerprint of the device that answers")
    session = _node.session()
    params = {"host": host, "port": port, **({"passphrase": code} if code else {"fingerprint": fingerprint})}

    def send() -> dict[str, object]:
        return dict(session.client.call("join_pool", "", session.token, **params))

    try:
        return founding.with_card(session, send)
    except NodeError as err:
        raise founding.Refusal(str(err), needs="none", then="ask-owner") from None


def found(name: str | None = None, policy: str = "secure", *, admin: founding.Admin | None = None,
          say: Callable[[str], object] | None = None) -> Founding:
    """Make this device's pool, governed by the person's key: the one step that turns a pool of one into a pool others can join.

    ``policy`` is ``secure`` (devices join with a code, the default) or ``open`` (devices on this network join by themselves). The person
    answers where only they can: the first key at the prompt this computer shows, the grant on the phone's card. The agent never signs
    for them. A device that is in a founded pool says so and changes nothing. Raises `Refusal` (an `ph.Denied`) whose message is one
    sentence an agent passes on, with ``needs``, ``where`` and ``then`` for what to do next.
    ``say`` is told, while the call waits, where the person answers (a sentence for the agent to pass on).
    """
    made = founding.found_here(Client(node_launch.default_state()), name, policy, admin=admin, voice=presence.Voice(say=say or (lambda _text: None)))
    return Founding(status(), made.already, made.say())


def remove(device: str) -> Member:
    """Remove a member by name or fingerprint: it can no longer sync boards or take work. Not this device."""
    found = [m for m in members() if device in (m.name, m.fingerprint)]
    if len(found) != 1:
        names = ", ".join(m.name for m in members()) or "none"
        raise errors.Error(f"{'no' if not found else 'two'} member is called {device!r} (members: {names})")
    _node.call("member_remove", {"fingerprint": found[0].fingerprint}, token=_node.session().token)
    return next(m for m in members(include_removed=True) if m.fingerprint == found[0].fingerprint)


def sync() -> None:
    """Ask the node to exchange board entries with every connected member now, rather than at its next round."""
    _node.call("sync_now", token=_node.session().token)


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

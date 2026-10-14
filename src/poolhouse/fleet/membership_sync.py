"""Keeping each device's record of a pool the same as its members': the way a join or a revocation spreads.

A device that lets another in, or puts one out, tells its members at once (`push`), and every daemon
asks its members for what it missed every `INTERVAL_S` seconds. The exchange is
``POST /fleet/v1/members`` over mutual TLS, so a member that is not itself a member of the pool is
neither heard nor answered. A revocation heard from one member reaches the rest through the others.
"""

from __future__ import annotations

import contextlib
import threading

from poolhouse import home
from poolhouse.fleet.remote import Discovery

from .discovery import DiscoveryError, Membership, memberships
from .membership import fingerprint_of
from .pool_roster import Pool
from .remote import Member, MemberError

__all__ = ["INTERVAL_S", "admit", "exchange", "push", "start", "sync_once"]

INTERVAL_S = 20.0


def exchange(pool: Pool, member: Member, group: str) -> int:
    """Swap records of ``group`` with ``member``; how many records of ours it changed."""
    asked = member.members(group, pool.export(group))
    rows = asked.get("rows") if isinstance(asked, dict) and asked.get("group") == group else None
    cert = member.beacon.cert if member.beacon else ""
    return pool.merge(group, rows, fingerprint_of(cert)) if isinstance(rows, list) else 0


def _members(member: Membership, pool: Pool, port: int | None) -> list[Member]:
    found = Member.discover(discovery=Discovery(timeout_s=1.5, key=member.key, pool_key_path=pool.path, port=port))
    return [p for p in found if p.beacon and p.beacon.machine != home.machine_id()]


def sync_once(pool: Pool, *, group: str | None = None, port: int | None = None) -> int:
    """Exchange records with every reachable member of the pool; how many of ours changed."""
    changed = 0
    for member in memberships(pool.path):
        if group not in (None, member.group):
            continue
        with contextlib.suppress(DiscoveryError, OSError):
            for daemon in _members(member, pool, port):
                with contextlib.suppress(MemberError, OSError, ValueError):
                    changed += exchange(pool, daemon, member.group)
    return changed


def admit(pool: Pool, group: str, cert: str, name: str, by: str = "join") -> None:
    """List a device that has just been let in, and tell the pool's other devices about it."""
    pool.enrol(group, cert, name, by)
    threading.Thread(target=push, args=(pool, group), name="membership-push", daemon=True).start()


def push(pool: Pool, group: str, *, port: int | None = None) -> int:
    """Tell the pool's other devices now that this machine's record of ``group`` changed."""
    return sync_once(pool, group=group, port=port)


def start(pool: Pool, *, port: int | None = None) -> threading.Event:
    """Run `sync_once` every `INTERVAL_S` until the returned event is set."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(INTERVAL_S):
            with contextlib.suppress(Exception):
                sync_once(pool, port=port)

    threading.Thread(target=loop, name="membership-sync", daemon=True).start()
    return stop

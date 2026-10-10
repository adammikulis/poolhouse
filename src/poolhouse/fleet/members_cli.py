"""``poolhouse-members roster`` -- who is in each pool this machine is in, and letting devices in and out.

Letting a device in or putting one out is for a person at a terminal: it decides who may run
commands on every machine of the pool, so a process an agent started cannot do it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from poolhouse.log import say, warn
from poolhouse.person import HumanRequired, require_person

from . import membership_sync, tls
from .discovery import DiscoveryError, discover, memberships
from .membership import Revoked, fingerprint_of
from .pool_roster import Pool


def _short(fingerprint: str) -> str:
    return fingerprint[:16]


def cmd_list(args: argparse.Namespace) -> int:
    pool = Pool(args.pool_key)
    mine = fingerprint_of(tls.local().beacon)
    held = pool.rosters()
    if not held:
        say("this machine is in no pool")
        return 1
    for member, roster in held:
        say(f"{member.group}")
        for device in roster.devices():
            note = "  (this machine)" if device.fingerprint == mine else ""
            say(f"  {device.status:8} {_short(device.fingerprint)}  {device.name or '-'}{note}")
    return 0


def cmd_self(args: argparse.Namespace) -> int:
    ident = tls.local()
    say(ident.beacon if args.cert else ident.fingerprint)
    return 0


def _pool(args: argparse.Namespace) -> str:
    groups = [m.group for m in memberships(args.pool_key)]
    if args.pool:
        if args.pool not in groups:
            raise DiscoveryError(f"this machine is not in a pool called {args.pool!r}")
        return args.pool
    if len(groups) != 1:
        raise DiscoveryError("name the pool with --pool" if groups else "this machine is in no pool")
    return groups[0]


def _text(source: str) -> str:
    return (sys.stdin.read() if source == "-" else Path(source).read_text()).strip()


def cmd_add(args: argparse.Namespace) -> int:
    """Let a device in by its certificate, after the person has read its fingerprint off that device."""
    require_person("letting a device into a pool")
    pool, group = Pool(args.pool_key), _pool(args)
    cert = _text(args.cert)
    fingerprint = fingerprint_of(cert)
    if not fingerprint:
        raise DiscoveryError("that is not a certificate: send the output of 'roster self --cert'")
    if args.fingerprint and args.fingerprint.lower() != fingerprint:
        raise DiscoveryError(f"that certificate's fingerprint is {fingerprint}, not the one named")
    pool.enrol(group, cert, args.name, "owner")
    membership_sync.push(pool, group)
    say(f"{args.name or _short(fingerprint)} ({_short(fingerprint)}) is now in {group}")
    return 0


def cmd_adopt(args: argparse.Namespace) -> int:
    """Enrol the machines that answer for the pool, one by one, each shown with its fingerprint.

    For a pool joined before devices had certificates of their own, or from a recovery file:
    compare each fingerprint with the one 'roster self' prints on that machine."""
    require_person("letting a device into a pool")
    pool, group = Pool(args.pool_key), _pool(args)
    member = next(m for m in memberships(args.pool_key) if m.group == group)
    roster = pool.roster(group)
    added = 0
    for beacon in discover(member.key, timeout_s=args.timeout, enrolled=False):
        fingerprint = fingerprint_of(beacon.cert)
        if not fingerprint or roster is None or roster.get(fingerprint) is not None:
            continue
        say(f"{beacon.name} at {beacon.host}  certificate {fingerprint}")
        if args.fingerprint:
            answer = "y" if fingerprint.startswith(args.fingerprint.lower()) else "n"
        else:
            answer = input("  let it in? [y/N] ").strip().lower()
        if answer == "y":
            pool.enrol(group, beacon.cert, beacon.name, "owner")
            added += 1
    if added:
        membership_sync.push(pool, group)
    say(f"{added} device(s) let into {group}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    """Put a device out of the pool (or of all): its next handshake and request are refused."""
    require_person("putting a device out of a pool")
    pool = Pool(args.pool_key)
    fingerprint = args.fingerprint.lower()
    if len(fingerprint) < 64:
        known = {d.fingerprint for _, r in pool.rosters() for d in r.devices() if d.fingerprint.startswith(fingerprint)}
        if len(known) != 1:
            raise DiscoveryError("name the device by its fingerprint, enough of it to be the only one")
        fingerprint = next(iter(known))
    done = pool.revoke(fingerprint, "owner", args.pool or None)
    if not done:
        raise DiscoveryError("no pool here lists that device")
    for group in done:
        membership_sync.push(pool, group)
    say(f"{_short(fingerprint)} is out of {', '.join(done)}; its certificate cannot be used to rejoin")
    return 0


def add_commands(sub: Any) -> None:
    """Register ``roster`` on a subparsers object."""
    top = sub.add_parser("roster", help="list, let in and put out the devices of a pool")
    verbs = top.add_subparsers(dest="verb", required=True)
    verbs.add_parser("list", help="the devices of every pool this machine is in")
    own = verbs.add_parser("self", help="this machine's device fingerprint (what another machine's owner compares)")
    own.add_argument("--cert", action="store_true", help="print the certificate itself, to hand to 'roster add'")
    add = verbs.add_parser("add", help="let a device in by its certificate (a person at a terminal)")
    add.add_argument("cert", help="a file holding the output of 'roster self --cert' there, or - for stdin")
    add.add_argument("--name", default="", help="what to call it")
    add.add_argument("--fingerprint", default="", help="the fingerprint you read off that device: refuse if it differs")
    adopt = verbs.add_parser("adopt", help="let in the machines that answer for the pool, each shown for your approval")
    adopt.add_argument("--timeout", type=float, default=3.0)
    adopt.add_argument("--fingerprint", default="", help="approve only the device whose fingerprint starts with this")
    revoke = verbs.add_parser("remove", help="put a device out of the pool (a person at a terminal)")
    revoke.add_argument("fingerprint", help="the device's fingerprint, or enough of its start to be the only one")
    for each in (add, adopt, revoke):
        each.add_argument("--pool", default="", help="which pool (default: the only one)")


def run(args: argparse.Namespace) -> int:
    handler = {"list": cmd_list, "self": cmd_self, "add": cmd_add, "adopt": cmd_adopt, "remove": cmd_remove}[args.verb]
    try:
        return handler(args)
    except (DiscoveryError, Revoked, ValueError, tls.TlsUnavailable) as exc:
        warn(f"error: {exc}")
        return 2
    except HumanRequired as exc:
        warn(f"error: {exc}")
        return 2


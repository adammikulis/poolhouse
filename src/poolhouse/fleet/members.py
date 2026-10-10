"""``poolhouse-members`` -- set up a pool and see who is in it."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import webbrowser
from typing import Any

from poolhouse import node_pool
from poolhouse.log import say, warn
from poolhouse.person import HumanRequired, require_person

from . import launch_open, members_cli, recovery
from .discovery import (
    MIN_JOIN_LENGTH,
    DiscoveryError,
    create_pool_key,
    derive_token,
    discover,
    key_path,
    load_pool_key,
    pool_group,
    require_name,
)
from .onboard.joining import pool_action
from .onboard.pools import known_pools, pick_pool


def _require_key(path: str | None) -> bytes:
    key = load_pool_key(path)
    if key is None:
        raise DiscoveryError(
            f"no pool key at {key_path(path)} -- run 'poolhouse-members init'")
    return key


def _prompt_passphrase(confirm: bool) -> str:
    """Read a passphrase, with confirmation when creating a pool."""
    while True:
        first = getpass.getpass("  Passphrase: ")
        if len(first.strip()) < MIN_JOIN_LENGTH:
            say(f"  The passphrase needs at least {MIN_JOIN_LENGTH} characters.")
            continue
        if not confirm:
            return first
        again = getpass.getpass("  Again:      ")
        if first != again:
            say("  Those did not match. Try again.")
            continue
        return first


def cmd_setup(args: argparse.Namespace) -> int:
    """The whole join story, for someone who has never used this before."""
    interactive = sys.stdin.isatty()
    p = key_path(args.pool_key)

    if p.exists() and not args.force:
        current = pool_group(args.pool_key)
        where = f" '{current}'" if current else ""
        say(f"This machine is already in pool{where} (key at {p}).")
        say("Run 'poolhouse-members ls' to see who else is in it,")
        say("or 'poolhouse-members setup --force' to join a different one.")
        return 0

    say("Create a new pool." if args.create else "Join a pool already running on this network.")
    say()

    group = args.group
    if interactive and not args.group_given and not os.environ.get("POOLHOUSE_NONINTERACTIVE"):
        if args.create:
            group = input("  Pool name: ").strip()
        else:
            say("  Looking for pools on this network...", flush=True)
            choice = pick_pool(known_pools(args.pool_key), default="")
            group = choice.name
    group = require_name(group)

    if args.passphrase:
        passphrase = args.passphrase
    elif interactive:
        passphrase = _prompt_passphrase(confirm=args.create)
    else:
        passphrase = sys.stdin.readline()
        if not passphrase.strip():
            warn("error: no passphrase on stdin")
            return 2

    say()
    say("  Creating the pool..." if args.create else "  Looking for the pool on this network...", flush=True)
    pool_action("create" if args.create else "join", passphrase, group, args.pool_key)
    recovery.remember(passphrase, group, args.pool_key, say=lambda s: say(f"  {s}"))

    say(f"  {'Created' if args.create else 'Joined'} '{group}'.")
    say()
    say("Next:")
    say("  1. Join your other machines with this name and passphrase, without --create.")
    say("  2. On each of them, start the daemon:")
    say()
    say("       poolhouse-traind")
    say()
    say("  3. Check they found each other:")
    say()
    say("       poolhouse-members ls")
    say()
    say("Anyone who knows this passphrase can run commands on every machine in the")
    say("group. Treat it like the password to your house, not like a wifi password.")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    p = key_path(args.pool_key)
    existed = p.exists()
    create_pool_key(args.pool_key, group=args.group)
    say(f"pool key {'already at' if existed else 'written to'} {p}")
    say("Export its name and key together, then copy the recovery file privately:")
    say("    poolhouse-pool recovery export pool-recovery.json")
    say("On the other machine (including Windows PowerShell):")
    say("    poolhouse-pool recovery import pool-recovery.json")
    say()
    say("Then start the daemon on the box with the card:")
    say()
    say("    poolhouse-traind")
    say()
    say("Anyone holding this key can run commands on every daemon in the")
    say("pool. Treat it exactly like an ssh private key.")
    return 0


def cmd_key(args: argparse.Namespace) -> int:
    say(_require_key(args.pool_key).decode())
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    say(derive_token(_require_key(args.pool_key)))
    return 0


def cmd_ls(args: argparse.Namespace) -> int:
    if load_pool_key(args.pool_key) is None and (pool := node_pool.status()) is not None:
        say(node_pool.render(pool, as_json=args.json))     # joined through the node's pool: no key file
        return 0
    members = discover(_require_key(args.pool_key), timeout_s=args.timeout)
    if args.json:
        say(json.dumps([{**p.public(), "host": p.host,
                           "base_url": p.base_url} for p in members], indent=2))
        return 0 if members else 1
    if not members:
        say("no members answered.")
        say("  - is 'poolhouse-traind' running there?")
        say("  - same LAN, and does that box hold the same pool key?")
        return 1
    say(f"{'NAME':<16} {'URL':<28} {'FREE':<7} {'STATE':<10} {'ROOM':<7} DEVICE")
    for p in members:
        # `measuring` before busy: a bench holds the GPU as surely as a job does, and a
        # sweep dispatched over the fleet must not land a second model on it
        state = ("measuring" if p.device.get("measuring")
                 else "busy" if p.free == 0 else "idle")
        if p.queued:
            state += f" +{p.queued}"
        slots = f"{p.free}/{p.slots}"
        gpu = (p.device.get("gpu")
               or ",".join(p.device.get("backends") or [])
               or f"{p.device.get('cpus', '?')} cpu")
        vram = p.device.get("vram_free_gb")
        if vram is not None:
            gpu += f"  {vram}/{p.device.get('vram_total_gb', '?')} GB free"
        # what a model may use there -- `hub.room()`, not free memory -- which is what
        # `fleet.sweeps.plan` fits each model against
        room = p.device.get("room_bytes")
        shown = f"{int(room) / 2**30:.0f}G" if room else "?"
        say(f"{p.name:<16} {p.base_url:<28} {slots:<7} {state:<10} {shown:<7} {gpu}")
    return 0


def _member(args: argparse.Namespace) -> Any:
    from .remote import Member
    key = _require_key(args.pool_key)
    if args.name:
        return Member.find_one(name=args.name, key=key)
    return Member(f"http://127.0.0.1:{args.port}", derive_token(key))


def cmd_pause(args: argparse.Namespace) -> int:
    """Take this machine back, now."""
    member = _member(args)
    out = member.availability("pause", minutes=args.minutes, reason=args.reason)
    say(out["unavailable_because"])
    if args.minutes:
        say(f"  it will start taking work again on its own in {args.minutes:.0f} min")
    else:
        say("  run 'poolhouse-members resume' when you are done with it")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    member = _member(args)
    out = member.availability("resume")
    say("taking work again" if out["available"]
        else f"still not taking work: {out['unavailable_because']}")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    """Open this machine's page in a browser, signed in with a one-shot ticket."""
    try:
        require_person("open the poolhouse page")
        url = launch_open.ticket_url()
    except (HumanRequired, launch_open.LaunchError) as exc:
        warn(f"error: {exc}")
        return 2
    if args.print:
        say(url)
    else:
        webbrowser.open(url)
        say(url.partition("?")[0])
    return 0


def cmd_when(args: argparse.Namespace) -> int:
    """What this machine's schedule looks like."""
    member = _member(args)
    out = member.availability()
    say("available now" if out["available"] else f"NOT available: {out['unavailable_because']}")
    for window in out.get("windows", []):
        say(f"  busy  {window}")
    if out.get("reserved"):
        r = out["reserved"]
        say(f"  held by {r['holder']} for another {r['seconds_left']:.0f}s")
    if not out.get("windows") and not out.get("reserved") and out["available"]:
        say("  no schedule set -- it will take work at any hour")
    return 0


def cmd_busy(args: argparse.Namespace) -> int:
    """Block out hours when this machine is somebody's desk."""
    member = _member(args)
    if args.clear:
        member.availability("clear_windows")
        say("cleared; it will take work at any hour")
        return 0
    out = member.availability("window", spec=args.when, busy=not args.free)
    say("schedule now:")
    for window in out.get("windows", []):
        say(f"  busy  {window}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="poolhouse-members")
    ap.add_argument("--pool-key", default=None,
                    help="path to the pool key (default: ~/.poolhouse/pool.key)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    setup = sub.add_parser(
        "setup", help="join a pool with a passphrase (start here)")
    setup.add_argument("--group", default="", help="pool name (required)")
    setup.add_argument("--create", action="store_true", help="create a new pool instead of joining an existing one")
    setup.add_argument("--passphrase", default="",
                       help="skip the prompt. Avoid on a shared machine: it lands in "
                            "your shell history and in 'ps'.")
    setup.add_argument("--force", action="store_true",
                       help="leave the pool this machine is in and join another")
    init = sub.add_parser("init", help="mint a random key instead of using a passphrase")
    init.add_argument("--group", required=True, help="pool name")
    opened = sub.add_parser("open", help="open this machine's page in your browser, signed in "
                                         "(a person at a terminal)")
    opened.add_argument("--print", action="store_true",
                        help="print the one-use page address instead of opening it")
    sub.add_parser("key", help="print the pool key")
    sub.add_parser("token", help="print the traind bearer token this key derives")
    ls = sub.add_parser("ls", help="list daemons on this LAN")
    ls.add_argument("--timeout", type=float, default=2.0)
    ls.add_argument("--json", action="store_true")

    for verb, helptext in (
        ("pause", "stop taking work now -- for when you want the machine back"),
        ("resume", "start taking work again"),
        ("when", "show this machine's schedule"),
    ):
        sp = sub.add_parser(verb, help=helptext)
        sp.add_argument("--name", default="",
                        help="another machine on the LAN (default: this one)")
        sp.add_argument("--port", type=int, default=8770)
        if verb == "pause":
            sp.add_argument("--minutes", type=float, default=0,
                            help="pause for this long, then resume on its own "
                                 "(default: until you say otherwise)")
            sp.add_argument("--reason", default="",
                            help="shown to anyone looking at the fleet")

    members_cli.add_commands(sub)
    busy = sub.add_parser("busy", help="block out hours, e.g. 'mon-fri 09:00-17:00'")
    busy.add_argument("when", nargs="?", default="")
    busy.add_argument("--free", action="store_true",
                      help="carve an exception out of a busy window instead")
    busy.add_argument("--clear", action="store_true", help="remove every window")
    busy.add_argument("--name", default="")
    busy.add_argument("--port", type=int, default=8770)
    args = ap.parse_args(argv)
    args.group_given = any(a == "--group" or a.startswith("--group=")
                           for a in (argv if argv is not None else sys.argv))
    fn = {"setup": cmd_setup, "init": cmd_init, "key": cmd_key,
          "token": cmd_token, "ls": cmd_ls, "pause": cmd_pause,
          "resume": cmd_resume, "when": cmd_when, "busy": cmd_busy, "open": cmd_open,
          "roster": cmd_ls}[args.cmd]
    if args.cmd == "roster":
        return members_cli.run(args)
    try:
        return fn(args)
    except (DiscoveryError, OSError) as exc:
        warn(f"error: {exc}")
        return 2
    except Exception as exc:                          # noqa: BLE001
        warn(f"error: {exc}")
        warn("  is 'poolhouse-traind' running on that machine?")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

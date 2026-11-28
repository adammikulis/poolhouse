"""``python -m poolhouse.guest``: share a model with a guest, join computers, list, remove, and connect to a host.

Letting another computer in (``share``, ``show``, ``scan``, ``confirm``, ``limit``, ``connect``) is held at least as tightly as adding
a device to the pool: refused to an agent, to a confined process and below an unattended agent's runner, and every run is on the
audit log. Nothing here asks a question: the person who compared the words runs ``confirm``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import threading
import time

from poolhouse import caller
from poolhouse.command import Group, flag
from poolhouse.guest import disclosure
from poolhouse.guest.control import Control, reach
from poolhouse.guest.engine import CACHE_MB, CONTEXT, PrivateServers
from poolhouse.guest.gateway import PORT, Gateway
from poolhouse.guest.joining import TTL_S
from poolhouse.guest.local import Proxy
from poolhouse.isolation import ancestry
from poolhouse.log import say

__all__ = ["GROUP", "permit"]

HELD = ("share", "show", "scan", "confirm", "limit", "connect")

GROUP = Group("python -m poolhouse.guest", "Guest access: someone outside the pool asks a model on this computer.",
              errors=(OSError, RuntimeError, ValueError))


def permit(action: str) -> None:
    """Refuse ``action`` at once, with the reason, to anything that is not an attended person; audit it either way."""
    ancestry.require_attended(action)
    caller.refuse_agent(action, "it lets another computer in, or lets this one send to it, which is held as tightly as adding a device")


def _control(args: argparse.Namespace) -> Control:
    if args.cmd in HELD:
        permit(f"guest {args.cmd}")
    return Control()


def _until_stopped() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        threading.Event().wait()


def _words(session: dict) -> int:
    if session["state"] != "confirm":
        say(session["error"] or session["state"])
        return 1
    if session["role"] == "guest":
        say("\n".join(disclosure.FOR_GUEST))
    say(f"\n{session['name']}\nBoth screens must show: {' '.join(session['words'])}")
    say(f"If they are the same: python -m poolhouse.guest confirm {session['id']} yes (otherwise: ... no)")
    return 0


@GROUP.command("share", help="answer your guests with a GGUF model until stopped", options=[
    flag("model"), flag("--context", type=int, default=CONTEXT), flag("--cache-mb", type=int, default=CACHE_MB),
    flag("--address", default="", help="the private or Tailscale address to answer on (default: the first found)"),
    flag("--port", type=int, default=PORT)])
def share(args: argparse.Namespace) -> int:
    control = _control(args)
    found = [args.address] if args.address else reach()
    if not found:
        raise ValueError("this computer has no private network address; join a network or turn Tailscale on")
    engine = PrivateServers(args.model, context=args.context, cache_mb=args.cache_mb)
    gateway = Gateway(engine, store=control.store, address=found[0], port=args.port)
    gateway.start()
    say("\n".join(disclosure.FOR_HOST))
    say(f"Answering guests on {found[0]} port {gateway.port}. Each active guest takes {engine.need_bytes() / 2**30:.1f} GiB. "
        "Stop with Ctrl-C.", flush=True)
    try:
        _until_stopped()
    finally:
        gateway.close()
    return 0


@GROUP.command("show", help="show a code for the other computer to read",
               options=[flag("role", choices=("host", "guest"), help="what this computer is"), flag("--name", default="my computer"),
                        flag("--minutes", type=int, default=TTL_S // 60)])
def show(args: argparse.Namespace) -> int:
    control = _control(args)
    session = control.show(args.role, args.name, args.minutes * 60)
    say(session["code"], flush=True)
    try:
        while session["state"] == "waiting":   # for the other computer, until the code runs out
            time.sleep(0.5)
            session = control.status(session["id"])
        return _words(session)
    finally:
        control.close()


@GROUP.command("scan", help="answer the code another computer shows", options=[flag("code"), flag("--name", default="my computer")])
def scan(args: argparse.Namespace) -> int:
    return _words(_control(args).scan(args.code, args.name))


@GROUP.command("confirm", help="say whether the words were the same", options=[flag("id"), flag("match", choices=("yes", "no"))])
def confirm(args: argparse.Namespace) -> int:
    done = _control(args).confirm(args.id, args.match == "yes")
    say(done["error"] or done["state"])
    return 0 if done["state"] == "done" else 1


@GROUP.command("list", help="guests (counts only) and hosts")
def listing(args: argparse.Namespace) -> int:
    say(json.dumps(_control(args).overview(), indent=2))
    return 0


@GROUP.command("remove", help="remove a guest or a host", options=[flag("id")])
def remove(args: argparse.Namespace) -> int:
    say(json.dumps(_control(args).revoke(args.id)))
    return 0


@GROUP.command("limit", help="change a guest's limits", options=[flag("id"), flag("changes", nargs="+", metavar="NAME=NUMBER")])
def limit(args: argparse.Namespace) -> int:
    changes = {k: int(v) for k, _, v in (one.partition("=") for one in args.changes)}
    say(json.dumps(_control(args).limit(args.id, changes)))
    return 0


@GROUP.command("connect", help="open a local address for a host, until stopped", options=[flag("id")])
def connect(args: argparse.Namespace) -> int:
    proxy = Proxy(args.id, store=_control(args).store)
    proxy.start()
    say("\n".join(disclosure.FOR_GUEST))
    say(f"\nAddress: {proxy.url}/v1\nAPI key: {proxy.token}\nStop with Ctrl-C.", flush=True)
    try:
        _until_stopped()
    finally:
        proxy.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(GROUP.run())

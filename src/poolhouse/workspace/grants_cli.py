"""The grant commands of `poolhouse-workspace`: what sessions may do beyond the default, who allowed it and how often it was
used (docs/node.md, "Standing grants").

`grants` and `grant-log` read; any session may run them. `grant`, `revoke-grant`, `grants-setup`, `person-proof` and
`person-enroll` are a person's: they refuse a process an agent started or one with no terminal, and they sign with the
person's key, which only that person's keystore opens.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from poolhouse import person
from poolhouse.board import grants, person as person_session, person_key
from poolhouse.board.client import Client, Denied
from poolhouse.board.session import Session
from poolhouse.command import flag
from poolhouse.log import say
from poolhouse.workspace import board_cli, render

__all__ = ["PERSON", "TABLE", "person_runner", "reader"]

TO = [flag("--to", default="", metavar="NAME", help="one session, by its unique name"),
      flag("--class", dest="family", default="", metavar="FAMILY", help="the main sessions of a model family (claude, qwen, ...)"),
      flag("--account", dest="model", default="", metavar="MODEL", help="the sessions whose verified model is exactly MODEL"),
      flag("--with-subagents", action="store_true", help="--class: the subagents of those sessions too"),
      flag("--verified", action="store_true", help="--class: only sessions whose model the node verified")]


@dataclass(slots=True)
class Ctx:
    """What a person's command works with: the node, a session that carries the request, and the key that signs it."""

    client: Client
    carrier: Session
    key: Any


def _when(ms: int) -> str:
    return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(ms / 1000))


def _who(to: dict[str, Any]) -> str:
    if to["kind"] == "session":
        return str(to["name"])
    if to["kind"] == "account":
        return f"{to['model']} (verified)"
    return f"{'' if not to.get('main', True) else 'main '}sessions of {to['family']}{' (verified)' if to.get('verified') else ''}"


def _line(g: grants.Grant) -> str:
    left = "unlimited" if g.uses is None else f"{g.used}/{g.uses}"
    return (f"{g.gid}  {g.status:<9} {g.action:<18} {_who(g.to)}  on {g.scope['resource']}  until {_when(g.until_ms)}  used {left}"
            f"{'  needs review' if g.review else ''}{'  (' + g.why + ')' if g.why else ''}")


def show_grants(args: argparse.Namespace, s: Session) -> Any:
    """Who holds which grant: its state, what it covers, when it ends and how often it was used."""
    got = grants.listing(s)
    rows = [g for g in got.grants if (not args.status or g.status == args.status) and (not args.mine or g.mine)]
    return [asdict(g) for g in rows] if args.json else [_line(g) for g in rows] or ["(no grants)"]


def show_log(args: argparse.Namespace, s: Session) -> Any:
    """The newest grant entries: issues, revocations and uses."""
    rows = grants.log(s, args.limit)
    if args.json:
        return rows
    return [f"{_when(r['at_ms'])}  {r['op']:<6} {r['gid']}  {r.get('action') or ''}  {r.get('target') or r.get('reason') or ''}  "
            f"{r.get('user') or r['by']}{'  (another device)' if r['foreign'] else ''}" for r in rows] or ["(nothing yet)"]


def reader(handler: Any) -> Any:
    """A command any session runs; a person at a terminal who holds no session reads as the screen's own."""
    def run(args: argparse.Namespace) -> int:
        try:
            s = board_cli.connect(args)
        except Denied:
            person.require_person("reading the grants without a session")
            s = person_session.session(Path.cwd(), Client(), args.board)
        render.show(args, handler(args, s))
        return 0
    return board_cli.guarded(run)


def person_runner(handler: Any) -> Any:
    """A command only a person at a terminal runs: it signs with the person's key and a session only carries the request."""
    def run(args: argparse.Namespace) -> int:
        person.require_person("granting")
        client = Client()
        ctx = Ctx(client, person_session.session(Path.cwd(), client, args.board), person_key.keys(client.state))
        try:
            result = handler(args, ctx)
        except RuntimeError as err:
            raise Denied("denied", str(err)) from err
        if result is not None:
            render.show(args, result)
        return 0
    return board_cli.guarded(run)


def _held(ctx: Ctx) -> Any:
    if not person_key.kept(ctx.client.state):
        raise Denied("denied", "no person key is kept on this device: run `poolhouse-workspace grants-setup`")
    if grants.fingerprint(ctx.key.public) not in grants.listing(ctx.carrier).persons:
        raise Denied("denied", "this node does not hold your key: run `poolhouse-workspace grants-setup`")
    return ctx.key


def _to(args: argparse.Namespace) -> dict[str, Any]:
    if [bool(args.to), bool(args.family), bool(args.model)].count(True) != 1:
        raise ValueError("name who it is for with exactly one of --to NAME, --class FAMILY, --account MODEL")
    if args.to:
        return {"kind": "session", "name": args.to}
    if args.family:
        return {"kind": "class", "family": args.family, "main": not args.with_subagents, **({"verified": True} if args.verified else {})}
    return {"kind": "account", "model": args.model}


def _device(asked: str, this: str) -> str:
    return this if asked in ("", "this") else "*" if asked == "any" else asked


def grant(args: argparse.Namespace, ctx: Ctx) -> Any:
    """Allow an action: to a session, a model family or a verified model, on a resource, until a time, a number of times."""
    key = _held(ctx)
    this = grants.listing(ctx.carrier).device
    want = grants.Want(args.action, _to(args), args.scope, _device(args.device, this), grants.duration_ms(args.until), args.uses or None, args.review)
    got = grants.issue(ctx.carrier, key, want)
    return {"granted": got["gid"], "action": want.action, "to": _who(want.to), "on": want.resource, "until": _when(got["until_ms"]),
            "uses": want.uses or "unlimited", "revoke": f"poolhouse-workspace revoke-grant {got['gid']}"}


def revoke_grant(args: argparse.Namespace, ctx: Ctx) -> Any:
    """End a grant at once."""
    done = grants.revoke(ctx.carrier, _held(ctx), args.gid, args.reason)
    return {"revoked": done["gid"]}


def _family(args: argparse.Namespace, ctx: Ctx) -> str:
    if args.family:
        return str(args.family)
    found = sorted({a.family for a in ctx.carrier.agents() if not a.parent and a.harness != person_session.HARNESS and a.family != "agent"})
    if len(found) != 1:
        raise ValueError(f"name the lead's model family with --family (main sessions here: {', '.join(found) or 'none'})")
    return found[0]


def _enrolled(ctx: Ctx) -> bool:
    """Put this device's key on the node when it holds none; whether it did."""
    if grants.fingerprint(ctx.key.public) in grants.listing(ctx.carrier).persons:
        return False
    if grants.listing(ctx.carrier).persons:
        raise Denied("denied", "the node already holds another key: carry this one over with `person-proof` here and `person-enroll` where the other key is")
    about = "".join(c for c in socket.gethostname() if c.isascii() and c.isprintable())[:40] or "this device"
    grants.enrol(ctx.client, ctx.carrier.token, grants.enrolment(ctx.key, about))
    return True


def setup(args: argparse.Namespace, ctx: Ctx) -> Any:
    """Approve once the grants the lead needs: the exact list is printed, every item is revoked by its own id."""
    enrolled = _enrolled(ctx)
    family, device = _family(args, ctx), grants.listing(ctx.carrier).device
    bundle = grants.default_bundle(family, device, args.days)
    say(f"These grants go to the main sessions of {family} on this device, each for {args.days} days, none for more:")
    for number, (want, text) in enumerate(bundle, 1):
        say(f"  {number}. {want.action}: {text}")
    if not args.yes and input("Type yes to grant them: ").strip().lower() != "yes":
        raise Denied("denied", "nothing was granted")
    done = [grants.issue(ctx.carrier, ctx.key, want) for want, _ in bundle]
    return {"key_enrolled": enrolled, "family": family, "granted": [{"gid": d["gid"], "action": d["action"]} for d in done],
            "revoke": "poolhouse-workspace revoke-grant ID", "see": "poolhouse-workspace grants"}


def proof(args: argparse.Namespace, ctx: Ctx) -> Any:
    """This device's key proving it holds its private half, to carry to another node: valid for five minutes."""
    if not person_key.kept(ctx.client.state):
        raise Denied("denied", "no person key is kept on this device: run `poolhouse-workspace grants-setup`")
    return grants.enrolment(ctx.key, args.about)


def enroll(args: argparse.Namespace, ctx: Ctx) -> Any:
    """Put another device's key on this node, approved by the key this node already holds."""
    carried = json.loads(args.proof)
    return grants.enrol(ctx.client, ctx.carrier.token, grants.approve(carried, _held(ctx)))


TABLE = (
    ("grants", "who holds which grant: its state, what it covers, when it ends and how often it was used",
     [flag("--status", default="", choices=("live", "expired", "exhausted", "revoked", "not-yet", "ignored")),
      flag("--mine", action="store_true", help="only the grants that name this session")], show_grants),
    ("grant-log", "grants issued, revoked and used, newest first", [flag("--limit", type=int, default=50)], show_log),
)

PERSON = (
    ("grant", "a person allows an action: to a session, a model family or a verified model, until a time, a number of times",
     [flag("action"), *TO, flag("--scope", default="*", metavar="RESOURCE", help="what it covers: a name, or a prefix ending in * (default: any)"),
      flag("--device", default="this", help="this (default), any, or a device fingerprint"),
      flag("--until", required=True, metavar="TIME", help="how long it lasts: 90m, 12h, 30d (at most a year)"),
      flag("--uses", type=int, default=0, help="how often it may be used (default: as often as it is needed)"),
      flag("--review", action="store_true", help="each use needs an independent review behind it")], grant),
    ("revoke-grant", "a person ends a grant at once", [flag("gid"), flag("--reason", default="")], revoke_grant),
    ("grants-setup", "a person approves, once, the grants the lead of this board needs",
     [flag("--family", default="", help="the lead's model family (default: the one main session family on this board)"),
      flag("--days", type=int, default=30, help="how long they last (default 30)"),
      flag("--yes", action="store_true", help="grant the list printed above without typing yes")], setup),
    ("person-proof", "print this device's key proving it holds its private half, to carry to another node",
     [flag("--about", default="a person's key", help="what to call the key there")], proof),
    ("person-enroll", "put a key carried from another device on this node, approved by this node's key",
     [flag("--proof", required=True, help="what `person-proof` printed")], enroll),
)

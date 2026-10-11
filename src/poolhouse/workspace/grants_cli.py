"""The grant commands of `poolhouse-workspace`: what sessions may do beyond the default, who allowed it and how often it was
used (docs/node.md, "Standing grants").

`grants` and `grant-log` read; any session may run them. `grant`, `revoke-grant`, `grants-setup`, `person-proof`,
`person-enroll` and `person-unenroll` are a person's: they refuse a process that carries an agent marker or has no terminal, and
they sign with the person's key, which asks for a typed `yes` on the terminal device for each thing it signs. The markers and the
question stop accidents and an agent that has no terminal of its own, not a process of the same user (docs/node.md, "Key custody").
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
from poolhouse.log import say, warn
from poolhouse.workspace import board_cli, render

__all__ = ["PERSON", "TABLE", "person_runner", "reader"]

OPEN = "--i-understand-registration-is-open"
TO = [flag("--to", default="", metavar="NAME", help="one session, by its unique name"),
      flag("--class", dest="family", default="", metavar="FAMILY", help="the main sessions that say they are of a model family (claude, qwen, ...); needs " + OPEN),
      flag("--account", dest="model", default="", metavar="MODEL", help="the sessions whose verified model is exactly MODEL (no session is verified yet)"),
      flag("--with-subagents", action="store_true", help="--to: its subagents too; --class: the subagents of those sessions too"),
      flag("--verified", action="store_true", help="--class: only sessions whose model the node verified (no session is verified yet)"),
      flag(OPEN, dest="open_registration", action="store_true", help="--class: any process can register under any model id, so a class grant is held by whatever claims the family")]


@dataclass(slots=True)
class Ctx:
    """What a person's command works with: the node, a session that carries the request, and the key that signs it."""

    client: Client
    carrier: Session
    key: Any


def _who(to: dict[str, Any]) -> str:
    if to["kind"] == "session":
        return f"{to['name']}{' and its subagents' if to.get('subagents') else ''}"
    if to["kind"] == "account":
        return f"{to['model']} (verified)"
    return f"{'' if not to.get('main', True) else 'main '}sessions of {to['family']}{' (verified)' if to.get('verified') else ''}"


def _matches_nobody_yet(to: dict[str, Any]) -> bool:
    return to["kind"] == "account" or bool(to.get("verified"))


def _line(g: grants.Grant) -> str:
    left = "unlimited" if g.uses is None else f"{g.used}/{g.uses}"
    return (f"{g.gid}  {g.status:<9} {g.action:<18} {_who(g.to)}  on {g.scope['resource']}  until {grants.when(g.until_ms)}  used {left}"
            f"{'  needs review' if g.review else ''}{'  (' + g.why + ')' if g.why else ''}"
            f"{'  (names a verified model, and no session is verified yet: it matches nobody)' if _matches_nobody_yet(g.to) else ''}")


def show_grants(args: argparse.Namespace, s: Session) -> Any:
    """Who holds which grant: its state, what it covers, when it ends and how often it was used."""
    got = grants.listing(s)
    rows = [g for g in got.grants if (not args.status or g.status == args.status) and (not args.mine or g.mine)]
    return [{**asdict(g), "matches_nobody_yet": _matches_nobody_yet(g.to)} for g in rows] if args.json else [_line(g) for g in rows] or ["(no grants)"]


def show_log(args: argparse.Namespace, s: Session) -> Any:
    """The newest grant entries: issues, revocations and uses."""
    rows = grants.log(s, args.limit)
    if args.json:
        return rows
    return [f"{grants.when(r['at_ms'])}  {r['op']:<6} {r['gid']}  {r.get('action') or ''}  {r.get('target') or r.get('reason') or ''}  "
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


def _class_allowed(args: argparse.Namespace) -> None:
    if not args.open_registration:
        raise ValueError("a class grant is held by whatever process registers as that family, and registration is open: any process can say "
                         f"it is a claude. Name the sessions with --to (or --session for grants-setup), or pass {OPEN} to grant the class anyway")
    warn("registration is open: this grant is held by every session that says it is of that family, until sessions are authenticated")


def _to(args: argparse.Namespace) -> dict[str, Any]:
    if [bool(args.to), bool(args.family), bool(args.model)].count(True) != 1:
        raise ValueError("name who it is for with exactly one of --to NAME, --class FAMILY, --account MODEL")
    if args.to:
        return {"kind": "session", "name": args.to, **({"subagents": True} if args.with_subagents else {})}
    if args.family:
        _class_allowed(args)
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
    return {"granted": got["gid"], "action": want.action, "to": _who(want.to), "on": want.resource, "until": grants.when(got["until_ms"]),
            "uses": want.uses or "unlimited", "revoke": f"poolhouse-workspace revoke-grant {got['gid']}"}


def revoke_grant(args: argparse.Namespace, ctx: Ctx) -> Any:
    """End a grant at once."""
    done = grants.revoke(ctx.carrier, _held(ctx), args.gid, args.reason)
    return {"revoked": done["gid"]}


def _leads(ctx: Ctx) -> dict[str, Any]:
    """The live main sessions of the board, by name: what each said it runs on is a claim."""
    return {a.name: a for a in sorted(ctx.carrier.agents(), key=lambda a: a.name) if not a.parent and a.harness != person_session.HARNESS}


def _chosen(args: argparse.Namespace, main: dict[str, Any]) -> list[str]:
    if args.session:
        return list(dict.fromkeys(args.session))
    if not main:
        raise ValueError("no session is registered on this board yet: start the lead, then run this again")
    say("The main sessions on this board (the model and harness are what each session says about itself):")
    for number, agent in enumerate(main.values(), 1):
        say(f"  {number}. {agent.name}  model {agent.model or '?'}  harness {agent.harness or '?'}  last seen {grants.when(agent.seen_ms)}")
    typed = input("The lead session or sessions to grant, by number or name: ").replace(",", " ").split()
    names = [list(main)[int(t) - 1] if t.isdigit() and 0 < int(t) <= len(main) else t for t in typed]
    if not names:
        raise ValueError("no session was chosen")
    return list(dict.fromkeys(names))


def _who_gets(args: argparse.Namespace, ctx: Ctx) -> list[dict[str, Any]]:
    if args.family:
        _class_allowed(args)
        return [{"kind": "class", "family": args.family, "main": True}]
    main = _leads(ctx)
    names = _chosen(args, main)
    if unknown := [n for n in names if n not in main]:
        raise ValueError(f"not a main session of this board: {', '.join(unknown)}")
    return [{"kind": "session", "name": n} for n in names]


def setup(args: argparse.Namespace, ctx: Ctx) -> Any:
    """Approve once the grants the lead needs: the exact list is printed and asked about at the terminal, every item is revoked by its own id."""
    who = _who_gets(args, ctx)
    held = grants.listing(ctx.carrier)
    if held.persons and grants.fingerprint(ctx.key.public) not in held.persons:
        raise Denied("denied", "the node already holds another key: carry this one over with `person-proof` here and `person-enroll` where the other key is")
    bundle = grants.default_bundle(who, held.device, args.days)
    about = "".join(c for c in socket.gethostname() if c.isascii() and c.isprintable())[:40] or "this device"
    enrolling = not held.persons
    at = int(time.time() * 1000)
    lines = [*([grants.enrol_line(ctx.key.public, about)] if enrolling else []), *(grants.want_line(ctx.carrier.board, want, at) for want, _ in bundle)]
    for action, text in dict.fromkeys((want.action, text) for want, text in bundle):
        say(f"  {action}: {text}")
    ctx.key.approve(lines, f"These {len(lines)} signatures give the lead sessions above the grants listed, each for {args.days} days, none for more:")
    if enrolling:
        grants.enrol_first(ctx.client, ctx.carrier.token, ctx.key, about)
    done = [grants.issue(ctx.carrier, ctx.key, want, now_ms=at) for want, _ in bundle]
    return {"key_enrolled": enrolling, "to": [_who(to) for to in who], "granted": [{"gid": d["gid"], "action": d["action"]} for d in done],
            "revoke": "poolhouse-workspace revoke-grant ID", "see": "poolhouse-workspace grants"}


def proof(args: argparse.Namespace, ctx: Ctx) -> Any:
    """This device's key proving it holds its private half, to carry to another node: valid for five minutes."""
    if not person_key.kept(ctx.client.state):
        raise Denied("denied", "no person key is kept on this device: run `poolhouse-workspace grants-setup`")
    return grants.enrolment(ctx.key, args.about)


def enroll(args: argparse.Namespace, ctx: Ctx) -> Any:
    """Put another device's key on this node, approved by the key this node already holds."""
    carried = json.loads(args.proof)
    return grants.enrol(ctx.client, ctx.carrier.token, grants.approve(carried, _held(ctx), node=grants.listing(ctx.carrier).device))


def unenroll(args: argparse.Namespace, ctx: Ctx) -> Any:
    """Take a key off this node, signed by a key this node holds; the node keeps its last key."""
    return grants.unenrol(ctx.client, ctx.carrier.token, _held(ctx), args.key)


def rebind(args: argparse.Namespace, ctx: Ctx) -> Any:
    """Move a main session to the terminal this runs in: the node ends the tokens it holds, and the harness registers it from here."""
    done = grants.rebind(ctx.client, ctx.carrier.board, args.harness, args.session, _held(ctx))
    return {"moved": done["name"], "tokens_ended": done["tokens_ended"], "next": "start the harness in this terminal: it registers as that session"}


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
    ("grants-setup", "a person approves, once, the grants the lead sessions of this board need",
     [flag("--session", action="append", metavar="NAME", help="a lead session, by its unique name (repeat for several; default: pick from the list the board shows)"),
      flag("--family", default="", help="every main session that says it is of this model family; needs " + OPEN),
      flag(OPEN, dest="open_registration", action="store_true", help="--family: any process can register under any model id, so the grants go to whatever claims the family"),
      flag("--days", type=int, default=30, help="how long they last (default 30)")], setup),
    ("person-proof", "print this device's key proving it holds its private half, to carry to another node",
     [flag("--about", default="a person's key", help="what to call the key there")], proof),
    ("person-enroll", "put a key carried from another device on this node, approved by this node's key",
     [flag("--proof", required=True, help="what `person-proof` printed")], enroll),
    ("rebind", "a person moves a main session to the terminal this runs in, ending the tokens it holds, so its harness can register from here",
     [flag("harness", help="the harness of the session, as the refusal named it (claude-code, codex, ...)"),
      flag("session", help="the native session id of the harness, as the refusal named it")], rebind),
    ("person-unenroll", "take a key off this node, signed by a key this node holds (the last key stays)",
     [flag("--key", required=True, metavar="FINGERPRINT", help="the fingerprint of the key to remove (`grants` lists the keys the node holds)")], unenroll),
)

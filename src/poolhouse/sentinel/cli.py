"""``poolhouse security``: status, events, quarantine, scan, baseline, verify. Releasing,
purging and changing the mode are decided at once by the quarantine and policy gates (docs/sentinel.md)."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any

from poolhouse import sentinel
from poolhouse.caller import AgentRefused, refuse_agent
from poolhouse.command import Group, flag, option
from poolhouse.log import say, warn
from poolhouse.sandbox import cli as sandbox_cli
from poolhouse.sentinel import inert, observers, permit, review
from poolhouse.sentinel.events import Severity
from poolhouse.sentinel.explain import show
from poolhouse.sentinel.integrity import FILE_KINDS
from poolhouse.sentinel.policy import Mode
from poolhouse.sentinel.store import KINDS, Holding, Record, State, TransitionRefused

COMMANDS = Group(
    "poolhouse security",
    "What sentinel has seen and holds. Everything is read-only except `quarantine add`, "
    "`scan`, `baseline` and `honey`; `release`, `purge` and `mode` are decided at once "
    "by the authority gates and refused with the reason to an agent while a gate is held back.")

JSON = option("json")


def _out(args: argparse.Namespace, data: Any, lines: Callable[[], list[str]]) -> int:
    say(json.dumps(data, indent=2, default=str) if args.json else "\n".join(lines()))
    return 0


def _row(r: Record) -> dict[str, Any]:
    return {"id": r.id, "kind": r.kind, "key": r.key, "state": r.state.value,
            "reason": r.reason, "updated": r.updated, "purged": r.purged,
            "held": bool(r.held and "file" in r.held), "moved": bool(r.action)}


def _loop(state: dict[str, Any]) -> str:
    if state["armed"]:
        return f"armed, pid {state['pid']}, every {state['interval_s']:g}s"
    return f"NOT ARMED ({state['why']})"


def _held() -> list[review.Item]:
    node = sentinel.default()
    return review.held_items(node.store, node.clock())


@COMMANDS.command("status", help="mode, counts by state, pins, decoys and whether the log verifies",
                  options=(JSON,))
def status(args: argparse.Namespace) -> int:
    """Print the sentinel's state, and what is held by name with the command that reviews it."""
    held = _held()
    info = {**sentinel.default().status(), "sandbox": sandbox_cli.summary(),
            "reputation": observers.line() or "no sources recorded",
            "held": [i.to_json() for i in held]}
    return _out(args, info, lambda: [
        *(f"{k}: {_loop(v) if k == 'scanner' else v}" for k, v in info.items() if k != "held"),
        *([review.hint(held)] if held else [])])


@COMMANDS.command("sandbox", help="status | test: the confinement used for untrusted execution",
                  options=(JSON, flag("what", choices=["status", "test"])))
def sandbox(args: argparse.Namespace) -> int:
    """Print the sandbox backend, or run the self-test (exit 1 when a check fails)."""
    if args.what == "status":
        info = sandbox_cli.status()
        return _out(args, info, lambda: [f"{k}: {v}" for k, v in info.items()])
    rows = sandbox_cli.test()
    _out(args, rows, lambda: [f"{'ok  ' if r['passed'] else 'FAIL'} {r['check']} ({r['detail']})"
                              for r in rows])
    return 0 if all(r["passed"] for r in rows) else 1


@COMMANDS.command("chip", help="the status mark: green, yellow, red or none, with a label",
                  options=(JSON,))
def chip(args: argparse.Namespace) -> int:
    """Print the status mark, and what to run when something is held."""
    mark = sentinel.default().chip()
    hint = review.hint(_held())
    mark = {**mark, "review": hint} if hint else mark
    return _out(args, mark, lambda: [f"{mark['verdict']}  {mark['label']}",
                                     *([hint] if hint else [])])


@COMMANDS.command("events", help="the most recent security events",
                  options=(JSON, flag("--limit", type=int, default=50),
                           flag("--kind", default="", help="only kinds starting with this"),
                           flag("--severity", default="info",
                                choices=[s.name.lower() for s in Severity])))
def events(args: argparse.Namespace) -> int:
    """Print recent events from the log."""
    floor = Severity.parse(args.severity)
    log = sentinel.default().bus.log
    rows = [e.to_record() for e in (log.read() if log else [])
            if e.severity >= floor and e.kind.startswith(args.kind)][-args.limit:]
    return _out(args, rows, lambda: [
        f"{r['ts']:.0f} {r['severity']:<8} {r['kind']:<28} {r['subject']}" for r in rows])


def _list(args: argparse.Namespace) -> int:
    rows = [_row(r) for r in sentinel.default().store.records()
            if not args.state or r.state.value == args.state]
    return _out(args, rows, lambda: [
        f"{r['id']}  {r['state']:<11} {r['kind']}:{show(r['key'], 80)}  {show(r['reason'], 60)}"
        for r in rows])


def _find(ident: str) -> Record:
    record = sentinel.default().store.get(ident)
    if record is None:
        raise SystemExit(f"poolhouse security: no record {ident}")
    return record


def _show(args: argparse.Namespace) -> int:
    record = _find(args.subject)
    data = record.to_json()
    if args.text or args.html:
        grant = permit.mint_gated("sentinel.quarantine", "inspect", record.id)
        text = sentinel.default().store.read(record.id, grant)
        say(inert.render_html(record.id, text) if args.html else inert.render_text(text))
        return 0
    return _out(args, data, lambda: [f"{k}: {v}" for k, v in data.items()])


def _release(args: argparse.Namespace) -> int:
    record = _find(args.subject)
    done = sentinel.default().store.release(record.id, permit.mint_gated("sentinel.quarantine", "release", record.id))
    say(f"{done.id} released ({done.kind}:{done.key})")
    return 0


def _purge(args: argparse.Namespace) -> int:
    record = _find(args.subject)
    done = sentinel.default().store.purge(record.id, permit.mint_gated("sentinel.quarantine", "purge", record.id))
    say(f"{done.id} purged: what was held is deleted, the record stays")
    return 0


def _add(args: argparse.Namespace) -> int:
    kind, _, key = args.subject.partition(":")
    if kind not in KINDS or not key:
        raise SystemExit(f"poolhouse security: give kind:key, kind one of {', '.join(KINDS)}")
    record = sentinel.default().store.quarantine(
        (kind, key), args.reason, {"by": "command line"}, Holding(path=args.path or None),
        actor="human")
    say(f"{record.id if record else 'not recorded'} quarantined {kind}:{key}")
    return 0


_ACTIONS = {"list": _list, "show": _show, "release": _release, "purge": _purge, "add": _add}


@COMMANDS.command("quarantine", help="list | show ID | release ID | purge ID | add KIND:KEY",
                  options=(JSON, flag("action", choices=sorted(_ACTIONS)),
                           flag("subject", nargs="?", default=""),
                           flag("--state", default="", choices=["", *[s.value for s in State]]),
                           flag("--text", action="store_true",
                                help="show: print the held text, escaped"),
                           flag("--html", action="store_true",
                                help="show: print the held text as an inert page"),
                           flag("--path", default="", help="add: the file to move aside"),
                           flag("--reason", default="held by a person")))
def quarantine(args: argparse.Namespace) -> int:
    """Run one quarantine action."""
    if args.action != "list" and not args.subject:
        raise SystemExit("poolhouse security quarantine: this needs an id or kind:key")
    try:
        return _ACTIONS[args.action](args)
    except AgentRefused as exc:
        warn(f"poolhouse security: {exc}")
        return 2
    except (TransitionRefused, OSError) as exc:
        warn(f"poolhouse security: {exc}")
        return 1


@COMMANDS.command("review", help="list everything held, and what each one blocks and how to deal with it", options=(JSON,))
def review_command(args: argparse.Namespace) -> int:
    """Print what is held."""
    items = _held()
    return _out(args, [i.to_json() for i in items], lambda: review.table(items))


@COMMANDS.command("release", help="release a quarantined subject (decided at once by the sentinel.quarantine gate)",
                  options=(flag("subject"),))
def release(args: argparse.Namespace) -> int:
    """Release one record."""
    args.action = "release"
    return quarantine(args)


@COMMANDS.command("scan", help="check every pin and decoy now", options=(
    JSON, flag("--deep", action="store_true", help="hash every file")))
def scan(args: argparse.Namespace) -> int:
    """Check pinned files and decoys; exit 1 when anything was found."""
    found = sentinel.default().scan(deep=args.deep)
    rows = [f.event.to_record() for f in found]
    _out(args, rows, lambda: [f"{r['kind']:<28} {r['subject']}" for r in rows] or ["clean"])
    return 1 if found else 0


@COMMANDS.command("baseline", help="pin files by digest and plant decoys", options=(
    JSON, flag("--pin", action="append", default=[], help="a file to pin"),
    flag("--kind", default="model", choices=FILE_KINDS), flag("--source", default=""),
    flag("--honey", action="store_true", help="plant the decoy files")))
def baseline(args: argparse.Namespace) -> int:
    """Pin the named files as they are now. An agent may not replace the pin of a downloaded model whose bytes changed:
    that digest is what the download was checked against."""
    node = sentinel.default()
    if args.source in node.manifest.DOWNLOADED | node.manifest.SELF_MADE:
        warn(f"poolhouse security: the source {args.source!r} is recorded by a download or by serving a file; name another")
        return 2
    for path in args.pin:
        held = node.manifest.pin_of(path)
        if held is not None and held.source in node.manifest.DOWNLOADED and node.changed_digest(held):
            refuse_agent("replacing the pin of a changed downloaded model", "download it again: its digest is what the download was checked against")
    pins = [node.manifest.pin(p, args.kind, args.source) for p in args.pin]
    if args.honey:
        node.honey.plant()
    return _out(args, [p.to_json() for p in pins],
                lambda: [f"pinned {p.path} {p.sha256[:12]}" for p in pins])


@COMMANDS.command("verify", help="check the event log's hash chain", options=(
    JSON, flag("--anchor", default="", help="a head line written down earlier")))
def verify(args: argparse.Namespace) -> int:
    """Check the chain; exit 1 when it does not hold."""
    log = sentinel.default().bus.log
    result = log.verify(args.anchor) if log else None
    if result is None:
        return 1
    data = {"ok": result.ok, "records": result.records, "head": result.head,
            "problems": list(result.problems)}
    _out(args, data, lambda: [("ok " if result.ok else "BROKEN ") + result.head,
                              *result.problems])
    return 0 if result.ok else 1


@COMMANDS.command("mode", help="show or set observe | guarded | enforce | off", options=(
    flag("to", nargs="?", default="", choices=["", *[m.value for m in Mode]]),))
def mode(args: argparse.Namespace) -> int:
    """Print the mode, or change it; decided at once by the sentinel.policy gate."""
    if not args.to:
        say(sentinel.default().mode.value)
        return 0
    try:
        grant = permit.mint_gated("sentinel.policy", "mode", "sentinel")
    except AgentRefused as exc:
        warn(f"poolhouse security: {exc}")
        return 2
    sentinel.default().set_mode(Mode(args.to), grant)
    say(f"mode is {args.to}")
    return 0


@COMMANDS.command("honey", help="plant | remove | status the decoy files", options=(
    JSON, flag("what", choices=["plant", "remove", "status"])))
def honey(args: argparse.Namespace) -> int:
    """Manage the decoys."""
    node = sentinel.default()
    if args.what == "plant":
        node.honey.plant()
    elif args.what == "remove":
        say(f"{node.honey.remove()} decoy files removed")
        return 0
    rows = [{"id": d.id, "path": d.path} for d in node.honey.decoys()]
    return _out(args, rows, lambda: [f"{r['id']}  {r['path']}" for r in rows])


command = COMMANDS.run

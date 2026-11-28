"""``poolhouse-delegate cards ACTION``: the session cards of the project's backlog, and the runner that works them (`poolhouse.cards_run`).

    list     check     show ID     next     split ID
    release --why TEXT [--tier N ...]     hold --why TEXT     retry ID     run [--once | --detach]     wait

`release`, `hold`, `retry`, `run` and `wait` are a lead's: a delegate, and whatever it starts, is refused. The lead that releases the runner is the
one every card's delegate answers to, and a release records each card as it stands: one added or changed since waits for the next release.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from pathlib import Path

from poolhouse import (
    authority,
    caller,
    cards,
    cards_ledger,
    cards_run,
    delegate_store as store,
    harnessid,
    isolation,
    jobs,
    node_launch,
)
from poolhouse.board import place
from poolhouse.board.client import Client
from poolhouse.command import flag, option
from poolhouse.log import say
from poolhouse.workspace import nudge_fence

__all__ = ["ACTIONS", "OPTIONS", "command", "project_of"]

ACTIONS = ("list", "check", "show", "next", "split", "release", "hold", "retry", "run", "wait")
OPTIONS = (
    flag("action", choices=ACTIONS), flag("card", nargs="?", default="", metavar="ID"),
    flag("--project", default="", metavar="DIR", help="the project whose cards these are (default: the one this command runs in)"),
    flag("--why", default="", help="release and hold: the reason, kept with the record"),
    flag("--tier", action="append", default=[], metavar="N", help="release: a paused tier of the backlog the runner may work, by its number"),
    flag("--as", dest="parent", default="", metavar="AGENT", help="release: the session that is the cards' lead (default: this command's)"),
    flag("--once", action="store_true", help="run: end when one card has an outcome, or none can start"),
    flag("--detach", action="store_true", help="run: go on in a process of its own, with its output in a log"),
    flag("--idle-minutes", type=float, default=cards_run.IDLE_S / 60, help="run: how long the model must be unused first (default: %(default)g)"),
    option("json", help="list: print the listing as JSON"))
JOB = "runner"


def project_of(given: str) -> Path:
    """The project at ``given``, or the one holding this folder: the nearest folder up with a backlog; ValueError for none."""
    here = Path(given or ".").resolve()
    found = next((each for each in (here, *here.parents) if (each / "HANDOFF.md").is_file()), None)
    if found is None:
        raise ValueError(f"{here} is in no project with a HANDOFF.md backlog")
    return found


def _leads(action: str) -> None:
    isolation.require_attended(action)
    authority.require("workspace.agents", action)


def _line(row: dict) -> str:
    run = f"  {row['delegate']}" if row.get("delegate") else ""
    peak = f"  peak {row['peak']:,}" if row.get("peak") else ""
    why = f"  {row['why']}" if row.get("why") else ""
    return f"{row['state']:<9} {row['id']}  {row['budget']:,} tokens  {row['title']}{run}{peak}{why}"


def _list(root: Path, args: argparse.Namespace) -> int:
    shown = cards_run.listing(root)
    if args.json:
        say(json.dumps(shown, indent=1))
        return 0
    release = shown["release"]
    tiers = f", paused tier(s) {', '.join(release['tiers'])} included" if release.get("tiers") else ""
    say(f"held: {shown['held']}" if shown["held"] else f"released for lead {release.get('lead')}{tiers}")
    say(nudge_fence.fenced([nudge_fence.clean(_line(row)) for row in shown["cards"]]) if shown["cards"] else "no card")
    for line in cards_run.needs(root):
        say(f"waits for the lead: {line}")
    return 0


def _check(root: Path, _args: argparse.Namespace) -> int:
    wrong = cards.problems(root)
    for ident, why in sorted(wrong.items()):
        for each in why:
            say(f"{ident}: {each}")
    for card in cards.ordered(root):
        if card.id not in wrong:
            say(f"{card.id}: good, {cards.estimate(root, card).said()}")
    say(f"{len(wrong)} with problems" if wrong else "every card is good")
    return 1 if wrong else 0


def _known(root: Path, ident: str) -> cards_run.Standing:
    found = next((each for each in cards_run.standing(root) if each.card.id == ident), None)
    if found is None:
        raise ValueError(f"no card {ident!r}; `poolhouse-delegate cards list` lists them")
    return found


def _show(root: Path, args: argparse.Namespace) -> int:
    each = _known(root, args.card)
    say(f"{each.card.id}: {each.state}")
    if each.why:
        say(nudge_fence.fenced([nudge_fence.clean(each.why)]))
    say(f"row: {each.row.tier} / {each.row.item}" if each.row else f"row: none begins {each.card.row!r}")
    say(f"budget: {cards.estimate(root, each.card).said()} (most {cards.CAP:,}; compaction at {cards.COMPACTS_AT:,})")
    lead = str(cards_ledger.read(root)["release"].get("lead") or "the lead that releases the runner")
    say(cards.brief(root, each.card, lead, [str(one) for one in each.entry.get("reasons") or []]))
    return 0


def _next(root: Path, _args: argparse.Namespace) -> int:
    ready = next((each for each in cards_run.standing(root) if each.state == "ready"), None)
    why = cards_run.held(root, cards_ledger.read(root))
    say(f"{ready.card.id}: {ready.card.title}" if ready else "no card is ready")
    if why:
        say(f"held: {why}")
    return 0 if ready and not why else 1


def _split(root: Path, args: argparse.Namespace) -> int:
    made = cards.split(root, _known(root, args.card).card.id)
    say(f"split {args.card} into {', '.join(made)}: narrow each one's task, read and write, run `cards check`, and publish the change")
    return 0


def _reason(args: argparse.Namespace) -> str:
    if not args.why.strip():
        raise ValueError(f"{args.action} says why: --why TEXT")
    return " ".join(args.why.split())


def _release(root: Path, args: argparse.Namespace) -> int:
    _leads("releasing the card runner")
    why = _reason(args)
    numbers = {cards_run.tier_number(row) for row in cards.backlog(root) if row.paused} - {""}
    if unknown := [each for each in args.tier if each not in numbers]:
        raise ValueError(f"no paused tier {', '.join(unknown)}; the paused ones are {', '.join(sorted(numbers)) or 'none'}")
    client = Client(node_launch.default_state())
    board = place.at_path(client, root)
    lead = harnessid.launcher(client, board, args.parent).name
    if store.load(lead) is not None:
        raise ValueError(f"{lead} is a delegate, which leads no card")
    release = {"lead": lead, "board": board, "why": why, "tiers": sorted(set(args.tier)), "at": time.time(),
               "hold": cards_run.digest(cards_run.stated(root)),
               "cards": {card.id: cards_run.card_digest(root, card) for card in cards.load(root)[0]}}
    cards_ledger.edit(root, lambda ledger: ledger.update(release=release, hold=""))
    caller.record("cards.release", lead=lead, project=str(root), why=why, tiers=release["tiers"])
    say(f"released: ready cards run as delegates of {lead}" + (f", paused tier(s) {', '.join(release['tiers'])} included" if args.tier else ""))
    say("`poolhouse-delegate cards run` starts the runner; run `poolhouse-delegate cards wait` in the background to be woken when a card needs you")
    for line in cards_run.needs(root):
        say(f"waits for you: {line}")
    cards_run.listing(root)
    return 0


def _hold(root: Path, args: argparse.Namespace) -> int:
    _leads("holding the card runner")
    why = _reason(args)
    cards_ledger.edit(root, lambda ledger: ledger.update(hold=why))
    caller.record("cards.hold", project=str(root), why=why)
    say("held: no further card starts; a delegate that is running goes on (`poolhouse-delegate stop NAME` ends it)")
    cards_run.listing(root)
    return 0


def _retry(root: Path, args: argparse.Namespace) -> int:
    _leads("returning a card to the queue")
    each = _known(root, args.card)
    if each.state == "running":
        raise ValueError(f"{args.card} is running; `poolhouse-delegate stop {each.entry.get('delegate')}` ends it")
    cards_ledger.edit(root, lambda ledger: ledger["cards"].setdefault(args.card, {}).update(state="", why=""))
    cards_run.listing(root)
    say(f"{args.card} is in the queue again")
    return 0


def _wait(root: Path, host: cards_run.Host) -> int:
    _leads("waiting on the session cards")
    while True:
        cards_run.watch(root, host)   # settles an ended run and yields the model, whether or not a runner is polling
        if found := cards_run.needs(root):
            break
        host.sleep(cards_run.POLL_S)
    cards_run.listing(root)
    for line in found:
        say(line)
    return 0


def command(args: argparse.Namespace, start: Callable[[list[str]], int]) -> int:
    """Run the action ``args`` names; ``start`` is `poolhouse-delegate`'s own command line, which the runner starts and stops delegates by."""
    root = project_of(args.project)
    if args.action == "wait":
        return _wait(root, cards_run.Host(start))
    if args.action != "run":
        actions = {"list": _list, "check": _check, "show": _show, "next": _next, "split": _split, "release": _release, "hold": _hold,
                   "retry": _retry}
        return actions[args.action](root, args)
    _leads("running the session cards")
    if args.detach:
        words = ["cards", "run", "--project", str(root), "--idle-minutes", str(args.idle_minutes), *(["--once"] if args.once else [])]
        log = cards_ledger.folder() / "runner.log"
        job = jobs.detach("poolhouse.delegate", words, log=log, kind=JOB, home=cards_ledger.folder(), same_interpreter=True)
        say(f"the card runner runs as pid {job.pid}; its output is in {log}")
        return 0
    return cards_run.run(root, cards_run.Host(start, idle_s=args.idle_minutes * 60), say, once=args.once)

"""The card runner: when the served model is free it takes the next ready card, runs it as a delegate on a branch of its own, bound to the lead that
released the runner, and records what came of it. One at a time on a device.

It is held until a lead releases it, and takes a paused tier only when the release names it. It never loads a model, never starts while anything else
holds or uses one and stops its delegate when someone else comes for the model. It grades, accepts, pushes and merges nothing: finished work waits
for the lead, whose grade accepts or rejects it (`cards_ledger.graded`).
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import (
    cards,
    cards_ledger,
    delegate_record,
    delegate_store as store,
    jobs,
    lock,
)
from poolhouse.client import counters
from poolhouse.errors import Denied
from poolhouse.ownership import recorded_servers
from poolhouse.serve import admission, broker_wire
from poolhouse.serve.broker import BrokerError
from poolhouse.serve.manager import measurement_on_the_card
from poolhouse.serve.matching import model_matches
from poolhouse.serve.reclaim import busy_now

__all__ = ["IDLE_S", "Host", "Standing", "digest", "held", "listing", "needs", "outcome", "queued", "run", "standing", "stated", "step",
           "tier_number"]

IDLE_S = 600.0
"""The seconds a served model is seen unused before a card is started on it."""
POLL_S = 10.0
OVERSIZE = cards.COMPACTS_AT * 9 // 10
"""A run whose context came this near the compaction point did not fit its session."""
UP = "poolhouse-serve up "
"""How the holder `poolhouse-serve up` is labelled: it keeps a server running and asks it nothing."""
DEFERRED = "llamacpp:requests_deferred"
UNAVAILABLE = (BrokerError, OSError, RuntimeError, ValueError)
CARD_MODEL = "Qwen3.8-Flash-Next"
"""The model cards are written for: any quant of it a server holds."""


def queued(base_url: str) -> int:
    """The requests waiting for a slot on the server at ``base_url``; 0 where it does not say."""
    for line in counters.metrics_text(base_url).splitlines():
        name, _, value = line.partition(" ")
        if name == DEFERRED:
            return int(float(value)) if value.replace(".", "", 1).isdigit() else 0
    return 0


@dataclass(slots=True)
class Host:
    """What the runner asks of this computer, and how it starts and stops a delegate (`poolhouse-delegate`'s own command line)."""

    start: Callable[[list[str]], int]
    status: Callable[[], dict[str, Any]] = broker_wire.status
    servers: Callable[[], dict[int, dict[str, Any]]] = recorded_servers
    live: Callable[[dict[str, Any]], bool] = admission.live
    measuring: Callable[[], Any] = measurement_on_the_card
    busy: Callable[[str], bool | None] = busy_now
    queued: Callable[[str], int] = queued
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    model: str = CARD_MODEL
    idle_s: float = IDLE_S
    quiet_since: float | None = None


@dataclass(frozen=True, slots=True)
class Standing:
    """Where one card stands: its state, why, and its entry in the ledger."""

    card: cards.Card
    state: str
    why: str
    entry: dict[str, Any]
    row: cards.Row | None


def digest(text: str) -> str:
    """The short digest by which a card's file and a stated reason to hold are told apart."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def stated(root: Path) -> str:
    """The reason the project itself states for holding the runner; empty for none."""
    path = root / cards.HOLD
    return " ".join(path.read_text(encoding="utf-8").split()) if path.is_file() else ""


def held(root: Path, ledger: dict[str, Any]) -> str:
    """Why no card is started in the project at ``root``; empty once a lead has released it. A new reason stated by the project holds it again."""
    if ledger["hold"]:
        return ledger["hold"]
    release = ledger["release"]
    if release and release.get("hold") == digest(stated(root)):
        return ""
    return stated(root) or "no lead has released the runner: `poolhouse-delegate cards release --why TEXT`"


def tier_number(row: cards.Row) -> str:
    """The number a tier's heading starts with, which a release names it by; empty for a heading with none."""
    head = row.tier.split(".", 1)[0]
    return head if head.isdigit() else ""


def card_digest(root: Path, card: cards.Card) -> str:
    return digest((root / cards.FOLDER / f"{card.id}.toml").read_text(encoding="utf-8"))


def _state(root: Path, card: cards.Card, entry: dict[str, Any], found: tuple[cards.Row | None, list[str], set[str], dict[str, Any]],
           ) -> tuple[str, str]:
    row, wrong, ids, release = found
    state = str(entry.get("state") or "")
    if state in ("running", "review", "accepted"):
        return state, str(entry.get("why") or "")
    if wrong or row is None:
        return "invalid", "; ".join(wrong)
    if state == "returned" and entry.get("card") == card_digest(root, card):
        return state, str(entry.get("why") or "")
    if row.paused and tier_number(row) not in release.get("tiers", ()):
        return "paused", f"its tier is paused: {row.tier}"
    if waiting := [each for each in card.after if each in ids]:
        return "waiting", f"after {', '.join(waiting)}"
    return "ready", ""


def standing(root: Path) -> list[Standing]:
    """Every card of the project at ``root`` in backlog order, with where it stands."""
    wrong, ledger, rows = cards.problems(root), cards_ledger.read(root), cards.backlog(root)
    found = cards.ordered(root)
    ids = {card.id for card in found}
    out = []
    for card in found:
        entry = ledger["cards"].get(card.id) or {}
        row = cards.row_of(card, rows)
        state, why = _state(root, card, entry, (row, wrong.get(card.id, []), ids, ledger["release"]))
        out.append(Standing(card, state, why, entry, row))
    return out


def listing(root: Path) -> dict[str, Any]:
    """The cards of ``root`` as the commands and the web page show them, kept for the page as it is made."""
    ledger, rows = cards_ledger.read(root), []
    for each in standing(root):
        try:
            tokens = cards.estimate(root, each.card).total
        except (OSError, ValueError):
            tokens = 0
        rows.append({"id": each.card.id, "title": each.card.title, "state": each.state, "why": each.why,
                     "tier": each.row.tier if each.row else "", "row": each.card.row, "budget": tokens,
                     **{key: each.entry.get(key) for key in ("delegate", "branch", "commit", "peak", "tokens", "memory", "attempts")}})
    broken = [{"id": ident, "title": "", "state": "invalid", "why": "; ".join(why)} for ident, why in cards.problems(root).items()
              if ident not in {row["id"] for row in rows}]
    shown = {"project": str(root), "at": time.time(), "held": held(root, ledger), "release": ledger["release"], "cards": [*rows, *broken]}
    cards_ledger.publish(shown)
    return shown


def needs(root: Path) -> list[str]:
    """What waits for the lead: a card's delegate asking, work to review and cards sent back; one line each."""
    found = []
    for each in standing(root):
        name = str(each.entry.get("delegate") or "")
        if each.state == "running" and name and delegate_record.state_of(name)[0] == "asking":
            found.append(f"{each.card.id}: {name} asks you something; read your inbox and answer it")
        elif each.state == "review":
            found.append(f"{each.card.id}: review branch {each.entry.get('branch')} at {each.entry.get('commit')}, then `poolhouse-delegate "
                         f"grade {name} correct|partly|wrong --critique TEXT --check \"RUN => SAW\"`; an accepted branch is yours to publish as "
                         "a pull request and merge")
        elif each.state == "returned":
            found.append(f"{each.card.id}: sent back: {each.why}. Split it, correct it, or `poolhouse-delegate cards retry {each.card.id}`")
    return found


def _events(name: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    after, more = 0, True
    while more:
        page, after, more = delegate_record.read(name, after)
        found += page
    return found


def _peak(events: list[dict[str, Any]]) -> int:
    """The most tokens one step held: its prompt, read and reused, and what it wrote."""
    return max((sum(int(row.get(key) or 0) for key in ("input_tokens", "cache_read_tokens", "output_tokens"))
                for row in events if row.get("kind") == "usage"), default=0)


def _unchecked(events: list[dict[str, Any]], check: str) -> str:
    """Why the record does not show ``check`` passing: it is run exactly, exits 0, and no file is edited afterwards. Empty when it does."""
    passed, seen = False, False
    for row in events:
        if row.get("kind") != "result":
            continue
        if row.get("tool") == "shell" and str(row.get("command") or "").strip() in (check, f"{check} 2>&1"):
            seen, passed = True, row.get("exit") == 0
        elif row.get("tool") in ("edit", "write") and row.get("status") == "completed":
            passed = False
    if passed:
        return ""
    return "its last run of the check did not pass, or files changed after it" if seen else "it never ran the check as written"


def outcome(name: str, check: str) -> dict[str, Any]:
    """What the ended run of delegate ``name`` came to, from its own record: the ledger fields of a card in `review`, or `returned` with why."""
    state, detail = delegate_record.state_of(name)
    result, events = store.result(name), _events(name)
    tree = result.get("tree") or {}
    peak = _peak(events)
    if peak >= OVERSIZE:
        why = f"oversize: its context reached {peak:,} tokens, so split the card"
    elif state != "done":
        why = f"the run ended {state}: {delegate_record.cleaned(result.get('error') or detail, 300)}"
    elif unchecked := _unchecked(events, check):
        why = unchecked
    else:
        why = "" if tree.get("commit") else "it changed nothing"
    return {"state": "returned" if why else "review", "why": why, "branch": tree.get("branch") or "", "commit": tree.get("commit") or "",
            "peak": peak, "tokens": {key: result.get(key, 0) for key in ("input_tokens", "output_tokens", "cache_read_tokens")}}


def _serving(host: Host) -> tuple[str, str]:
    """``(model, base URL)`` of a live server of the card model whose slot holds a card's session; empty when there is none."""
    for port, entry in host.servers().items():
        window = int(entry.get("context") or 0) // max(1, int(entry.get("parallel") or 1))
        if not entry.get("embedding") and window >= cards.CAP * 2 and model_matches(str(entry.get("model") or ""), host.model) and host.live(entry):
            return str(entry["model"]), f"http://127.0.0.1:{port}"
    return "", ""


def _others(status: dict[str, Any], mine: int) -> str:
    """Who besides ``mine`` (a process id) holds a server or claims the device; empty for nobody."""
    for name, claim in (status.get("claims") or {}).items():
        purpose = str((claim.get("info") or {}).get("purpose") or name)
        if purpose != "resident decision":
            return f"the device is claimed: {purpose}"
    for server in status.get("servers") or []:
        if server.get("loading"):
            return "a model is loading"
        for holder in server.get("holders") or []:
            if holder.get("pid") != mine and not str(holder.get("label") or "").startswith(UP):
                return f"port {server.get('port')} is in use by {holder.get('label') or holder.get('pid')}"
    return ""


def _not_free(host: Host) -> tuple[str, str]:
    """``(why the model is not free, the model to run on)``: one of the two is empty."""
    if live := [name for name in store.names() if delegate_record.state_of(name)[0] in delegate_record.LIVE]:
        return f"{live[0]} is running; one delegate at a time", ""
    try:
        if host.measuring() is not None:
            return "a measurement holds the device", ""
        if why := _others(host.status(), 0):
            return why, ""
        model, url = _serving(host)
    except UNAVAILABLE as why:
        return f"the model broker could not be asked: {why}", ""
    if not model:
        return f"{host.model} is not being served with room for a card's session; the runner never loads a model", ""
    if host.busy(url) is not False or host.queued(url):
        return "the model is answering a request, or does not say", ""
    return "", model


def _intruder(host: Host, name: str) -> str:
    """Why delegate ``name`` must give the model up: someone else holds it or waits for it; empty while nobody does."""
    mine = int(jobs.recorded(delegate_record.JOB, home=delegate_record.folder(name)).get("pid") or 0)
    try:
        if why := _others(host.status(), mine):
            return why
        _model, url = _serving(host)
    except UNAVAILABLE:
        return ""
    return "a request waits for the model's slot" if url and host.queued(url) else ""


def _record(root: Path, ident: str, **fields: Any) -> None:
    def change(ledger: dict[str, Any]) -> None:
        ledger["cards"][ident] = {**ledger["cards"].get(ident, {}), **fields}

    cards_ledger.edit(root, change)


def _watched(root: Path, host: Host, each: Standing) -> tuple[str, str]:
    """One look at the running card ``each``: give the model up to someone else, or record what its ended run came to."""
    name = str(each.entry.get("delegate"))
    if delegate_record.state_of(name)[0] in delegate_record.LIVE:
        if why := _intruder(host, name):
            host.start(["stop", name])
            _record(root, each.card.id, state="", why=f"it gave the model up: {why}", delegate="")
            host.quiet_since = None
            return "waiting", f"{each.card.id}: stopped {name}, since {why}; the card is ready again"
        return "running", f"{each.card.id}: running as {name}"
    came = outcome(name, each.card.check)
    _record(root, each.card.id, **came, card=card_digest(root, each.card))
    return "settled", f"{each.card.id}: {came['state']}" + (f", {came['why']}" if came["why"] else f", branch {came['branch']}")


def _launch(root: Path, host: Host, each: Standing, model: str) -> tuple[str, str]:
    """Start the delegate of card ``each`` on ``model``, bound to the released lead."""
    lead = str(cards_ledger.read(root)["release"].get("lead"))
    card = each.card
    reasons = [str(one) for one in each.entry.get("reasons") or []]
    task = cards.brief(root, card, lead, reasons)
    before = set(store.names())
    words = ["start", "--task", task, "--edit", "--project", str(root), "--as", lead, "--name", card.id, "--kind", "card", "--card", card.id,
             "--model", model, *(["--memory", "--about", f"{card.title} {' '.join(card.write)}"] if card.memory else [])]
    status = host.start(words)
    new = [name for name in store.names() if name not in before and (rec := store.load(name)) and rec.card == card.id]
    if status or not new:
        why = f"{card.id}: no delegate started for lead {lead}; a live lead releases the runner again once what it said is settled"
        cards_ledger.edit(root, lambda ledger: ledger.update(hold=why))   # not tried again every few seconds
        return "held", f"held: {why}"
    _record(root, card.id, state="running", delegate=new[-1], why="", memory=card.memory, attempts=int(each.entry.get("attempts") or 0) + 1)
    host.quiet_since = None
    return "launched", f"{card.id}: started {new[-1]} for {lead}"


def step(root: Path, host: Host) -> tuple[str, str]:
    """Do the next thing for the project at ``root`` and say what: ``running``, ``settled``, ``launched``, ``waiting`` for the model, ``held`` or
    ``idle`` (no card is ready)."""
    found = standing(root)
    for each in found:
        if each.state == "running":
            return _watched(root, host, each)
    if why := held(root, cards_ledger.read(root)):
        return "held", f"held: {why}"
    ready = [each for each in found if each.state == "ready"]
    if not ready:
        return "idle", "no card is ready"
    why, model = _not_free(host)
    if why:
        host.quiet_since = None
        return "waiting", f"waiting: {why}"
    host.quiet_since = host.clock() if host.quiet_since is None else host.quiet_since
    if host.clock() - host.quiet_since < host.idle_s:
        return "waiting", f"waiting: the model has to be unused for {delegate_record.span(host.idle_s)} first"
    return _launch(root, host, ready[0], model)


def run(root: Path, host: Host, say: Callable[[str], None], *, once: bool = False) -> int:
    """Work the cards of ``root`` until stopped, or with ``once`` until one card has an outcome or none can start. One runner a device: a second
    one ends at once."""
    said = ""
    try:
        with lock.only_one(cards_ledger.folder() / "run.lock", wait=False, announce=say):
            while True:
                try:
                    kind, text = step(root, host)
                except (Denied, OSError, RuntimeError, ValueError) as why:
                    kind, text = "waiting", f"waiting: {type(why).__name__}: {why}"
                if text != said:
                    say(text)
                    said = text
                    listing(root)
                if once and kind in ("settled", "held", "idle"):
                    return 0
                host.sleep(POLL_S)
    except lock.Busy as why:
        say(f"a card runner already runs on this device ({why})")
        return 1

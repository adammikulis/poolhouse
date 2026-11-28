"""The card runner: when the served model is free it takes the next ready card, runs it as a delegate on a branch of its own, bound to the lead that
released the runner, and records what came of it. One at a time on a device.

It is held until a lead releases it, runs only the cards that release recorded, and takes a paused tier only when the release names it. It runs a
card only in a sandbox and only for a lead that is live, which it never registers again. It never loads a model, never starts while anything else
holds, uses or waits for one and stops its delegate when someone else comes for the model. An ended run waits for review only when its commit stays
within the card and the card's check passes on it, run again by this process (`cards_check`). It grades, accepts, pushes and merges nothing:
finished work waits for the lead, whose grade accepts or rejects it (`cards_ledger.graded`).
"""

from __future__ import annotations

import contextlib
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from poolhouse import (
    cards,
    cards_check,
    cards_ledger,
    delegate_confined,
    delegate_record,
    delegate_store as store,
    harnessing,
    jobs,
    lock,
    node_launch,
)
from poolhouse.board import credentials, session as sessions
from poolhouse.board.client import Client, NodeError
from poolhouse.client import counters
from poolhouse.errors import Denied
from poolhouse.ownership import recorded_servers
from poolhouse.serve import admission, broker_wire
from poolhouse.serve.broker import BrokerError
from poolhouse.serve.manager import measurement_on_the_card
from poolhouse.serve.matching import model_matches
from poolhouse.serve.reclaim import busy_now
from poolhouse.workspace import nudge_fence

__all__ = ["IDLE_S", "Host", "Standing", "card_digest", "digest", "family", "held", "lead_gone", "listing", "needs", "outcome", "queued", "run",
           "standing", "stated", "step", "tier_number", "watch"]

IDLE_S = 600.0
"""The seconds a served model is seen unused before a card is started on it."""
POLL_S = 10.0
OVERSIZE = cards.COMPACTS_AT * 9 // 10
"""A run whose context came this near the compaction point did not fit its session."""
UNUSING = ("poolhouse-serve up ", "sentinel-canary")
"""How a holder that is nobody's use of the model is labelled: a `poolhouse-serve up` that waits on its server asks it nothing, and the broker's
canary check asks one question of a server that is idle. A server left up holds itself (its holder's process is the server's), which is no use
either."""
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


def family(pid: int) -> set[int]:
    """``pid`` and every process below it: a delegate's runner, and on Windows the interpreter a virtual environment's launcher starts under it,
    which is the process that holds the model's lease. Empty for no process."""
    if not pid:
        return set()
    found = {pid}
    with contextlib.suppress(psutil.Error, OSError):
        found |= {each.pid for each in psutil.Process(pid).children(recursive=True)}
    return found


def lead_gone(release: dict[str, Any]) -> str:
    """Why the lead of ``release`` is not live now, empty while it is: asked with the token kept for it, which is never renewed and whose session
    is never registered again. A node that cannot be asked raises, as it says nothing of the lead."""
    lead, board = str(release.get("lead") or ""), str(release.get("board") or "")
    try:
        client = Client(node_launch.default_state())
        found = sessions.Session(client, board, credentials.load(client.state, board, lead), lead).whoami()
    except Denied as why:
        return f"lead {lead} is not live on its board: {why}"
    return f"lead {lead} has ended" if found.retired or found.name != lead else ""


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
    takes: Callable[[str], bool] = harnessing.takes_up
    sandbox: Callable[[], str] = delegate_confined.unsandboxable
    family: Callable[[int], set[int]] = family
    lead_gone: Callable[[dict[str, Any]], str] = lead_gone
    recheck: Callable[[Path, cards.Card, str, str, Path], str] = cards_check.rerun
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
    now = card_digest(root, card)
    if release and release.get("cards", {}).get(card.id) != now:
        return "unreleased", "added or changed since the release; `poolhouse-delegate cards release` records it"
    if state == "returned" and entry.get("card") == now:
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
    """The cards of ``root`` as the commands and the web page show them, kept for the page as it is made. What was kept of accepted work whose card
    has landed (its file is gone) is dropped."""
    cards_ledger.landed(root, {card.id for card in cards.load(root)[0]})
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


def _data(text: object) -> str:
    """``text``, which may hold a local model's words or what its work printed, fenced as data."""
    return nudge_fence.fenced([nudge_fence.clean(text)])


def needs(root: Path) -> list[str]:
    """What waits for the lead: a card's delegate asking, work to review and cards sent back; one entry each, what a model or its work said
    fenced as data."""
    found = []
    for each in standing(root):
        name = str(each.entry.get("delegate") or "")
        if each.state == "running" and name and delegate_record.state_of(name)[0] == "asking":
            found.append(f"{each.card.id}: {name} asks you something; read your inbox and answer it")
        elif each.state == "review":
            found.append(f"{each.card.id}: review branch {each.entry.get('branch')} at {each.entry.get('commit')}, read `poolhouse-delegate result "
                         f"{name}`, then `poolhouse-delegate grade {name} correct|partly|wrong --critique TEXT --check \"RUN => SAW\"`; an "
                         "accepted branch is yours to publish as a pull request and merge")
        elif each.state == "returned":
            found.append(f"{each.card.id}: sent back. Split it, correct it, or `poolhouse-delegate cards retry {each.card.id}`. Why:\n"
                         f"{_data(each.why)}")
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


def outcome(root: Path, host: Host, card: cards.Card, name: str) -> dict[str, Any]:
    """What the ended run of delegate ``name`` on ``card`` came to: the ledger fields of a card in `review`, or `returned` with why. Its commit
    has to stay within the card and the card's check has to pass on it, run here; what the run's record says of a check counts for nothing."""
    state, detail = delegate_record.state_of(name)
    result, events = store.result(name), _events(name)
    tree, rec = result.get("tree") or {}, store.load(name)
    commit = str(tree.get("commit") or "")
    peak = _peak(events)
    if peak >= OVERSIZE:
        why = f"oversize: its context reached {peak:,} tokens, so split the card"
    elif state != "done":
        why = f"the run ended {state}: {delegate_record.cleaned(result.get('error') or detail, 300)}"
    elif not commit or rec is None:
        why = "it changed nothing"
    else:
        why = (cards_check.outside(root, card, rec.commit, commit)
               or host.recheck(root, card, commit, name, store.folder(name) / "recheck.log"))
    return {"state": "returned" if why else "review", "why": why, "branch": tree.get("branch") or "", "commit": commit,
            "peak": peak, "tokens": {key: result.get(key, 0) for key in ("input_tokens", "output_tokens", "cache_read_tokens")}}


def _serving(host: Host) -> tuple[str, str]:
    """``(model, base URL)`` of a live server of the card model whose slot holds a card's session; empty when there is none."""
    for port, entry in host.servers().items():
        window = int(entry.get("context") or 0) // max(1, int(entry.get("parallel") or 1))
        if not entry.get("embedding") and window >= cards.CAP * 2 and model_matches(str(entry.get("model") or ""), host.model) and host.live(entry):
            return str(entry["model"]), f"http://127.0.0.1:{port}"
    return "", ""


def _others(status: dict[str, Any], mine: set[int]) -> str:
    """Who besides the processes ``mine`` holds a server, claims the device or waits for either; empty for nobody."""
    for name, claim in (status.get("claims") or {}).items():
        purpose = str((claim.get("info") or {}).get("purpose") or name)
        if purpose != "resident decision" and claim.get("pid") not in mine:
            return f"the device is claimed: {purpose}"
    for waiter in status.get("claim_waiters") or []:
        if waiter.get("pid") not in mine:
            return f"the device is waited for: {(waiter.get('info') or {}).get('purpose') or waiter.get('name')}"
    for ask in status.get("queue") or []:
        if ask.get("pid") not in mine and not str(ask.get("label") or "").startswith(UNUSING):
            return f"a model is waited for by {ask.get('label') or ask.get('pid')}"
    for device, turns in (status.get("requests") or {}).items():
        if any(not turn.get("running") and turn.get("pid") not in mine for turn in turns):
            return f"a request waits for the {device}"
    for server in status.get("servers") or []:
        if server.get("loading"):
            return "a model is loading"
        for holder in server.get("holders") or []:
            pid = holder.get("pid")
            if pid not in mine and pid != server.get("pid") and not str(holder.get("label") or "").startswith(UNUSING):
                return f"port {server.get('port')} is in use by {holder.get('label') or pid}"
    return ""


def _not_free(host: Host) -> tuple[str, str]:
    """``(why the model is not free, the model to run on)``: one of the two is empty."""
    if live := [name for name in store.names() if delegate_record.state_of(name)[0] in delegate_record.LIVE]:
        return f"{live[0]} is running; one delegate at a time", ""
    try:
        if host.measuring() is not None:
            return "a measurement holds the device", ""
        if why := _others(host.status(), set()):
            return why, ""
        model, url = _serving(host)
        if model and not host.takes(model):
            return f"the server of {model} is not one a delegate takes as it stands, and the runner never starts another", ""
    except UNAVAILABLE as why:
        return f"the model broker could not be asked: {why}", ""
    if not model:
        return f"{host.model} is not being served with room for a card's session; the runner never loads a model", ""
    if host.busy(url) is not False or host.queued(url):
        return "the model is answering a request, or does not say", ""
    return "", model


def _intruder(host: Host, name: str) -> str:
    """Why delegate ``name`` must give the model up: someone else holds it or waits for it or for the device; empty while nobody does."""
    mine = host.family(int(jobs.recorded(delegate_record.JOB, home=delegate_record.folder(name)).get("pid") or 0))
    try:
        if host.measuring() is not None:
            return "a measurement holds the device"
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
    now = card_digest(root, each.card)
    if now != each.entry.get("card"):
        came: dict[str, Any] = {"state": "returned", "why": "its card changed while it ran",
                                "branch": str((store.result(name).get("tree") or {}).get("branch") or "")}
    else:
        came = outcome(root, host, each.card, name)
    _record(root, each.card.id, **came, card=now)
    said = f"; why:\n{_data(came['why'])}" if came["why"] else f", branch {came['branch']}"
    return "settled", f"{each.card.id}: {came['state']}{said}"


def watch(root: Path, host: Host) -> tuple[str, str] | None:
    """One look at the card of ``root`` that is running, by whoever asks (the runner, `cards wait`, a grade), one at a time: an ended run is
    settled, and a live one gives the model up to anyone else. None when no card is running."""
    with lock.held(cards_ledger.watching(root)), sessions.only_existing():
        for each in standing(root):
            if each.state == "running":
                return _watched(root, host, each)
    return None


def _held_for(root: Path, why: str) -> tuple[str, str]:
    """Hold the runner of ``root`` until a lead releases it again, so nothing is tried again every few seconds."""
    cards_ledger.edit(root, lambda ledger: ledger.update(hold=why))
    return "held", f"held: {why}"


def _launch(root: Path, host: Host, each: Standing, model: str) -> tuple[str, str]:
    """Start the delegate of card ``each`` on ``model``, bound to the released lead."""
    release = cards_ledger.read(root)["release"]
    lead = str(release.get("lead"))
    card = each.card
    if gone := host.lead_gone(release):
        return _held_for(root, f"{gone}; a live lead releases the runner again")
    reasons = [str(one) for one in each.entry.get("reasons") or []]
    pinned = card_digest(root, card)
    task = cards.brief(root, card, lead, reasons)
    before = set(store.names())
    words = ["start", "--task", task, "--edit", "--project", str(root), "--as", lead, "--kind", "card", "--card", card.id, "--model", model,
             *(["--memory", "--about", f"{card.title} {' '.join(card.write)}"] if card.memory else [])]
    status = host.start(words)
    new = [name for name in store.names() if name not in before and (rec := store.load(name)) and rec.card == card.id]
    if status or not new:
        return _held_for(root, f"{card.id}: no delegate started for lead {lead}; a live lead releases the runner again once what it said is "
                               "settled")
    _record(root, card.id, state="running", delegate=new[-1], why="", card=pinned, memory=card.memory,
            attempts=int(each.entry.get("attempts") or 0) + 1)
    host.quiet_since = None
    return "launched", f"{card.id}: started {new[-1]} for {lead}"


def step(root: Path, host: Host) -> tuple[str, str]:
    """Do the next thing for the project at ``root`` and say what: ``running``, ``settled``, ``launched``, ``waiting`` for the model, ``held`` or
    ``idle`` (no card is ready). Nothing it does registers an ended session again."""
    if looked := watch(root, host):
        return looked
    if why := held(root, cards_ledger.read(root)):
        return "held", f"held: {why}"
    if why := host.sandbox():
        return "held", f"held: a card runs only in a sandbox: {why}"
    ready = [each for each in standing(root) if each.state == "ready"]
    if not ready:
        return "idle", "no card is ready"
    why, model = _not_free(host)
    if why:
        host.quiet_since = None
        return "waiting", f"waiting: {why}"
    host.quiet_since = host.clock() if host.quiet_since is None else host.quiet_since
    if host.clock() - host.quiet_since < host.idle_s:
        return "waiting", f"waiting: the model has to be unused for {delegate_record.span(host.idle_s)} first"
    with sessions.only_existing():
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
                except (Denied, NodeError, OSError, RuntimeError, ValueError) as why:
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

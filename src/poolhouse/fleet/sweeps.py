"""A model sweep spread over the fleet: `plan` puts each model on the idle member with the
most room that fits it, `dispatch` sends the jobs, and `wait` polls the daemons.

`submit_bench` and `bench_export` are the two calls a member answers, over its daemon or, for
a `fleet.measuring.Local`, in this process.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from poolhouse.log import say
from poolhouse.units import human_bytes

from .jobs import DaemonError
from .measuring import TAIL, Job, Local, Refused
from .remote import MemberError
from .sizing import estimate

__all__ = ["Handle", "Plan", "bench_export", "dispatch", "plan", "submit_bench",
           "wait"]

UNANSWERED = (MemberError, DaemonError, ValueError, KeyError, OSError)
"""What asking a `Member`, or a `Local` answering as one, raises when it gets no answer."""


def submit_bench(member: Any, job: Job) -> dict[str, Any]:
    """``POST /bench`` on ``member``: the daemon's job record, or `Refused`."""
    if isinstance(member, Local):
        return member.host.submit(job).public()
    try:
        return member._json("POST", "/bench", job.public())
    except MemberError as exc:
        answered = _answered(str(exc))
        if answered and answered[0] == 409:
            raise Refused(str(answered[1].get("refused") or "refused"),
                          str(answered[1].get("error") or exc)) from None
        raise


def bench_export(member: Any, *, since: str = "", job: str = "", full: bool = True,
                 anyway: bool = False) -> dict[str, Any]:
    """``GET /bench/export`` on ``member``: its runs since ``since`` or since ``job`` began."""
    if isinstance(member, Local):
        return {**member.host.export(since=since, job=job, full=full, anyway=anyway),
                "host": member.name}
    query = urllib.parse.urlencode({k: v for k, v in (("since", since), ("job", job),
                                                        ("full", "1" if full else ""),
                                                        ("anyway", "1" if anyway else ""))
                                    if v})
    return member._json("GET", f"/bench/export?{query}")


def _answered(message: str) -> tuple[int, dict[str, Any]] | None:
    """The status and JSON body out of a `MemberError`'s message, or None."""
    found = re.search(r"-> (\d{3}): (\{.*\})", message, re.S)
    if not found:
        return None
    try:
        return int(found.group(1)), json.loads(found.group(2))
    except ValueError:
        return None


# -- dispatch ------------------------------------------------------------------------
class Plan(dict):
    """``{member: [model, ...]}`` -- and ``unplaced``, the models that fit nowhere, each with
    why not on every member, so nothing is dropped silently."""

    def __init__(self) -> None:
        super().__init__()
        self.unplaced: list[tuple[str, str]] = []


def plan(models: Sequence[str], members: Sequence[Any], *, needs: Mapping[str, int] | None = None,
         context: int = 32768, log: Callable[[str], None] = say) -> Plan:
    """Which member serves which model.

    Each member is asked ``health()`` for its name, whether it is idle (not busy, a free slot,
    not measuring) and its ``room_bytes``. Each model -- sized by ``needs`` when given, else
    by `estimate` at ``context`` -- goes, largest first, to an idle member with room for it:
    the one holding the fewest models so far, the roomiest on a tie, so two models land on
    two machines rather than both on the biggest. A model nobody fits is listed under
    ``unplaced`` with every member's reason, and printed. ``members`` may include `here()`.
    """
    sized = dict(needs or {})
    for model in models:
        if model not in sized:
            sized[model] = int(estimate(model, context=context))
    seen: list[tuple[Any, str, dict[str, Any]]] = []
    for member in members:
        try:
            health = member.health()
        except UNANSWERED as exc:  # a member that does not answer is not idle
            log(f"  {getattr(member, 'name', member)}: did not answer ({exc})")
            continue
        seen.append((member, _name_of(member, health), health))
    out = Plan()
    for member, _name, _health in seen:
        out[member] = []
    log(f"planning {len(models)} model(s) over {len(seen)} member(s):")
    for model in sorted(models, key=lambda m: -sized.get(m, 0)):
        need = sized.get(model, 0)
        fitting: list[tuple[Any, str, int]] = []
        reasons: list[str] = []
        for member, name, health in seen:
            room = int(health.get("room_bytes") or 0)
            why = _not_idle(health)
            if why:
                reasons.append(f"{name}: {why}")
            elif room and need and need > room:
                reasons.append(f"{name}: room {human_bytes(room)} < {human_bytes(need)}")
            else:
                fitting.append((member, name, room))
        if not fitting:
            said = "; ".join(reasons) or "no members answered"
            out.unplaced.append((model, said))
            log(f"  {model:<40} {human_bytes(need):>7} fits nowhere: {said}")
            continue
        member, name, room = min(fitting, key=lambda f: (len(out[f[0]]), -f[2]))
        out[member].append(model)
        log(f"  {model:<40} {human_bytes(need):>7} -> {name} (room {human_bytes(room) if room else '?'})"
            + ("" if need else "  [size unknown; the member's preflight decides]"))
    return out


def _name_of(member: Any, health: Mapping[str, Any] | None = None) -> str:
    """What a member calls itself: its beacon's name, else what ``/health`` says, else
    whatever it is addressed by -- a `Member` made from a URL alone is named by the URL."""
    return _said_by(member, "name", health) or str(getattr(member, "name", member))


def _machine_of(member: Any, health: Mapping[str, Any] | None = None) -> str:
    """A member's `home.machine_id`: its beacon's, else what ``/health`` says, else ""."""
    return _said_by(member, "machine", health)


def _said_by(member: Any, field_name: str, health: Mapping[str, Any] | None) -> str:
    """``field_name`` off the member's beacon, else out of its ``/health``, else ""."""
    beacon = getattr(member, "beacon", None)
    if beacon is not None and getattr(beacon, field_name, ""):
        return str(getattr(beacon, field_name))
    return str((_health_of(member) if health is None else health).get(field_name) or "")


def _health_of(member: Any) -> Mapping[str, Any]:
    """The member's ``/health``, or {} when it does not answer."""
    try:
        return member.health()
    except UNANSWERED:
        return {}


def _not_idle(health: Mapping[str, Any]) -> str:
    if health.get("measuring"):
        return "measuring"
    if health.get("busy"):
        return "busy"
    if int(health.get("free", 1) or 0) < 1:
        return "no free slot"
    return ""


@dataclass
class Handle:
    """One dispatched job: where it went, the daemon's id for it, and how it stands."""

    member: Any
    job: Job
    id: str = ""
    name: str = ""
    state: str = "pending"
    """pending | running | done | failed | stopped | refused"""
    log: str = ""
    why: str = ""
    host: str = ""
    """What the member calls itself: the label `gather` files its runs under."""
    machine: str = ""
    """The member's `home.machine_id`, which `gather` stamps on its runs."""

    @property
    def member_name(self) -> str:
        return self.host or str(getattr(self.member, "name", self.member))

    @property
    def ended(self) -> bool:
        return self.state in ("done", "failed", "stopped", "refused")


def dispatch(jobs: Mapping[Any, Job], *, log: Callable[[str], None] = say) -> list[Handle]:
    """Send each job to its member. A refusal is a `Handle` in state ``refused`` with the
    reason, printed, not an exception: the rest of the sweep still goes out."""
    out: list[Handle] = []
    for member, job in jobs.items():
        health = _health_of(member)
        handle = Handle(member=member, job=job, host=_name_of(member, health),
                        machine=_machine_of(member, health))
        try:
            answered = submit_bench(member, job)
        except Refused as why:
            handle.state, handle.why = "refused", f"{why.kind}: {why}"
            log(f"  {handle.member_name}: refused ({why.kind}) -- {why}")
        except UNANSWERED as exc:  # one unreachable member must not stop the rest
            handle.state, handle.why = "refused", f"unreachable: {exc}"
            log(f"  {handle.member_name}: unreachable -- {exc}")
        else:
            handle.id = str(answered.get("id") or "")
            handle.name = str(answered.get("name") or job.name)
            handle.state = str(answered.get("state") or "running")
            handle.log = str(answered.get("log") or "")
            log(f"  {handle.member_name}: {handle.name} {handle.state} (job {handle.id}"
                + (f", log {handle.log}" if handle.log else "") + ")")
        out.append(handle)
    return out


def wait(handles: Sequence[Handle], *, poll_s: float = 20.0, timeout_s: float | None = None,
         log: Callable[[str], None] = say) -> list[Handle]:
    """Poll each member's job until every one has ended, printing one line per change and
    the last `TAIL` lines of the log when a job ends. ``timeout_s`` bounds the whole wait;
    a job still running then is left in state ``running`` and said so."""
    deadline = time.monotonic() + timeout_s if timeout_s else None
    while True:
        for handle in handles:
            if handle.ended:
                continue
            try:
                current = handle.member.job(handle.id)
            except UNANSWERED as exc:  # said, and asked again next round
                log(f"  {handle.member_name}: could not read job {handle.id}: {exc}")
                continue
            state = str(current.get("state") or "")
            if state == handle.state:
                continue
            handle.state = state
            log(f"  {handle.member_name}: {handle.name} {state}")
            if handle.ended:
                try:
                    tail = handle.member.log(handle.id, tail=TAIL).rstrip()
                except UNANSWERED:
                    tail = ""
                for line in tail.splitlines():
                    log(f"      {line}")
        if all(h.ended for h in handles):
            return list(handles)
        if deadline is not None and time.monotonic() > deadline:
            for handle in handles:
                if not handle.ended:
                    log(f"  {handle.member_name}: {handle.name} still {handle.state} after "
                        f"{timeout_s:.0f}s; left running")
            return list(handles)
        time.sleep(poll_s)

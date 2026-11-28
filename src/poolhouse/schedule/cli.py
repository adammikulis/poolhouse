"""``poolhouse schedule``: add, list, show and cancel scheduled jobs (docs/scheduled-jobs.md).

    poolhouse schedule add FILE --at "2026-11-28 23:07" [--window 3h] [--name N] [--code DIR] [--dry-run]
    poolhouse schedule list        poolhouse schedule show ID        poolhouse schedule cancel ID

``add`` and ``cancel`` are decided by `poolhouse.schedule.who`: an agent session in no sandbox, or the person.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import authority, caller, node_launch, schedule_record as record
from poolhouse.board import place
from poolhouse.board.client import Client, NodeError
from poolhouse.command import Group, flag, option
from poolhouse.errors import Denied
from poolhouse.fleet import autostart_check
from poolhouse.log import say
from poolhouse.schedule import check, code, runner, spawn, steps, who

__all__ = ["Asked", "add_job", "autostart_note", "main", "moment", "span"]

COMMANDS = Group("poolhouse schedule",
                 "Poolhouse's own commands run later: a bench queue, a comparison of delegates, a model removal, once the device "
                 "is free.", errors=(ValueError, OSError, PermissionError, RuntimeError))
main = COMMANDS.run
WINDOW = "1h"
MAX_HOURS = 24.0
_SPAN = re.compile(r"(\d+(?:\.\d+)?)([mh])")


def span(text: str) -> float:
    """``90m`` or ``2h`` in seconds."""
    found = _SPAN.fullmatch(text.strip())
    if found is None:
        raise ValueError(f"{text!r} is not a length of time: write it as 90m or 2h")
    return float(found[1]) * (60 if found[2] == "m" else 3600)


def moment(text: str, now: float) -> float:
    """When ``text`` names: ``now``, ``+30m``, ``23:07`` (the next one) or ``2026-11-28 23:07``, in local time."""
    said = text.strip()
    if said == "now":
        return now
    if said.startswith("+"):
        return now + span(said[1:])
    for shape in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return time.mktime(time.strptime(said, shape))
        except ValueError:
            continue
    try:
        clock = time.strptime(said, "%H:%M")
    except ValueError:
        raise ValueError(f"{text!r} is not a time: write 2026-11-28 23:07, 23:07, +30m or now") from None
    today = time.localtime(now)
    for days in (0, 1):
        found = time.mktime((today.tm_year, today.tm_mon, today.tm_mday + days, clock.tm_hour, clock.tm_min, 0, 0, 0, -1))
        if found >= now - 60:
            return found
    return found


def autostart_note(state: str) -> str:
    """What happens to a waiting job when the computer restarts, in plain words, for the autostart ``state``."""
    awake = ("The computer must be on and awake at the start time: on mains power it is kept from idle sleep while the job "
             "waits; a sleeping computer or a closed lid on battery misses the time, which is then reported.")
    if state == "current":
        return f"Autostart is enabled: after a restart of the computer the job is picked up when you log in. {awake}"
    return (f"Autostart is not enabled on this computer (state: {state}): the job runs with every terminal and window closed, "
            f"but after a restart or a log out it is picked up only once Poolhouse is open again or a `poolhouse schedule` "
            f"command runs; if its latest start has passed by then it is reported missed. {awake}")


def _prepare(job: dict[str, Any], *, checkout: str, python: str, cwd: Path) -> Callable[[Path], None]:
    """Fill a job's folder: the copy of the code, the checked bench lines and the copies of their input files."""
    def fill(where: Path) -> None:
        if not any(step["kind"] == "bench" for step in job["steps"]):
            return
        source = code.code_dir(checkout)
        copy = None
        if source is not None:
            copy = where / "code"
            code.snapshot(source, copy)
        rows = steps.bench_rows(job["steps"], lambda argvs: code.check_bench(argvs, python=python, copy=copy,
                                                                              cwd=where / "work"))
        pins: dict[str, list[int]] = {}
        for step, values in rows:
            step["argv"] = code.freeze(step["argv"], values, base=cwd, into=where / "inputs", pins=pins)
        job["pins"] = pins
        job["code"] = {"python": python, "copied": copy is not None, **(code.revision(source) if source else {})}

    return fill


def _told(identity: Mapping[str, Any], notify: str, cwd: Path) -> tuple[str, str]:
    """The board and the session the notice goes to: ``notify``, else the session this runs in, else everybody on the project's
    board."""
    sessions = list(identity.get("sessions") or [])
    wanted = notify or os.environ.get("POOLHOUSE_WORKSPACE_AGENT", "")
    found = (next((one for one in sessions if one["name"] == wanted), None)
             or next((one for one in sessions if not one["parent"]), None) or next(iter(sessions), None))
    agent = notify or (found["name"] if found else "")
    if found:
        return found["board"], agent
    try:
        return place.at_path(Client(node_launch.default_state()), cwd), agent
    except (Denied, NodeError, OSError, ValueError):
        return "", agent


def _delegate_gates(found: list[dict[str, Any]], identity: Mapping[str, Any]) -> None:
    """The gates a delegate's start passes, decided now for whoever schedules it: the job's own process starts it later outside
    every agent, so a gate held back from agents is asked of the agent here, at once."""
    attested = "an agent's session is above it" if identity.get("sessions") else ""
    for step in found:
        if step["kind"] != "delegate":
            continue
        authority.require("workspace.agents", "schedule a delegate run", attested=attested)
        asked = step["delegate"]
        if asked["project"] or asked["edit"]:
            authority.require("isolation.grant", "schedule a delegate on a project", attested=attested,
                              scope=authority.Scope(project=asked["project"]))


@dataclass(frozen=True)
class Asked:
    """What `add` is asked beyond the file: the window it may start in, its name, the code and interpreter of its bench steps
    (this Poolhouse's when empty), the session told how it ended and the longest it may run."""

    at: float
    latest: float
    name: str = ""
    checkout: str = ""
    python: str = ""
    notify: str = ""
    max_hours: float = MAX_HOURS


def add_job(file: Path, asked: Asked, *, identity: Mapping[str, Any] | None = None, cwd: Path | None = None,
            begin: Callable[[str], None] = spawn.start) -> dict[str, Any]:
    """Schedule the job of ``file``, decided now for this caller (`who.admit`), audited, with its process started."""
    identity = who.admit("scheduling a job") if identity is None else identity
    here = (cwd or Path.cwd()).resolve()
    if asked.latest < time.time():
        raise ValueError(f"the latest start, {record.when(asked.latest)}, has passed")
    if not 0 < asked.max_hours <= 168:
        raise ValueError("--max-hours is more than 0 and at most 168")
    found = steps.parse(file.read_text(encoding="utf-8"), base=here)
    _delegate_gates(found, identity)
    board, agent = _told(identity, asked.notify, here)
    job = {"name": asked.name or file.stem, "at": asked.at, "latest": asked.latest, "max_hours": asked.max_hours,
           "created": time.time(), "steps": found,
           "by": {"who": identity["who"], "sessions": identity.get("sessions") or [], "board": board, "agent": agent,
                  "cwd": str(here)}, "code": {}, "pins": {}, "child": None}
    made = record.create(job, _prepare(job, checkout=asked.checkout, python=asked.python or sys.executable, cwd=here))
    digest = hashlib.sha256(json.dumps(made["steps"], sort_keys=True).encode()).hexdigest()
    caller.record("schedule.create", job=made["id"], by=identity["who"], agent=agent, at=record.when(asked.at),
                  latest=record.when(asked.latest), steps=[step["words"] for step in made["steps"]], steps_sha256=digest,
                  commit=made["code"].get("commit", ""))
    begin(made["id"])
    return made


def _listed(job: Mapping[str, Any]) -> None:
    for step in job["steps"]:
        mark = {"smoke": "smoke:", "then": "then: "}.get(step["role"], "      ")
        words = " ".join(step["argv"]) if step["kind"] == "bench" else step["words"]
        say(f"  {step['n']:>2}  {mark} {step['kind']:<12} {words}")


@COMMANDS.command("add", help="schedule the job a file describes", options=[
    flag("file", help="the job file: bench queue lines, `delegate` and `remove-model` lines"),
    flag("--at", required=True, help="when it may start: 2026-11-28 23:07, 23:07, +30m or now"),
    flag("--window", default=WINDOW, help="how long after --at it may still start (default: %(default)s)"),
    flag("--name", default="", help="what to call it (default: the file's name)"),
    flag("--code", default="", metavar="DIR", help="the source checkout whose bench the job runs (default: this one)"),
    flag("--python", default="", metavar="PATH", help="the interpreter of the bench steps (default: this one)"),
    flag("--notify", default="", metavar="AGENT", help="the session told how it ended (default: this one)"),
    flag("--max-hours", type=float, default=MAX_HOURS, help="the longest it may run (default: %(default)s)"),
    option("dry-run", help="check everything and say what would run; schedule nothing"),
])
def add(args: argparse.Namespace) -> int:
    """Check a job file and schedule it."""
    file = Path(args.file).expanduser()
    at = moment(args.at, time.time())
    latest = at + span(args.window)
    if args.dry_run:
        here = Path.cwd().resolve()
        job: dict[str, Any] = {"steps": steps.parse(file.read_text(encoding="utf-8"), base=here)}
        with tempfile.TemporaryDirectory(prefix="poolhouse-schedule-") as held:
            (Path(held) / "work").mkdir()
            _prepare(job, checkout=args.code, python=args.python or sys.executable, cwd=here)(Path(held))
            say(f"{file}: {len(job['steps'])} step(s), start {record.when(at)}, latest start {record.when(latest)}")
            _listed(job)
        found = job.get("code") or {}
        if found.get("copied"):
            say(f"code: {found.get('commit', '?')[:12] or 'no commit'} of {found.get('checkout')}"
                + (" with uncommitted changes" if found.get("uncommitted") else ""))
        say(autostart_note(autostart_check.state_word()))
        say("nothing was scheduled: --dry-run")
        return 0
    made = add_job(file, Asked(at, latest, args.name, args.code, args.python, args.notify, args.max_hours))
    say(f"scheduled {made['id']} ({made['name']}): start {record.when(at)}, latest start {record.when(latest)}")
    _listed(made)
    say(f"the notice goes to {made['by']['agent'] or '#announcements'}; `poolhouse schedule show {made['id']}`")
    say(autostart_note(autostart_check.state_word()))
    return 0


@COMMANDS.command("list", help="every scheduled job, its state and why it waits", options=[option("json")])
def listing(args: argparse.Namespace) -> int:
    """The jobs, oldest first; a waiting one with no process gets one."""
    spawn.ensure()
    rows = [record.view(one) for one in record.jobs()]
    if args.json:
        say(json.dumps(rows, indent=2))
        return 0
    if not rows:
        say("no job is scheduled")
    for row in rows:
        say(f"{row['id']}  {row['state']:<9}  start {row['starts'] or '-':<16}  {row['name']}"
            + (f"\n    {row['why']}" if row["why"] else ""))
    return 0


@COMMANDS.command("show", help="one job: its steps, results and the end of its output",
                  options=[flag("job"), option("json"), flag("--log", type=int, default=20, help="lines of output shown")])
def show(args: argparse.Namespace) -> int:
    """One job in full."""
    spawn.ensure()
    found = record.load(args.job)
    if found is None:
        raise ValueError(f"no scheduled job {args.job}; `poolhouse schedule list` names them")
    row = record.view(found)
    tail = record.log_tail(args.job).splitlines()[-args.log:] if args.log > 0 else []
    if args.json:
        say(json.dumps({**row, "tail": tail}, indent=2))
        return 0
    say(f"{row['id']} {row['name']}: {row['state']}" + (f": {row['why']}" if row["why"] else ""))
    say(f"  start {row['starts']}, latest start {row['latest_start']}, scheduled {row['created']} by {row['by']}")
    if row["started"] or row["ended"]:
        say(f"  started {row['started'] or '-'}, ended {row['ended'] or '-'}")
    for step in row["steps"]:
        say(f"  {step['n']:>2}  {step['state']:<8} {step['words']}" + (f": {step['why']}" if step["why"] else ""))
    say(f"  output: {row['log']}")
    for line in tail:
        say(f"    {line}")
    return 0


@COMMANDS.command("cancel", help="cancel a job: a waiting one never starts, a running one stops its step", options=[flag("job")])
def cancel(args: argparse.Namespace) -> int:
    """Cancel one job."""
    identity = who.admit("cancelling a scheduled job")
    before = record.load(args.job)
    if before is None:
        raise ValueError(f"no scheduled job {args.job}; `poolhouse schedule list` names them")
    if before["state"] in record.ENDED:
        say(f"{args.job} had already ended {before['state']}")
        return 0
    found = record.cancel(args.job, identity["who"])
    say(f"{args.job}: " + ("cancelled" if found["state"] == "cancelled" else "asked to cancel; it stops within seconds"))
    spawn.ensure()
    return 0


@COMMANDS.command("hold", help="a job's own process, which Poolhouse starts: wait for the job and run it", options=[flag("job")])
def hold(args: argparse.Namespace) -> int:
    """`runner.hold`."""
    return runner.hold(args.job)


@COMMANDS.command("check-lines", help="read bench lines from standard input through this code's parser; scheduling a job starts it")
def check_lines(_args: argparse.Namespace) -> int:
    """`check.answer`."""
    return check.answer()


if __name__ == "__main__":
    raise SystemExit(main())

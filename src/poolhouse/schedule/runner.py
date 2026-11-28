"""One job's own process (``poolhouse schedule hold ID``, started by `spawn`). It waits for the job's time, takes the device
when it is free, runs the steps one at a time, records what came of each and tells whoever scheduled it.

One process holds a job at a time (`record.RUNNER_LOCK`). It belongs to no terminal, app or daemon, so quitting or updating
any of them leaves it running; one that only waits moves to a newly selected runtime that has scheduled jobs by starting its
successor, and keeps the job when the successor does not take it. A time that passes without a start is recorded as
``missed`` with why, never dropped. A job whose process ended while it ran is failed, and nothing of it is run a second time.

A disk that refuses a write ends nothing: a change of the record is held here and written with the next, a job is not started
until its start is recorded, and an end that cannot be written is told at once and written when there is room.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from poolhouse import caller, runtime, schedule_record as record
from poolhouse.lock import Busy, only_one, pid_alive
from poolhouse.log import say, warn
from poolhouse.schedule import execute, notice, spawn, who
from poolhouse.schedule.device import Device
from poolhouse.schedule.power import SOON_S, Awake
from poolhouse.serve.process import kill_process_tree, started_at

__all__ = ["GAP_S", "HANDOVER_BEFORE_S", "POLL_S", "SETTLE_S", "STEP_WAIT_S", "Runner", "World", "hold"]

POLL_S = 15.0
GAP_S = 180.0
"""A waiting job unchecked for this long past `POLL_S` was not being watched: the computer slept, or its process was gone."""
STEP_WAIT_S = 6 * 3600.0
"""How long a started job waits for the device again before a bench step, should its reservation have been lost."""
HANDOVER_BEFORE_S = 300.0
"""A job due sooner than this keeps its process: none is started anew just before the time."""
SETTLE_S = 7 * 86400.0
"""How long the process of an ended job stays to write what the disk refused."""
STEPS = {"bench": execute.bench, "delegate": execute.delegate, "remove-model": execute.remove}
Change = Callable[[dict[str, Any]], object]


@dataclass(frozen=True)
class World:
    """What a job's process reads and does around it: the time, waiting, telling, which runtime is selected and whether it has
    scheduled jobs, and how often a step in flight is looked at."""

    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    tell: Callable[[dict[str, Any]], str] = notice.send
    selection: Callable[[], Any] = runtime.selection_prefix
    look_s: float = execute.LOOK_S
    runs: Callable[[Any], bool] = spawn.runs_jobs


class Runner:
    """The job ``job`` as its process drives it."""

    def __init__(self, job: str, world: World | None = None, *, device: Device | None = None, awake: Awake | None = None) -> None:
        self.job, self.world = job, world or World()
        self.device = device or Device(job, say=self.say)
        self.awake = awake or Awake()
        self._log: Any = None
        self._owed: list[Change] = []

    def say(self, line: str) -> None:
        if self._log is not None:
            with contextlib.suppress(OSError):  # a full disk: the line is lost and the job goes on
                self._log.write(f"{time.strftime('%H:%M:%S')} {line}\n".encode())

    def read(self) -> dict[str, Any]:
        """The job's record with every change the disk has not taken yet."""
        found = record.load(self.job)
        if found is None:
            raise record.Refused(f"no scheduled job {self.job}")
        for change in self._owed:
            change(found)
        return found

    def _write(self, *more: Change) -> dict[str, Any]:
        """Write the changes still owed and ``more`` to the record, all or none; the record as written."""
        changes = [*self._owed, *more]

        def apply(job: dict[str, Any]) -> None:
            for change in changes:
                change(job)

        written = record.update(self.job, apply)
        self._owed.clear()
        return written

    def keep(self, change: Change | None = None) -> bool:
        """Write ``change`` to the record, after any the disk refused earlier. One it refuses now is held here and written
        with the next; whether everything is written."""
        if change is not None:
            self._owed.append(change)
        try:
            self._write()
        except OSError as why:
            self.say(f"the job's record could not be written, and is tried again: {why}")
            return False
        return True

    def _audit(self, event: str, **said: Any) -> None:
        try:
            caller.record(event, job=self.job, **said)
        except OSError as why:
            self.say(f"the audit log could not be written ({event}): {why}")

    def run(self) -> str:
        """Drive the job as far as this process takes it: the state it ended in, or ``handover`` when a successor should wait."""
        with (record.folder(self.job) / "log").open("ab", buffering=0) as self._log:
            try:
                job = self.read()
                if job["state"] in record.ENDED:
                    self._notice(job)
                    return job["state"]
                if job["state"] == "running":
                    return self._interrupted(job)
                outcome = self._wait()
                return self._run_steps() if outcome == "running" else outcome
            finally:
                self.awake.release()

    # -- waiting ---------------------------------------------------------------------------

    def _wait(self) -> str:
        born = self.world.selection()
        while True:
            job = self.read()
            if job["state"] in record.ENDED:
                self._notice(job)
                return job["state"]
            if job.get("cancel"):
                return self._end("cancelled", f"cancelled by {job['cancel']['by']}")
            now = self.world.clock()
            if now > job["latest"]:
                return self._end("missed", self._missed(job, now))
            early = job["at"] - now
            if early > 0:
                self.awake.hold("waiting" if early < SOON_S else "")
                if early > HANDOVER_BEFORE_S and (chosen := self.world.selection()) != born and self.world.runs(chosen):
                    return "handover"
                self._checked("")
                self.world.sleep(min(POLL_S, early))
                continue
            self.awake.hold("waiting")
            if not (why := self.device.take(alone=any(step["kind"] == "bench" for step in job["steps"]))):
                outcome, why = self._begin()
                if outcome:
                    return outcome
            if why != job.get("why"):
                self.say(f"waiting: {why}")
            self._checked(why)
            self.world.sleep(POLL_S)

    def _checked(self, why: str) -> None:
        now = self.world.clock()
        self.keep(lambda job: job.update(checked=now, why=why))

    @staticmethod
    def _missed(job: dict[str, Any], now: float) -> str:
        checked, last = float(job.get("checked") or 0), str(job.get("why") or "")
        window = f"it could start from {record.when(job['at'])} to {record.when(job['latest'])}"
        if checked < job["at"]:
            return (f"{window}, and nothing checked it in that time: the computer was asleep or off, or no process of the job "
                    f"was running")
        if min(now, job["latest"]) - checked > GAP_S + POLL_S:
            return (f"{window}; from {record.when(checked)} the computer slept or the job's process was not running, and before "
                    f"that the device was not free: {last}")
        return f"{window}, and the device was not free: {last}"

    def _begin(self) -> tuple[str, str]:
        """Record the start: ``("running", "")``, ``("cancelled", "")``, or ``("", why)`` when it could not be recorded, so
        nothing may start yet."""
        now = self.world.clock()

        def start(job: dict[str, Any]) -> None:
            if not job.get("cancel"):
                job.update(state="running", why="", started=now, checked=now)

        try:
            job = self._write(start)
        except OSError as why:
            self.device.give_back()
            return "", f"the start could not be recorded, so nothing was started: {why}"
        if job["state"] != "running":
            self.device.give_back()
            return self._end("cancelled", f"cancelled by {job['cancel']['by']}"), ""
        self._audit("schedule.run", scheduled_by=job["by"].get("who", ""), agent=job["by"].get("agent", ""))
        self.say("the device is free: starting")
        return "running", ""

    # -- running ---------------------------------------------------------------------------

    def _stop(self) -> str:
        try:
            job = self.read()
        except OSError as why:
            self.say(f"the job's record could not be read: {why}")
            return ""
        if job.get("cancel"):
            return f"cancelled by {job['cancel']['by']}"
        if self.world.clock() - job["started"] > job["max_hours"] * 3600:
            return f"the job ran past its {job['max_hours']:g} hours"
        return ""

    def _note(self, step: dict[str, Any], state: str, why: str = "", seconds: float = 0.0, **more: Any) -> None:
        result = {"state": state, "why": why, "seconds": round(seconds, 1), **more}
        self.keep(lambda job: job["results"].__setitem__(str(step["n"]), result))
        if state != "running":
            self.say(f"step {step['n']} ({step['words']}): {state}" + (f": {why}" if why else ""))

    def _child(self, pid: int) -> None:
        held = {"pid": pid, "started": started_at(pid)} if pid else None
        self.keep(lambda job: job.update(child=held))

    def _device_again(self) -> str:
        """Hold the device for the next bench step, waiting for it should it have been lost; why it could not be held."""
        began = self.world.clock()
        while why := self.device.take(alone=True):
            if stopped := self._stop():
                return stopped
            if self.world.clock() - began > STEP_WAIT_S:
                return f"the device was not free again within {STEP_WAIT_S / 3600:g} hours: {why}"
            self.keep(lambda job, why=why: job.update(why=f"waiting between steps: {why}"))
            self.world.sleep(POLL_S)
        self.keep(lambda job: job.update(why=""))
        return ""

    def _run_steps(self) -> str:
        job = self.read()
        run = execute.Run(job, self._log, self._stop, self._child, self.world.look_s)
        self.awake.hold("running")
        failed_smoke, bad, step, ran = 0, False, None, False
        # what a later removal rests on: whether a step kept a result, and the first measuring step that kept nothing usable
        proven, lacking, held_back, before = False, "", False, 0
        try:
            for step in job["steps"]:
                if self._stop():
                    break
                removal = step["kind"] == "remove-model"
                before += not removal
                if step["role"] == "then" and failed_smoke:
                    self._note(step, "skipped", f"its smoke (step {failed_smoke}) failed")
                    failed_smoke = 0
                    continue
                failed_smoke = 0
                if removal and bad:
                    self._note(step, "skipped", "a model is removed only when every step before it passed")
                    continue
                if removal and (lacking or (before and not proven)):
                    self._note(step, "skipped", "a model is removed only after usable results: "
                               + (lacking or "no step before it kept a result"))
                    held_back = True
                    continue
                self._note(step, "running")
                began = self.world.clock()
                state, why = self._one(run, step)
                more: dict[str, Any] = {}
                if state == "ok" and step["kind"] == "delegate":
                    proven = True
                elif state == "ok" and step.get("measures"):
                    lack = execute.unusable(run, step)
                    more, why = {"usable": not lack}, lack or why
                    proven = proven or not lack
                    lacking = lacking or (f"step {step['n']} {lack}" if lack else "")
                self._note(step, state, why, self.world.clock() - began, **more)
                if state != "ok":
                    bad = True
                    failed_smoke = step["n"] if step["role"] == "smoke" else 0
            ran = True
        finally:
            if not ran:
                self._end("failed", "the job's process ended" + (f" during step {step['n']}" if step else "")
                          + " (the computer shut down, the process was told to stop, or it failed: runner.log in the job's folder "
                            "has the cause); nothing of it is run again")
        if (found := self.read()).get("cancel"):
            return self._end("cancelled", f"cancelled by {found['cancel']['by']}")
        stopped = self._stop()
        short = [f"step {n} {one['state']}: {one['why']}" for n, one in found["results"].items()
                 if one["state"] != "ok" or one.get("usable") is False]
        return self._end("failed" if bad or held_back or stopped else "done", stopped or "; ".join(short)[:600])

    def _one(self, run: execute.Run, step: dict[str, Any]) -> tuple[str, str]:
        if step["kind"] == "bench":
            if why := self._device_again():
                return "failed", why
        else:
            self.device.give_back()   # a delegate leases its own model, and a removal needs no device
        try:
            return STEPS[step["kind"]](run, step)
        except (OSError, ValueError, RuntimeError) as why:
            return "failed", f"{type(why).__name__}: {why}"

    def _interrupted(self, job: dict[str, Any]) -> str:
        """End a job whose earlier process is gone. A step it left running is waited for, never ended, unless cancelled or past the
        job's time: its result is not known either way."""
        child = job.get("child") or {}
        while child.get("pid") and child.get("started") and pid_alive(child["pid"], child["started"], tolerance=1.0):
            if self._stop():
                kill_process_tree(child["pid"], grace_s=60.0)
                break
            self._checked("a step an earlier process of this job started is still running")
            self.world.sleep(POLL_S)
        running = next((n for n, one in job.get("results", {}).items() if one.get("state") == "running"), "")
        return self._end("failed", "the job's process ended while it ran" + (f", during step {running}" if running else "")
                         + " (the computer restarted, or the process was killed); that step's result is not known, later steps "
                           "did not run and nothing is run again")

    # -- ending ----------------------------------------------------------------------------

    def _end(self, state: str, why: str) -> str:
        now = self.world.clock()

        def end(job: dict[str, Any]) -> None:
            job.update(state=state, why=why, ended=now, child=None)
            for one in job["results"].values():
                if one["state"] == "running":
                    one.update(state="failed", why=one["why"] or why)

        self.device.give_back()
        self.keep(end)
        job = self.read()
        self._audit("schedule.end", state=state, why=why, scheduled_by=job["by"].get("who", ""))
        self.say(f"job {state}" + (f": {why}" if why else ""))
        self._notice(job)
        self._settle()
        return state

    def _settle(self) -> None:
        """Stay until the record holds everything of an ended job, for `SETTLE_S` at most."""
        until = self.world.clock() + SETTLE_S
        while self._owed and self.world.clock() < until:
            self.world.sleep(POLL_S)
            self.keep()
        if self._owed:
            warn(f"the end of scheduled job {self.job} was never written to its record")

    def _notice(self, job: dict[str, Any]) -> None:
        if (job.get("notice") or {}).get("sent"):
            return
        error = self.world.tell(job)
        now = self.world.clock()
        said = {"sent": 0.0 if error else now, "tried": now, "error": error}
        self.keep(lambda kept: kept.update(notice=said))
        if error:
            self.say(f"the notice was not sent and will be tried again: {error}")


def _stopped(signum: int, _frame: Any) -> None:
    raise SystemExit(128 + signum)


def _take_up(job: str) -> None:
    """Say in the job's record that this process holds it, and run in the environment the job was scheduled in."""
    holder = {"pid": os.getpid(), "started": started_at(os.getpid()), "python": sys.executable, "at": time.time()}
    try:
        found = record.update(job, lambda kept: kept.update(runner=holder))
    except OSError as why:
        warn(f"this process could not be recorded as holding {job}: {why}")
        found = record.load(job) or {}
    taken = spawn.adopted(found.get("env") or {}, os.environ)
    os.environ.clear()
    os.environ.update(taken)


def hold(job: str) -> int:
    """Hold and drive job ``job`` as its process; 0 when another process holds it already."""
    kept = spawn.environment()
    kept.update({name: os.environ[name] for name in ("PYTHONPATH",) if os.environ.get(name)})
    os.environ.clear()
    os.environ.update(kept)
    if why := who.refused_here():
        say(why)
        return 1
    signal.signal(signal.SIGTERM, _stopped)
    while True:
        try:
            with only_one(record.folder(job) / record.RUNNER_LOCK, wait=False, note="scheduled job", announce=say):
                _take_up(job)
                outcome = Runner(job).run()
        except Busy:
            say(f"another process holds {job}")
            return 0
        except OSError as why:  # a full disk, say: the job keeps its process, which looks again
            warn(f"the process of {job} could not go on, and tries again: {why}")
            time.sleep(POLL_S)
            continue
        if outcome != "handover" or spawn.handover(job):
            return 0
        say(f"the successor did not take {job}; this process keeps it")

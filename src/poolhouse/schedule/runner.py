"""One job's own process (``poolhouse schedule hold ID``, started by `spawn`). It waits for the job's time, takes the device
when it is free, runs the steps one at a time, records what came of each and tells whoever scheduled it.

One process holds a job at a time (`record.RUNNER_LOCK`). It belongs to no terminal, app or daemon, so quitting or updating
any of them leaves it running; one that only waits moves to a newly selected runtime by starting its successor. A time that
passes without a start is recorded as ``missed`` with why, never dropped. A job whose process ended while it ran is failed, and
nothing of it is run a second time.
"""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from poolhouse import caller, runtime, schedule_record as record
from poolhouse.lock import Busy, only_one, pid_alive
from poolhouse.log import say
from poolhouse.schedule import execute, notice, spawn, who
from poolhouse.schedule.device import Device
from poolhouse.schedule.power import SOON_S, Awake
from poolhouse.serve.process import kill_process_tree, started_at

__all__ = ["GAP_S", "POLL_S", "STEP_WAIT_S", "Runner", "World", "hold"]

POLL_S = 15.0
GAP_S = 180.0
"""A waiting job unchecked for this long past `POLL_S` was not being watched: the computer slept, or its process was gone."""
STEP_WAIT_S = 6 * 3600.0
"""How long a started job waits for the device again before a bench step, should its reservation have been lost."""
STEPS = {"bench": execute.bench, "delegate": execute.delegate, "remove-model": execute.remove}


@dataclass(frozen=True)
class World:
    """What a job's process reads and does around it: the time, waiting, telling, which runtime is selected, and how often a
    step in flight is looked at."""

    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    tell: Callable[[dict[str, Any]], str] = notice.send
    selection: Callable[[], Any] = runtime.selection_prefix
    look_s: float = execute.LOOK_S


class Runner:
    """The job ``job`` as its process drives it."""

    def __init__(self, job: str, world: World | None = None, *, device: Device | None = None, awake: Awake | None = None) -> None:
        self.job, self.world = job, world or World()
        self.device = device or Device(job, say=self.say)
        self.awake = awake or Awake()
        self._log: Any = None

    def say(self, line: str) -> None:
        if self._log is not None:
            self._log.write(f"{time.strftime('%H:%M:%S')} {line}\n".encode())
            self._log.flush()

    def read(self) -> dict[str, Any]:
        found = record.load(self.job)
        if found is None:
            raise record.Refused(f"no scheduled job {self.job}")
        return found

    def run(self) -> str:
        """Drive the job as far as this process takes it: the state it ended in, or ``handover`` when a successor should wait."""
        with (record.folder(self.job) / "log").open("ab") as self._log:
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
                if self.world.selection() != born:
                    return "handover"
                self._checked("")
                self.world.sleep(min(POLL_S, early))
                continue
            self.awake.hold("waiting")
            if not (why := self.device.take(alone=any(step["kind"] == "bench" for step in job["steps"]))):
                return self._begin()
            if why != job.get("why"):
                self.say(f"waiting: {why}")
            self._checked(why)
            self.world.sleep(POLL_S)

    def _checked(self, why: str) -> None:
        record.update(self.job, lambda job: job.update(checked=self.world.clock(), why=why))

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

    def _begin(self) -> str:
        def start(job: dict[str, Any]) -> None:
            if not job.get("cancel"):
                job.update(state="running", why="", started=self.world.clock(), checked=self.world.clock())

        job = record.update(self.job, start)
        if job["state"] != "running":
            self.device.give_back()
            return self._end("cancelled", f"cancelled by {job['cancel']['by']}")
        caller.record("schedule.run", job=self.job, scheduled_by=job["by"].get("who", ""), agent=job["by"].get("agent", ""))
        self.say("the device is free: starting")
        return "running"

    # -- running ---------------------------------------------------------------------------

    def _stop(self) -> str:
        job = self.read()
        if job.get("cancel"):
            return f"cancelled by {job['cancel']['by']}"
        if self.world.clock() - job["started"] > job["max_hours"] * 3600:
            return f"the job ran past its {job['max_hours']:g} hours"
        return ""

    def _note(self, step: dict[str, Any], state: str, why: str = "", seconds: float = 0.0) -> None:
        result = {"state": state, "why": why, "seconds": round(seconds, 1)}
        record.update(self.job, lambda job: job["results"].__setitem__(str(step["n"]), result))
        if state != "running":
            self.say(f"step {step['n']} ({step['words']}): {state}" + (f": {why}" if why else ""))

    def _child(self, pid: int) -> None:
        held = {"pid": pid, "started": started_at(pid)} if pid else None
        record.update(self.job, lambda job: job.update(child=held))

    def _device_again(self) -> str:
        """Hold the device for the next bench step, waiting for it should it have been lost; why it could not be held."""
        began = self.world.clock()
        while why := self.device.take(alone=True):
            if stopped := self._stop():
                return stopped
            if self.world.clock() - began > STEP_WAIT_S:
                return f"the device was not free again within {STEP_WAIT_S / 3600:g} hours: {why}"
            record.update(self.job, lambda job, why=why: job.update(why=f"waiting between steps: {why}"))
            self.world.sleep(POLL_S)
        record.update(self.job, lambda job: job.update(why=""))
        return ""

    def _run_steps(self) -> str:
        job = self.read()
        run = execute.Run(job, self._log, self._stop, self._child, self.world.look_s)
        self.awake.hold("running")
        failed_smoke, bad, step, ran = 0, False, None, False
        try:
            for step in job["steps"]:
                if self._stop():
                    break
                if step["role"] == "then" and failed_smoke:
                    self._note(step, "skipped", f"its smoke (step {failed_smoke}) failed")
                    failed_smoke = 0
                    continue
                failed_smoke = 0
                if step["kind"] == "remove-model" and bad:
                    self._note(step, "skipped", "a model is removed only when every step before it passed")
                    continue
                self._note(step, "running")
                began = self.world.clock()
                state, why = self._one(run, step)
                self._note(step, state, why, self.world.clock() - began)
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
        failed = [f"step {n} {one['state']}: {one['why']}" for n, one in found["results"].items() if one["state"] != "ok"]
        return self._end("failed" if bad or stopped else "done", stopped or "; ".join(failed)[:600])

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
        def end(job: dict[str, Any]) -> None:
            job.update(state=state, why=why, ended=self.world.clock(), child=None)
            for one in job["results"].values():
                if one["state"] == "running":
                    one.update(state="failed", why=one["why"] or why)

        self.device.give_back()
        job = record.update(self.job, end)
        caller.record("schedule.end", job=self.job, state=state, why=why, scheduled_by=job["by"].get("who", ""))
        self.say(f"job {state}" + (f": {why}" if why else ""))
        self._notice(job)
        return state

    def _notice(self, job: dict[str, Any]) -> None:
        if (job.get("notice") or {}).get("sent"):
            return
        error = self.world.tell(job)
        now = self.world.clock()
        record.update(self.job, lambda kept: kept.update(
            notice={"sent": 0.0 if error else now, "tried": now, "error": error}))
        if error:
            self.say(f"the notice was not sent and will be tried again: {error}")


def _stopped(signum: int, _frame: Any) -> None:
    raise SystemExit(128 + signum)


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
    try:
        with only_one(record.folder(job) / record.RUNNER_LOCK, wait=False, note="scheduled job", announce=say):
            outcome = Runner(job).run()
    except Busy:
        say(f"another process holds {job}")
        return 0
    if outcome == "handover":
        spawn.start(job)
    return 0

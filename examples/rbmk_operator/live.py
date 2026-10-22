"""The live runner: the plant on its own clock, each decision an async request on its own cadence.

The deterministic core (`loop.Runner`, `plant`, `reactor`) is plain synchronous code in simulated time; this module
only schedules it. The plant task sleeps to its schedule (the speed factor scales the sleep, never the physics step)
and never waits for the model. A decision is a task that awaits the blocking model call on a single worker thread, so
compute stays serialized under the lease; its answer lands in the plant at the simulated time that matches its
real latency. A call that does not answer within its timeout halts the run loudly; the thread cannot be cancelled,
so the request is marked dead and a late answer is discarded and logged, never applied.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from typing import Any

from .loop import Result, Runner
from .operator_model import ModelStopped


class Compute:
    """The one worker thread for blocking model calls, and the state of any call a halted run left behind."""

    def __init__(self) -> None:
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="decider")
        self.lock = threading.Lock()
        self.dead = 0

    def state(self) -> dict[str, Any]:
        """For a header: whether a dead call still holds the worker."""
        return {"dead_calls_running": self.dead, "ready": self.dead == 0}

    def mark_dead(self) -> None:
        """A call was abandoned and is still running on the worker."""
        with self.lock:
            self.dead += 1

    def finished_dead(self) -> None:
        """An abandoned call returned."""
        with self.lock:
            self.dead -= 1


COMPUTE = Compute()


class LiveRunner:
    """Runs a scenario with the plant, the decider and the speed knob on separate schedules."""

    def __init__(self, runner: Runner, *, speed: float = 60.0, timeout: float = 120.0, compute: Compute = COMPUTE) -> None:
        self.runner, self.timeout, self.compute = runner, timeout, compute
        self.speed = 1.0
        self.set_speed(speed)
        self.failure: ModelStopped | None = None
        self.late: list[dict[str, Any]] = []
        self.halted = False
        self._wake = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        self._dead_gens: set[int] = set()

    @property
    def result(self) -> Result:
        """The rows so far."""
        return self.runner.res

    def set_speed(self, speed: float) -> None:
        """Change simulated seconds per wall second: it scales the plant's sleep and the latency conversion only."""
        if not speed > 0:
            raise ValueError("speed must be positive")
        self.speed = speed

    def stop(self) -> None:
        """Halt the run (a stop or a new scenario): an in-flight request is marked dead."""
        self.halted = True
        self._dead_gens.update(range(self.runner.requests))
        self._wake.set()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except TimeoutError:
            return

    def _late(self, n: int, started: float, cause: str) -> None:
        self.late.append({"request": n, "note": f"late answer discarded after halt ({cause})",
                          "latency_ms": round((time.monotonic() - started) * 1000.0, 1)})
        self.result.late = self.late

    def _abandon(self, n: int, started: float, future: concurrent.futures.Future[Any], cause: str) -> None:
        """A blocking call cannot be cancelled: mark it dead and log its answer, if it ever comes, as discarded."""
        self._dead_gens.add(n)
        self.compute.mark_dead()

        def done(fut: concurrent.futures.Future[Any]) -> None:
            self.compute.finished_dead()
            self._late(n, started, cause if fut.exception() is None else f"{cause}, then failed")
        future.add_done_callback(done)

    async def _ask(self, n: int, snap: Any, snapshot: dict[str, Any], fork: Any) -> None:
        started = time.monotonic()
        future = self.compute.executor.submit(self.runner.operator.propose, n, snap)
        try:
            async with asyncio.timeout(self.timeout):
                prop = await asyncio.wrap_future(future)
        except TimeoutError:
            self._abandon(n, started, future, "timeout")
            self.failure = ModelStopped(f"the model call for request {n} did not answer within {self.timeout:.0f} s; "
                                        "the run halts and a late answer will be discarded")
            self._wake.set()
            return
        except asyncio.CancelledError:
            self._abandon(n, started, future, "run ended")
            raise
        except ModelStopped as exc:
            self.failure = exc
            self._wake.set()
            return
        if n in self._dead_gens:
            self._late(n, started, "run stopped")
            return
        self.runner.speed = self.speed
        self.runner.receive(n, snapshot, fork, prop)

    async def run(self) -> Result:
        """Run until the scenario ends, the run is stopped, or the model fails; a failure raises `ModelStopped`."""
        if self.compute.dead:
            raise ModelStopped("compute is still held by a call from a halted run; wait for it to return "
                               f"({self.compute.state()})")
        r = self.runner
        try:
            while not r.plant.done() and not self.halted:
                if r.begin_tick():
                    if r.inflight or r.waiting:
                        r.busy()
                    else:
                        n, snap, snapshot, fork = r.request()
                        task = asyncio.create_task(self._ask(n, snap, snapshot, fork))
                        self._tasks.add(task)
                        task.add_done_callback(self._tasks.discard)
                await self._sleep(r.next_wall_s(self.speed))
                if self.failure:
                    raise self.failure
                self._wake.clear()
                r.finish_tick()
            return r.finish_run()
        finally:
            self.halted = True
            pending = [t for t in self._tasks if not t.done()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

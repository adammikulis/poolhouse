"""Run every gate step, with independent lanes executing side by side."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Step:
    """One gate step: its name and what it runs."""

    name: str
    run: Callable[[], int]


@dataclass
class Outcome:
    """How a step ended: its status and how long it took."""

    name: str
    status: int = 0
    seconds: float = 0.0


@dataclass
class Gate:
    """The steps of a gate, grouped into independent lanes."""

    lock: threading.Lock = field(default_factory=threading.Lock)

    def execute(self, step: Step) -> Outcome:
        """Run one step and report its actual status and elapsed time."""
        began = time.monotonic()
        status = step.run()
        return Outcome(step.name, status, time.monotonic() - began)

    def say(self, text: str) -> None:
        """Print one block without another thread's lines cutting into it."""
        with self.lock:
            print(text, flush=True)

    def chain(self, steps: Sequence[Step]) -> list[Outcome]:
        """Run steps one after another, as one lane."""
        out = []
        for step in steps:
            self.say(f"gate: {step.name}")
            out.append(self.execute(step))
            self.say(f"gate: {step.name} {'ok' if not out[-1].status else 'FAILED'} in {out[-1].seconds:.0f} s")
        return out

    def run(self, lanes: Sequence[Sequence[Step]]) -> list[Outcome]:
        """Run each lane at the same time, the first lane on this thread, and return outcomes in lane order.

        A lane's steps stay in order. The first lane holds the pytest step, which installs signal handlers
        and so must be on the main thread; the others are the lighter scripts.
        """
        if len(lanes) < 2:
            return [o for lane in lanes for o in self.chain(lane)]
        with ThreadPoolExecutor(max_workers=len(lanes) - 1) as pool:
            side = [pool.submit(self.chain, lane) for lane in lanes[1:]]
            first = self.chain(lanes[0])
            return [*first, *(o for future in side for o in future.result())]

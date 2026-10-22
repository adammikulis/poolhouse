"""Varied xenon-poisoned starts drawn from documented ranges around the state before the 1986 test.

`draw(seed, k)` is the k-th start of a seeded batch. The ranges are the experiment's own and are stated in the README.
"""

from __future__ import annotations

import random

from .scenarios import PROTECTIONS_OFF, XENON_BOUND_S, Event, Scenario

RANGES = {
    # Power: about 30 MW thermal at 00:28 and held near 200 MW from about 01:00 on 26 April 1986 (World Nuclear Association, "Chernobyl
    # accident appendix 1", a secondary source for INSAG-7). Relative to 3200 MW.
    "power": (30 / 3200, 200 / 3200),
    # Margin: the post-accident calculation gave 8 rod-equivalents at 01:22:30 (same source); accounts of 6 to 8 are recalled, unverified.
    "margin": (6.0, 8.0),
    # Iodine index: the toy's own range; xenon follows from power and margin. Unverified.
    "iodine": (0.9, 1.4),
}
ORDER_TICK = 4        # seconds into the run at which the supervisor's order arrives: the toy's own


def draw(seed: int, k: int) -> Scenario:
    """The k-th poisoned start of the batch with this seed."""
    rng = random.Random(f"{seed}:{k}")  # noqa: S311
    power = round(rng.uniform(*RANGES["power"]), 4)
    margin = round(rng.uniform(*RANGES["margin"]), 1)
    return Scenario(
        f"start_{seed}_{k}", "poisoned", ticks=100_000, max_s=XENON_BOUND_S, seed=rng.randrange(1 << 30), power=power, setpoint=0.22,
        iodine=round(rng.uniform(*RANGES["iodine"]), 2), orm=margin, tilt=0.0,
        pattern=rng.randrange(1 << 30), throttle=0.9, pumps=8, ar_on=False, has_test=True,
        fast_from=0, defeated=PROTECTIONS_OFF, speed=1.0, events=(Event(ORDER_TICK, "order", 0), Event(ORDER_TICK, "setpoint", 0.22)))

"""Varied xenon-poisoned starts drawn from documented ranges around the state before the 1986 test.

`draw(seed, k)` is the k-th start of a seeded batch. The ranges are the experiment's own and are stated in the README.
"""

from __future__ import annotations

import random

from .scenarios import PROTECTIONS_OFF, Event, Scenario

RANGES = {
    "power": (0.03, 0.10),        # relative power: 100 to 320 MW thermal
    "margin": (5.0, 11.0),        # rod-equivalents inserted
    "iodine": (0.9, 1.4),         # iodine index; xenon follows from power and margin
    "tilt": (-0.3, 0.3),          # radial flux tilt
    "throttle": (0.85, 0.95),
}
HORIZON_S = 3 * 3600.0
ORDER_TICK = 4


def draw(seed: int, k: int) -> Scenario:
    """The k-th poisoned start of the batch with this seed."""
    rng = random.Random(f"{seed}:{k}")  # noqa: S311
    power = round(rng.uniform(*RANGES["power"]), 4)
    margin = round(rng.uniform(*RANGES["margin"]), 1)
    return Scenario(
        f"start_{seed}_{k}", "poisoned", ticks=100_000, max_s=HORIZON_S, seed=rng.randrange(1 << 30), power=power, setpoint=0.22,
        iodine=round(rng.uniform(*RANGES["iodine"]), 2), orm=margin, tilt=round(rng.uniform(*RANGES["tilt"]), 2),
        pattern=rng.randrange(1 << 30), throttle=round(rng.uniform(*RANGES["throttle"]), 2), pumps=8, ar_on=False, has_test=True,
        fast_from=0, defeated=PROTECTIONS_OFF, speed=1.0, events=(Event(ORDER_TICK, "order", 0), Event(ORDER_TICK, "setpoint", 0.22)))

"""The 95% intervals the bench reports: a seeded bootstrap over rows, the same over the differences of two builds'
shared rows, and Student's t over a handful of repeats."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

__all__ = ["BOOTSTRAP", "BOOTSTRAP_SEED", "T95", "difference", "resample", "t_interval"]

BOOTSTRAP = 1000
BOOTSTRAP_SEED = 20260902


def _at(sorted_values: Sequence[float], share: float) -> float:
    """The value at ``share`` of the way through values already sorted."""
    last = len(sorted_values) - 1
    return sorted_values[min(last, max(0, round(share * last)))]


def resample(per: Sequence[Sequence[float]], *, draws: int = BOOTSTRAP,
             seed: int = BOOTSTRAP_SEED) -> list[tuple[float, float]]:
    """The 95% bootstrap interval of the mean of each column of ``per``: the rows are resampled
    with replacement ``draws`` times, one resample serving every column, and the 2.5th and
    97.5th percentiles of the means are kept. Empty for fewer than two rows."""
    n = len(per)
    if n < 2:
        return []
    rand = random.Random(seed)  # noqa: S311 - a sample, not a secret
    columns = range(len(per[0]))
    means: list[list[float]] = [[] for _ in columns]
    pool = range(n)
    for _ in range(draws):
        picked = [per[i] for i in rand.choices(pool, k=n)]
        for axis in columns:
            means[axis].append(sum(p[axis] for p in picked) / n)
    out = []
    for drawn in means:
        drawn.sort()
        out.append((_at(drawn, 0.025), _at(drawn, 0.975)))
    return out


def difference(first: Sequence[float], second: Sequence[float]) -> tuple[float, float, float] | None:
    """``(mean, lo, hi)`` of ``first - second`` taken row by row over the same items, with the
    95% bootstrap interval of that mean. None for fewer than two rows. The two are told apart
    when the interval leaves out zero."""
    if len(first) != len(second):
        raise ValueError(f"a difference needs the same items: {len(first)} against {len(second)}")
    differences = [(float(a) - float(b),) for a, b in zip(first, second, strict=True)]
    got = resample(differences)
    if not got:
        return None
    return sum(d[0] for d in differences) / len(differences), got[0][0], got[0][1]


#: Student's t at 97.5% by degrees of freedom, for the interval of a handful of repeats.
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
       10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 19: 2.093, 29: 2.045}


def t_interval(values: Sequence[float]) -> tuple[float, float, float] | None:
    """``(mean, lo, hi)``, the 95% t interval of the mean of repeated measurements. None for
    fewer than two. Between the tabled degrees of freedom the next lower one is used, which
    errs wide."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    spread = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    half = T95[max(k for k in T95 if k <= n - 1)] * spread / math.sqrt(n)
    return mean, mean - half, mean + half

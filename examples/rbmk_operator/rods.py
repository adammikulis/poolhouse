"""The 211 control rods of the toy core: type, grid position, worth by location, drive faults."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

STROKE_S = 20.0            # full stroke, about 18 to 21 s on the real drives
KINDS = {"MR": "manual control", "AR": "automatic regulation", "LAR": "local automatic regulation",
         "USP": "shortened absorber", "EP": "emergency protection"}
COUNTS = (("EP", 24), ("AR", 12), ("LAR", 12), ("USP", 24))   # the rest are manual rods: 139
GRID = 17


def worth(r: float) -> float:
    """Fraction of a rod's worth inserted at insertion fraction ``r`` (S-curve)."""
    return r - math.sin(2 * math.pi * r) / (2 * math.pi)


@dataclass
class Rod:
    """One control rod."""

    id: str
    kind: str
    x: float
    y: float
    w: float
    r: float = 0.0
    target: float = 0.0
    fault: str = ""        # "" | stuck | slow
    moved_by: str = ""

    def speed(self) -> float:
        """Insertion fraction per second."""
        return 0.0 if self.fault == "stuck" else (0.2 if self.fault == "slow" else 1.0) / STROKE_S


class Rods:
    """The whole rod array and its operational reactivity margin in rod-equivalents."""

    def __init__(self, tilt: float = 0.0, seed: int = 1986) -> None:
        cells = sorted(((i, j) for i in range(GRID) for j in range(GRID)),
                       key=lambda c: (math.hypot(c[0] - 8, c[1] - 8), c))[:211]
        order = list(range(211))
        random.Random(seed).shuffle(order)  # noqa: S311
        kinds = [k for k, n in COUNTS for _ in range(n)]
        kinds += ["MR"] * (211 - len(kinds))
        radius = max(math.hypot(i - 8, j - 8) for i, j in cells)
        raw = []
        for slot, (i, j) in zip(order, cells, strict=True):
            x, y = (i - 8) / radius, (j - 8) / radius
            flux = max(math.cos(1.1 * math.hypot(x, y) * math.pi / 2), 0.05) * (1.0 + tilt * x)
            raw.append((slot, i, j, x, y, flux ** 2))
        mean = sum(r[5] for r in raw) / len(raw)
        self.rods = [Rod(f"R-{i:02d}-{j:02d}", kinds[slot], round(x, 4), round(y, 4), round(w / mean, 4))
                     for slot, i, j, x, y, w in raw]
        self.tilt = tilt
        self.by_id = {r.id: r for r in self.rods}
        self._orm: float | None = None

    def orm(self) -> float:
        """Rod-equivalents inserted: each rod's worth fraction times its location weight."""
        if self._orm is None:
            self._orm = sum(r.w * worth(r.r) for r in self.rods)
        return self._orm

    def group(self, kind: str) -> list[Rod]:
        """The rods of one type."""
        return [r for r in self.rods if r.kind == kind]

    def command(self, rods: list[Rod], target: float, who: str) -> None:
        """Set the target position of ``rods``."""
        for r in rods:
            r.target, r.moved_by = min(max(target, 0.0), 1.0), who

    def step(self, dt: float) -> None:
        """Move every rod toward its target at its drive speed."""
        moved = False
        for r in self.rods:
            if r.r != r.target:
                d = r.speed() * dt
                r.r = min(r.r + d, r.target) if r.r < r.target else max(r.r - d, r.target)
                moved = True
        if moved:
            self._orm = None

    def scram(self) -> None:
        """AZ-5: every rod to full insertion at its own drive speed (a stuck rod stays)."""
        for r in self.rods:
            r.target, r.moved_by = 1.0, "AZ-5"

    def settle_to(self, orm: float) -> None:
        """Place rods for a given margin: regulators mid-stroke, then manual rods in by worth order."""
        for r in self.rods:
            r.r = r.target = 0.0
        regs = self.group("AR") + self.group("LAR")
        lo, hi = 0.0, 0.45
        for _ in range(30):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if sum(r.w * worth(mid) for r in regs) < orm / 2 else (lo, mid)
        for r in regs:
            r.r = r.target = lo
        left = orm - sum(r.w * worth(r.r) for r in self.rods)
        for r in sorted(self.group("MR"), key=lambda q: q.w):
            if left <= 0:
                break
            take = min(left / r.w, 0.5)
            lo, hi = 0.0, 1.0
            for _ in range(30):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if worth(mid) < take else (lo, mid)
            r.r = r.target = (lo + hi) / 2
            left -= r.w * worth(r.r)
        self._orm = None

    def table(self) -> list[dict[str, object]]:
        """Every rod as a JSON row for the interface."""
        return [{"id": r.id, "kind": r.kind, "x": r.x, "y": r.y, "w": r.w, "r": round(r.r, 4),
                 "target": round(r.target, 4), "fault": r.fault, "by": r.moved_by} for r in self.rods]

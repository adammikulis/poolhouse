"""Where a pytest run's wall time went: collection, session setup, tests and teardown.

Loaded into every ``scripts/test`` run as a pytest plugin. It only reads the reports pytest
already makes, so it adds no work to the run; the controller writes the phases to the file
named by ``PHASES_ENV`` and ``scripts/test`` prints them after the run.

* collection: from the plugin loading to the first test starting (worker start and collection).
* setup, tests, teardown: all reports from the worker with the longest total reported work.
  Fixture work belongs in setup/teardown, including fixtures used after the first test.
* queued: that same worker's reported CPU permit waits.
* residual: wall time outside collection and that worker's reports; this includes scheduling,
  overlap and unreported work, and is not a measurement of pure startup.

Fixed overhead still includes fixture work; its warning thresholds are unchanged.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

PHASES_ENV = "POOLHOUSE_PHASES_FILE"
WAIT_PROPERTY = "admission_wait"
OVERHEAD_SECONDS = 5.0
OVERHEAD_SHARE = 0.25
SHARE_FLOOR_SECONDS = 3.0
"""A share of wall time only counts once the overhead is also this large: a one-test run is all overhead."""


@dataclass(frozen=True)
class Phases:
    """One run's seconds by phase; ``wall`` is the plugin's own span, queue wait excluded."""

    wall: float
    collection: float
    setup: float
    tests: float
    teardown: float
    queued: float = 0.0
    worker: str = "main"
    workers: dict = field(default_factory=dict)
    complete: bool = True

    @property
    def residual(self) -> float:
        """Unassigned wall seconds; a negative value is inconsistent evidence."""
        return self.wall - self.collection - self.setup - self.tests - self.teardown

    @property
    def fixed(self) -> float:
        """Everything except the test calls and the wait for CPU permits."""
        return max(0.0, self.wall - self.tests - self.queued)


def record(phases: Phases) -> dict:
    """The figures a job records: the fixed seconds and whether they are over the limit."""
    return {"fixed_s": round(phases.fixed, 1), "over_limit": too_slow(phases)}


def line(phases: Phases) -> str:
    """The one-line breakdown, with a warning when the fixed overhead is large."""
    text = (f"test: phases collection {phases.collection:.1f} s, worker setup {phases.setup:.1f} s, "
            f"tests {phases.tests:.1f} s, teardown {phases.teardown:.1f} s, "
            f"queued for CPU {phases.queued:.1f} s (fixed {phases.fixed:.1f} s of {phases.wall:.1f} s)"
            f"\ntest: worker {phases.worker}; residual scheduling/unreported {phases.residual:.1f} s; fixed includes fixtures")
    if phases.workers:
        text += "\ntest: worker reports " + json.dumps(phases.workers, sort_keys=True)
    if not phases.complete or phases.residual < -0.01 or any(
            not math.isfinite(value) or value < 0 for value in
            (phases.wall, phases.collection, phases.setup, phases.tests, phases.teardown, phases.queued)):
        text += "\ntest: WARNING phase accounting is incomplete or inconsistent; inspect raw worker reports"
    if too_slow(phases):
        text += (f"\ntest: WARNING fixed overhead {phases.fixed:.1f} s is over {OVERHEAD_SECONDS:g} s "
                 f"or {OVERHEAD_SHARE:.0%} of wall time: find the fixture or import that is slow")
    return text


def too_slow(phases: Phases) -> bool:
    """Whether the fixed overhead is past the absolute or the relative limit."""
    return phases.fixed > OVERHEAD_SECONDS or (
        phases.fixed > SHARE_FLOOR_SECONDS and phases.fixed > OVERHEAD_SHARE * phases.wall)


def read(path: Path) -> Phases | None:
    """The phases a run wrote, or None when it wrote none."""
    try:
        return Phases(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


class Recorder:
    """Collects phase times from the reports the controller receives."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.began = time.time()
        self.first_start = 0.0
        self.workers: dict[str, dict] = {}
        self.complete = True

    def pytest_runtest_logreport(self, report) -> None:
        """Note one report's duration under its phase and worker."""
        node = getattr(report, "node", None)
        worker = getattr(getattr(node, "gateway", None), "id", None) if node is not None else "main"
        if not isinstance(worker, str) or not worker:
            self.complete = False
            worker = "unknown"
        if not self.first_start:
            self.first_start = getattr(report, "start", time.time())
        if report.when not in ("setup", "call", "teardown"):
            return
        row = self.workers.setdefault(worker, {"setup": 0.0, "call": 0.0, "teardown": 0.0,
                                               "queued": 0.0, "phases": []})
        duration = float(report.duration)
        if not math.isfinite(duration) or duration < 0:
            self.complete = False
            row.setdefault("invalid", []).append({report.when: repr(duration)})
        else:
            row[report.when] += duration
        if report.when not in row["phases"]:
            row["phases"].append(report.when)
        if report.when == "setup":
            for key, value in getattr(report, "user_properties", ()):
                if key != WAIT_PROPERTY:
                    continue
                waited = float(value)
                if not math.isfinite(waited) or waited < 0:
                    self.complete = False
                    row.setdefault("invalid", []).append({"queued": repr(waited)})
                else:
                    row["queued"] += waited

    def pytest_sessionfinish(self, session) -> None:
        """Write the phases to the file ``scripts/test`` reads."""
        worker = max(self.workers, key=lambda name: sum(self.workers[name][phase]
                     for phase in ("setup", "call", "teardown")), default="main")
        row = self.workers.get(worker, {})
        collection = max(0.0, (self.first_start or time.time()) - self.began)
        complete = self.complete and bool(self.workers) and all(
            set(value["phases"]) == {"setup", "call", "teardown"} for value in self.workers.values())
        phases = Phases(time.time() - self.began, collection, row.get("setup", 0.0),
                        row.get("call", 0.0), row.get("teardown", 0.0), row.get("queued", 0.0),
                        worker, self.workers, complete)
        Path(self.path).write_text(json.dumps(asdict(phases)), encoding="utf-8")


def pytest_configure(config) -> None:
    """Start recording on the controller, and on nothing else."""
    path = os.environ.get(PHASES_ENV)
    if path and not hasattr(config, "workerinput"):
        config.pluginmanager.register(Recorder(path), "poolhouse-phases")

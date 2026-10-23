"""A job: one run, with the append-only list of events a browser follows."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


class Busy(RuntimeError):
    """The model is already working for another job."""


class Cancelled(RuntimeError):
    """The person stopped the job."""


@dataclass
class Job:
    """What a browser follows: events are only ever appended."""

    id: str
    kind: str                         # run
    scenario: str
    controller: str
    label: str = ""
    status: str = "running"           # running | done | error | stopped
    error: str = ""
    model: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    docs: list[dict[str, Any]] = field(default_factory=list)
    runner: Any = None
    scn: Any = None
    backend: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    cond: threading.Condition = field(default_factory=threading.Condition)
    cancel: threading.Event = field(default_factory=threading.Event)

    def emit(self, **event: Any) -> None:
        """Append one event and wake the streams."""
        with self.cond:
            event["seq"] = len(self.events)
            self.events.append(event)
            self.cond.notify_all()

    def finish(self, status: str, error: str = "") -> None:
        """Set the final status and tell the streams."""
        self.status, self.error = status, error
        self.emit(type="status", status=status, error=error)

    def since(self, after: int, wait_s: float) -> list[dict[str, Any]]:
        """The events from index ``after`` on, waiting up to ``wait_s`` for one."""
        with self.cond:
            if len(self.events) <= after and self.status == "running":
                self.cond.wait(wait_s)
            return self.events[after:]

    def public(self) -> dict[str, Any]:
        """The job as the list of jobs shows it."""
        return {"id": self.id, "kind": self.kind, "scenario": self.scenario, "controller": self.controller,
                "label": self.label, "status": self.status, "error": self.error, "model": self.model,
                "events": len(self.events), "started": self.started}

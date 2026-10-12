"""Stand-ins for what `llama-cpp ensure` reads and does at the process boundary: the status check, the
update, the broker's lease table, the measuring lock and the running queue."""

from __future__ import annotations

from typing import Any

from poolhouse.serve import llamacpp_ensure
from poolhouse.serve.llamacpp_update import Outcome

__all__ = ["MASTER", "FakeEnsure", "status_answer"]

MASTER = "a" * 40


def status_answer(*, master: str = MASTER, tag: str = "b11359", **over: Any) -> dict[str, Any]:
    """What `llama-cpp status` gathers for a managed build 11300 that is behind master."""
    upstream = {"tag": tag, "stable": "v0.5.0", "master": master} if master or tag else {}
    base = {"binary": "/x/llama-server", "origin": "managed build (track)", "version": "", "build": 11300,
            "commit": "c" * 40, "pinned": "", "problem": "", "newer": "yes", "upstream_note": ""}
    return {**base, "upstream": upstream, **over}


class FakeEnsure:
    """An `Env` whose status, update, lease table, measuring lock and queue are set by the test and
    whose calls are recorded in ``checks``, ``updates`` and ``newest`` (the earlier answer each check got)."""

    def __init__(self, got: dict | None = None, outcome: Outcome | Exception | None = None,
                 leases: dict | None = None, age: float | None = None, **busy: dict | None) -> None:
        self.got = got or status_answer()
        self.outcome = outcome or Outcome("built", "b11359-aaaaaaaaa", "version: 11359")
        self.table = leases or {"servers": [], "queue": []}
        self.lock, self.running, self.age = busy.get("measuring"), busy.get("queue"), age
        self.checks, self.updates, self.newest = 0, [], []

    def env(self) -> llamacpp_ensure.Env:
        return llamacpp_ensure.Env(status=self._status, update=self._update, leases=lambda: self.table,
                                   measuring=lambda: self.lock, queue=lambda: self.running,
                                   now=lambda: 1_000_000.0, age_hours=lambda: self.age)

    def _status(self, upstream: Any, pipeline: Any, **more: Any) -> dict[str, Any]:
        self.checks += 1
        self.newest.append(more.get("newest"))
        return self.got

    def _update(self, ref: str, env: Any, **more: Any) -> Outcome:
        self.updates.append(ref)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

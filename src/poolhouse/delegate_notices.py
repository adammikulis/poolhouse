"""What the lead is told while a delegate runs, without polling: that an attempt was broken off or a review failed, that a restart began, that the
preflight found the box wanting, that a test run in the box failed for what looks like setup, that a request rewrote history the model server had
read or a step read it again, and that nothing has happened for a long while. How a run ended is told by the runner's own last message
(`delegate_run`).

A notice is a message to the lead on the board, from the delegate's session, and nothing waits for an answer. It is read from the run's record, so
it says what the record says; each is also an event of the record, and `poolhouse-delegate wait` returns when one is new.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from poolhouse import delegate_record, delegate_recorder, delegate_store as store

__all__ = ["MOST", "QUIET_S", "Teller"]

QUIET_S = 600.0
"""Seconds with no event after which the lead is told; the next telling of the same quiet is after twice as long, and so on."""
LOOK_S = 1.0
MOST = 30
"""The notices of one run; the last says that no more are sent."""


class Teller:
    """Reads the record of delegate ``name`` as it grows and gives ``post(subject, text)`` each thing its lead is to be told. `start` looks from a
    thread of its own until `close`, which looks a last time."""

    def __init__(self, name: str, post: Callable[[str, str], object]) -> None:
        self.name, self.post = name, post
        self.sent = 0
        self._after = 0
        self._told: set[str] = set()
        self._broke = ""
        self._quiet_for, self._quiet_since = 0.0, 0.0
        self._lock = threading.Lock()
        self._over = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._over.set()
        if self._thread.is_alive():
            self._thread.join(timeout=30)
        self.look(ending=True)

    def _watch(self) -> None:
        while not self._over.wait(LOOK_S):
            self.look()

    def look(self, *, ending: bool = False) -> None:
        """Tell what the record has gained since the last look and, unless the run is ``ending``, how long it has been quiet."""
        with self._lock:
            more = True
            while more:
                events, self._after, more = delegate_record.read(self.name, self._after)
                for event in events:
                    self._heard(event)
            if not ending:
                self._quiet(time.time())

    def _heard(self, event: Mapping[str, Any]) -> None:
        kind = event.get("kind")
        named = f"attempt {event.get('attempt')} of the {event.get('phase')} pass" + (f" {event['round']}" if event.get("round") else "")
        if kind == "attempt_end":
            broke, why = str(event.get("interrupted") or ""), str(event.get("why") or "")
            self._broke = broke
            # an error that ends the run is the runner's last message; one the run goes on after is told here
            if broke or (why and event.get("phase") == "review"):
                after = delegate_record.span(float(event.get("seconds") or 0))
                self._tell("attempt ended in error", f"{named} ended in error after {after}, {event.get('turns')} turn(s): {broke or why}")
        elif kind == "attempt" and event.get("attempt"):
            why = f"; the one before was broken off: {self._broke}" if self._broke else ""
            self._tell("restart", f"restarted: {named} has begun{why}")
        elif kind == "preflight" and not event.get("ok"):
            found = str(event.get("lacks") or event.get("untested"))
            self._tell("preflight failed", f"the preflight in the box failed: {found}", once=f"preflight {event.get('untested')}")
        elif kind == "rewrite":   # the first of each is told; a run that goes on doing it would fill the lead's inbox, and its record holds all
            self._tell("history rewritten", f"{event.get('why')}. Later ones of this run are in its record only.", once="rewrite")
        elif kind == "reread":
            self._tell("prompt read again", f"step {event.get('turn')} read {int(event.get('input_tokens') or 0):,} tokens of its prompt again; "
                       f"only {int(event.get('cache_read_tokens') or 0):,} came from the server's cache. Later ones of this run are in its record "
                       "only.", once="reread")
        elif kind == "test" and event.get("setup"):
            self._tell("test setup", f"a test run in the box failed and it looks like setup, not the work: command {event.get('command')!r}, "
                       f"{delegate_record.counts(event)}: {event['setup']}", once=f"setup {event['setup']}")

    def _quiet(self, now: float) -> None:
        status = store.status(self.name)
        heard = float(status.get("heard") or 0)
        if not heard or status.get("state") not in delegate_record.LIVE:
            return
        if heard != self._quiet_since:
            self._quiet_since, self._quiet_for = heard, QUIET_S
        if now - heard >= self._quiet_for:
            self._quiet_for *= 2
            self._tell("quiet", f"nothing has happened for {delegate_record.span(now - heard)}: "
                       f"{delegate_record.doing(str(status['state']), {**status, 'heard': 0}, now)}")

    def _tell(self, about: str, text: str, *, once: str = "") -> None:
        if self.sent >= MOST or (once and once in self._told):
            return
        self._told.add(once)
        self.sent += 1
        recorder = delegate_recorder.of(self.name)
        said = recorder.clean(text, 900)
        if self.sent == MOST:
            said += " No further notice is sent for this run."
        said += f"\nfollow it: poolhouse-delegate follow {self.name}"
        try:
            self.post(about, said)
            failed = ""
        except (OSError, RuntimeError, ValueError) as why:
            failed = str(why)
        recorder.add("notice", about=about, why=said, delivered=not failed, detail=failed)
        store.note(self.name, notices=int(store.status(self.name).get("notices") or 0) + 1, told=f"{about}: {said}")

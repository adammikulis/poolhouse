"""``GET /phone/v1/stream``: each new event of any session as it happens, and which lists changed (docs/phone-api.md, "Stream"). Live
only: a phone that reconnects reads each session's events from its last cursor. A stream ends after ten minutes, and at once when its
phone is removed."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from poolhouse import delegate_record
from poolhouse.phone_mailbox import Refused

__all__ = ["LIFE_S", "MOST_STREAMS", "Streams"]

LIFE_S = 600.0
PING_S = 15.0
LOOK_S = 1.0
LIST_S = 3.0
MOST_STREAMS = 3
MOST_FOLLOWED = 40
"""The sessions, the most recently active first, whose events a stream carries."""


def _sent(event: str, data: object) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Streams:
    def __init__(self, computer: Any, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.computer, self.clock, self.sleep = computer, clock, sleep
        self._open: dict[str, int] = {}
        self._lock = threading.Lock()
        self.closed = threading.Event()

    def _lists(self, rows: list[dict[str, Any]]) -> dict[str, str]:
        """What each list looks like now, to say which changed."""
        runner, store = self.computer.ui.runner, self.computer.store
        return {
            "sessions": _digest([(r["id"], r["state"], r["mode"], r["waiting"]) for r in rows]),
            "inbox": _digest([q["question"] for q in store.open_questions()]),
            "jobs": _digest([(j.get("id"), j.get("state")) for j in runner.snapshot()] if runner is not None else []),
            "delegates": _digest([(n, delegate_record.state_of(n)[0]) for n in delegate_record.names()[-50:]]),
            "models": _digest([each.public() for each in self.computer.ui.serving.live()] if self.computer.ui.serving is not None else []),
            "devices": _digest([(p.id, p.pool_use) for p in store.phones().values()]),
        }

    def open(self, phone: str) -> Iterator[bytes]:
        """The stream of ``phone``; past `MOST_STREAMS` it says so and ends."""
        with self._lock:
            if self._open.get(phone, 0) >= MOST_STREAMS:
                return iter((_sent("refused", {"message": "This phone has as many streams open as it may."}),))
            self._open[phone] = self._open.get(phone, 0) + 1
        return self._follow(phone)

    def _follow(self, phone: str) -> Iterator[bytes]:
        sessions = self.computer.sessions
        try:
            began = self.clock()
            yield b": ping\n\n"
            rows = sessions.list()
            cursors = {r["id"]: sessions.events(r["id"])["next"] for r in rows[:MOST_FOLLOWED]}
            looks, pinged, listed = self._lists(rows), self.clock(), self.clock()
            seen = {r["id"]: r["active_at"] for r in rows}
            while self.clock() - began < LIFE_S and not self.closed.is_set():
                self.sleep(LOOK_S)
                if phone not in self.computer.store.phones():
                    return
                changed = []
                if self.clock() - listed >= LIST_S:
                    rows, listed = sessions.list(), self.clock()
                    now = self._lists(rows)
                    changed = [name for name, digest in now.items() if looks.get(name) != digest]
                    looks = now
                for row in rows[:MOST_FOLLOWED]:
                    yield from self._news(row, cursors, seen)
                if changed:
                    yield _sent("changed", {"what": changed})
                if self.clock() - pinged >= PING_S:
                    pinged = self.clock()
                    yield b": ping\n\n"
        finally:
            with self._lock:
                self._open[phone] -= 1

    def _news(self, row: dict[str, Any], cursors: dict[str, str], seen: dict[str, float]) -> Iterator[bytes]:
        """The new events of one session. A session that appeared after the stream opened sends what it holds."""
        ident = row["id"]
        try:
            found = self.computer.sessions.events(ident, cursors.get(ident, "t0.m0"))
        except (Refused, OSError):
            return
        cursors[ident] = found["next"]
        seen[ident] = row["active_at"]
        for event in found["events"][-100:]:
            yield _sent("session", {"session": ident, "event": event})

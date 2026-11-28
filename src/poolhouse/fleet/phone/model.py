"""A phone's own model, lent to the pool (docs/phone-api.md, section 5). Off for each phone until switched on. The phone cannot be
dialled, so it asks for work with a long poll and posts the answer back; nothing is sent to a phone that is off, and what a request or an
answer holds lives in memory only, never in a file or a log. What a phone returns is text from a model: it is passed to the asker and
nothing here runs any of it."""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from poolhouse.phone_mailbox import Store

from .errors import ApiError

__all__ = ["OFFERED_S", "PREFIX", "PhoneModels", "Work"]

PREFIX = "phone:"
"""A model name ``phone:<phone id>`` asks that phone."""
RUNTIMES = ("litert-lm", "llama.cpp")
OFFERED_S = 45.0
"""A phone that has not polled for this long offers nothing."""
MOST_WAIT_S = 25.0
MOST_QUEUED = 2
MOST_A_MINUTE = 30
MOST_REQUEST = 1 << 20
MOST_MESSAGES = 256
MOST_PIECE = 16 * 1024
MOST_ANSWER = 1 << 20
FIRST_S = 90.0
BETWEEN_S = 90.0
TOTAL_S = 600.0
ROLES = ("system", "user", "assistant")


def _whole(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _offer(said: object) -> dict[str, Any]:
    """The model a phone describes, checked."""
    if not isinstance(said, Mapping):
        raise ApiError(400, "invalid", "Describe the model.")
    name, ident = said.get("name"), said.get("id")
    if (not all(isinstance(each, str) and 0 < len(each) <= 80 and each.isprintable() for each in (name, ident))
            or said.get("runtime") not in RUNTIMES or not _whole(said.get("context"), 256, 1 << 20)
            or not _whole(said.get("max_output"), 1, 8192)):
        raise ApiError(400, "invalid", "A model has an id, a name, a runtime (litert-lm or llama.cpp), a context and a max_output.")
    return {key: said[key] for key in ("id", "name", "runtime", "context", "max_output")}


@dataclass
class Work:
    """One request of the pool to one phone and what has come back of it."""

    id: str
    phone: str
    body: dict[str, Any]
    made: float
    taken: float = 0.0
    heard: float = 0.0
    seq: int = 0
    size: int = 0
    pieces: deque[str] = field(default_factory=deque)
    finished: dict[str, Any] | None = None
    """``{"finish_reason", "usage"}`` of a whole answer, or ``{"error": why}``."""


class PhoneModels:
    def __init__(self, store: Store, clock: Callable[[], float] = time.monotonic) -> None:
        self.store, self.clock = store, clock
        self._offers: dict[str, tuple[dict[str, Any] | None, str, float]] = {}
        self._work: dict[str, Work] = {}
        self._asked: dict[str, list[float]] = {}
        self._changed = threading.Condition()

    def _enabled(self, phone: str) -> bool:
        held = self.store.phones().get(phone)
        return held is not None and held.pool_use

    # -- what a phone offers -------------------------------------------------------
    def state(self, phone: str) -> dict[str, Any]:
        """What the switch and the phone's last poll say: whether it is on, whether the phone offers a model now, and what waits."""
        with self._changed:
            model, blocked, at = self._offers.get(phone, (None, "", float("-inf")))
            fresh = self.clock() - at < OFFERED_S
            enabled = self._enabled(phone)
            return {"enabled": enabled, "offering": bool(enabled and fresh and model and not blocked),
                    "model": model if enabled and fresh else None, "blocked": blocked if fresh else "",
                    "queued": sum(1 for w in self._work.values() if w.phone == phone and w.finished is None)}

    def switch(self, phone: str, on: bool) -> dict[str, Any]:
        self.store.change(phone, pool_use=on)
        self.store.audit(phone, "pool-use", "on" if on else "off")
        if not on:
            self.drop(phone, "The phone was switched out of the pool.")
        return self.state(phone)

    def drop(self, phone: str, why: str) -> None:
        """End everything asked of ``phone``: nothing more goes to it and its askers are told ``why``."""
        with self._changed:
            self._offers.pop(phone, None)
            for work in self._work.values():
                if work.phone == phone and work.finished is None:
                    work.finished = {"error": why}
                    work.body = {}
            self._changed.notify_all()

    # -- the phone's side ----------------------------------------------------------
    def next(self, phone: str, said: Mapping[str, Any]) -> dict[str, Any]:
        """The phone's long poll: it offers its model and is given the oldest request that waits, if any."""
        model = _offer(said.get("model"))
        blocked, wait = said.get("blocked", ""), said.get("wait_s", MOST_WAIT_S)
        if not isinstance(blocked, str) or len(blocked) > 120 or not isinstance(wait, (int, float)) or not 0 <= wait <= MOST_WAIT_S:
            raise ApiError(400, "invalid", "blocked is a short text and wait_s is 0 to 25.")
        until = self.clock() + float(wait)
        with self._changed:
            while True:
                if not self._enabled(phone):
                    self._offers.pop(phone, None)
                    return {"request": None, "enabled": False}
                self._offers[phone] = (model, blocked, self.clock())
                self._expire()
                waiting = [w for w in self._work.values() if w.phone == phone and not w.taken and w.finished is None]
                if waiting and not blocked:
                    work = min(waiting, key=lambda w: w.made)
                    work.taken = work.heard = self.clock()
                    return {"request": {"id": work.id, **work.body}, "enabled": True}
                left = until - self.clock()
                if left <= 0:
                    return {"request": None, "enabled": True}
                self._changed.wait(min(left, 1.0))

    def post(self, phone: str, ident: str, said: Mapping[str, Any]) -> dict[str, Any]:
        """One piece of an answer, in order. ``continue`` False tells the phone to stop."""
        with self._changed:
            work = self._work.get(ident)
            if work is None or work.phone != phone or not work.taken or work.finished is not None or not self._enabled(phone):
                return {"continue": False}
            if said.get("seq") != work.seq:
                raise ApiError(409, "conflict", f"The next post of this request is seq {work.seq}.")
            error, text = said.get("error"), said.get("text", "")
            if error is not None:
                work.finished = {"error": f"The phone could not answer: {str(error)[:200]}"}
            else:
                if not isinstance(text, str) or len(text.encode()) > MOST_PIECE:
                    raise ApiError(413, "too_large", f"A piece of an answer is text of at most {MOST_PIECE} bytes.")
                work.size += len(text.encode())
                if work.size > MOST_ANSWER:
                    work.finished = {"error": "The phone's answer passed the size limit."}
                else:
                    if text:
                        work.pieces.append(text)
                    if said.get("done") is True:
                        usage = said.get("usage") if isinstance(said.get("usage"), Mapping) else {}
                        work.finished = {"finish_reason": "length" if said.get("finish_reason") == "length" else "stop",
                                         "usage": {k: v for k in ("prompt_tokens", "completion_tokens") if _whole(v := usage.get(k), 0, 1 << 30)}}
            work.seq += 1
            work.heard = self.clock()
            if work.finished is not None:
                work.body = {}
            self._changed.notify_all()
            return {"continue": work.finished is None or "error" not in work.finished}

    # -- the pool's side -----------------------------------------------------------
    def ask(self, phone: str, asked: Mapping[str, Any], size: int) -> Work:
        """Queue an OpenAI chat request for ``phone``; `ApiError` when the phone takes none now or the request is not one it can take."""
        if not self._enabled(phone):
            raise ApiError(403, "refused", "That phone is not switched into the pool.")
        state = self.state(phone)
        if not state["offering"]:
            raise ApiError(503, "unavailable", "That phone offers no model now.")
        if size > MOST_REQUEST:
            raise ApiError(413, "too_large", f"A phone takes requests of at most {MOST_REQUEST} bytes.")
        if asked.get("tools") or asked.get("functions"):
            raise ApiError(400, "invalid", "A phone model takes no tools: it answers text only.")
        messages = asked.get("messages")
        if (not isinstance(messages, list) or not 1 <= len(messages) <= MOST_MESSAGES
                or not all(isinstance(m, Mapping) and m.get("role") in ROLES and isinstance(m.get("content"), str) for m in messages)):
            raise ApiError(400, "invalid", f"Give 1 to {MOST_MESSAGES} system, user or assistant messages of text.")
        model = state["model"]
        wanted = asked.get("max_tokens")
        limit = min(wanted, model["max_output"]) if _whole(wanted, 1, 1 << 30) else model["max_output"]
        temperature = asked.get("temperature", 0.7)
        if not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
            raise ApiError(400, "invalid", "temperature is 0 to 2.")
        with self._changed:
            self._expire()
            now = self.clock()
            recent = [t for t in self._asked.get(phone, []) if now - t < 60]
            if len(recent) >= MOST_A_MINUTE or self.state(phone)["queued"] >= MOST_QUEUED:
                raise ApiError(429, "slow_down", "That phone has all the requests it takes now.")
            self._asked[phone] = [*recent, now]
            work = Work("r-" + secrets.token_hex(8), phone, {
                "model": model["id"], "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
                "max_tokens": limit, "temperature": float(temperature), "deadline": time.time() + TOTAL_S}, now)
            self._work[work.id] = work
            self._changed.notify_all()
        return work

    def pieces(self, work: Work) -> Iterator[str]:
        """The answer's text as it arrives; `work.finished` says how it ended. Leaving early cancels the request."""
        try:
            while True:
                with self._changed:
                    self._expire()
                    ready = list(work.pieces)
                    work.pieces.clear()
                    done = work.finished is not None
                    if not ready and not done:
                        if not self._enabled(work.phone):
                            work.finished = {"error": "The phone was switched out of the pool."}
                            continue
                        self._changed.wait(0.5)
                        continue
                yield from ready
                if done:
                    return
        finally:
            with self._changed:
                if work.finished is None:
                    work.finished = {"error": "The asker left."}
                work.body = {}
                self._work.pop(work.id, None)

    def _expire(self) -> None:
        now = self.clock()
        for work in self._work.values():
            if work.finished is not None:
                continue
            late = (now - work.made > TOTAL_S or (not work.taken and now - work.made > FIRST_S)
                    or (work.taken and now - work.heard > (BETWEEN_S if work.seq else FIRST_S)))
            if late:
                work.finished = {"error": "The phone did not answer in time."}
                work.body = {}

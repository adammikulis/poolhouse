"""The one listener a guest reaches. It answers model calls and nothing else: no page, no board, no files, no jobs, none of a model
server's ``/slots``, ``/props`` or ``/metrics``.

Every connection is TLS 1.3 with a certificate of an active guest, matched exactly; there is nothing to call without one. A request
and its reply are held in memory while they pass and are written nowhere: what is kept is counts.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import queue
import socket
import ssl
import threading
import time
import urllib.parse
from collections import deque
from collections.abc import Callable
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler
from typing import Any

from poolhouse import gate
from poolhouse.fleet import tls
from poolhouse.guest import wire
from poolhouse.guest.requests import ROUTES, clean
from poolhouse.guest.store import Record, Store

__all__ = ["PORT", "Gateway"]

PORT = 7453
ANSWER_S = 600.0
CHUNK = 1 << 13
WATCH_S = 0.25
LINE_MOST = 1 << 20


class _Meter:
    """The token counts a model server reports in the reply passing through; only numbers are kept."""

    def __init__(self) -> None:
        self.input = self.output = self.events = 0
        self._line = bytearray()

    def feed(self, block: bytes) -> None:
        self._line += block
        while (end := self._line.find(b"\n")) >= 0:
            self._read(bytes(self._line[:end]))
            del self._line[:end + 1]
        if len(self._line) > LINE_MOST:
            del self._line[:]

    def done(self) -> None:
        self._read(bytes(self._line))
        del self._line[:]

    def _read(self, line: bytes) -> None:
        line = line.strip()
        if line.startswith(b"data:"):
            self.events += 1
            line = line[5:].strip()
        if not line.startswith(b"{"):
            return
        try:
            self._numbers(json.loads(line), 0)
        except (ValueError, RecursionError):
            return

    def _numbers(self, value: Any, depth: int) -> None:
        if not isinstance(value, dict) or depth > 3:
            return
        for key, into in (("prompt_tokens", "input"), ("input_tokens", "input"), ("prompt_n", "input"),
                          ("completion_tokens", "output"), ("output_tokens", "output"), ("predicted_n", "output")):
            if type(value.get(key)) is int:
                setattr(self, into, max(getattr(self, into), value[key]))
        for key in ("usage", "timings", "message", "response"):
            self._numbers(value.get(key), depth + 1)


class Gateway:
    """``engine`` has ``acquire(guest) -> (address, key)``, ``release(guest)``, ``sweep(allowed)`` and ``close()``
    (`poolhouse.guest.engine.PrivateServers`)."""

    def __init__(self, engine: Any, *, store: Store | None = None, address: str = "127.0.0.1", port: int = PORT,
                 turn: Callable[[str], Any] | None = None) -> None:
        self.engine, self.store = engine, store or Store()
        self.identity = self.store.identity()
        self.turn = turn or gate.turn
        self._built: tuple[Any, ssl.SSLContext] | None = None
        self._lock = threading.Lock()
        self._recent: dict[str, deque[float]] = {}
        self._busy: set[str] = set()
        self._closed = threading.Event()
        gateway = self

        class Handler(wire.Quiet):
            def do_GET(self) -> None:
                gateway._get(self)

            def do_POST(self) -> None:
                gateway._post(self)

        self.server = wire.Server(address, port, Handler, self.context)
        self.port = self.server.port
        self._janitor = threading.Thread(target=self._sweeping, daemon=True, name="guest-sweep")

    def context(self) -> ssl.SSLContext:
        """TLS 1.3 that completes only with a certificate of a guest who is active now; made again when the records change."""
        now = time.time()
        guests = [r for r in self.store.records() if r.role == "guest" and r.active(now)]
        stamp = (self.store.stamp(), tuple(r.id for r in guests))
        with self._lock:
            if self._built is None or self._built[0] != stamp:
                context = tls.server_context(self.identity)
                context.verify_mode = ssl.CERT_REQUIRED
                context.verify_flags = (context.verify_flags | ssl.VERIFY_X509_PARTIAL_CHAIN) & ~ssl.VERIFY_X509_STRICT
                if guests:
                    context.load_verify_locations(cadata="".join(ssl.DER_cert_to_PEM_cert(r.der) for r in guests))
                self._built = (stamp, context)
            return self._built[1]

    def start(self) -> None:
        self.server.start()
        self._janitor.start()

    def close(self) -> None:
        self._closed.set()
        self.server.close()
        self.engine.close()

    def _allowed(self, guest: str) -> bool:
        found = self.store.record(guest)
        return found is not None and found.role == "guest" and found.active()

    def _sweeping(self) -> None:
        while not self._closed.wait(1.0):
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                self.engine.sweep(self._allowed)

    def _who(self, handler: BaseHTTPRequestHandler) -> Record | None:
        found = self.store.by_certificate(handler.connection.getpeercert(binary_form=True) or b"", "guest")
        if found is None:
            wire.reply(handler, 403, {"error": "this computer has no guest access here"})
        return found

    def _get(self, handler: BaseHTTPRequestHandler) -> None:
        if self._who(handler) is None:
            return None
        if handler.path != "/v1/models":
            return wire.reply(handler, 404, {"error": "a guest can ask the shared model and nothing else"})
        name = getattr(self.engine, "model", None)
        wire.reply(handler, 200, {"object": "list", "data": [{"id": name.name if name else "shared", "object": "model",
                                                              "context_window": getattr(self.engine, "context", 0)}]})

    def _refuse(self, handler: BaseHTTPRequestHandler, guest: Record, code: int, why: str) -> None:
        self.store.count(guest.id, refused=1)
        wire.reply(handler, code, {"error": {"message": why, "type": "guest_access"}})

    def _admit(self, guest: Record) -> str:
        """Take the guest's one place, or say why not."""
        now = time.monotonic()
        with self._lock:
            recent = self._recent.setdefault(guest.id, deque())
            while recent and now - recent[0] >= 60:
                recent.popleft()
            if guest.id in self._busy:
                return "one request at a time; the last one is still being answered"
            if len(recent) >= guest.limits.requests_per_minute:
                return "too many requests this minute"
            if self.store.counts(guest.id)["tokens_today"] >= guest.limits.tokens_per_day:
                return "today's token limit is used up"
            recent.append(now)
            self._busy.add(guest.id)
        return ""

    def _post(self, handler: BaseHTTPRequestHandler) -> None:
        if (guest := self._who(handler)) is None:
            return
        path = urllib.parse.urlsplit(handler.path).path
        raw = wire.body(handler, guest.limits.request_bytes)   # read before any refusal, so the refusal arrives and not a reset
        if raw is None:
            return self._refuse(handler, guest, 413, "the request is too large, or does not say its length")
        if path not in ROUTES:
            return self._refuse(handler, guest, 404, "a guest can ask the shared model and nothing else")
        if why := self._admit(guest):
            return self._refuse(handler, guest, 429, why)
        try:
            try:
                sent = clean(path, raw, guest.limits.output_tokens_per_request)
            except ValueError as why:
                return self._refuse(handler, guest, 400, str(why))
            self._ask(handler, guest, path, sent)
        finally:
            with self._lock:
                self._busy.discard(guest.id)

    def _ask(self, handler: BaseHTTPRequestHandler, guest: Record, path: str, sent: bytes) -> None:
        try:
            base, key = self.engine.acquire(guest.id)
        except (OSError, RuntimeError, ValueError) as why:   # the guest is told nothing of the host's paths or messages
            said = str(why) if type(why).__name__ == "DoesNotFit" else "the shared model could not be started"
            return self._refuse(handler, guest, 503, said)
        meter, answered = _Meter(), False
        out: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        gone = threading.Event()
        writer = threading.Thread(target=self._send_on, args=(handler, out, gone), daemon=True, name="guest-reply")
        try:
            where = urllib.parse.urlsplit(base)
            upstream = http.client.HTTPConnection(where.hostname or "127.0.0.1", where.port, timeout=ANSWER_S)
            done = threading.Event()
            try:
                # the owner's requests wait for this turn, so it ends when the model has answered, however slowly the guest reads
                with self.turn(base) if ROUTES[path][1] else nullcontext():
                    if not self._allowed(guest.id):
                        return self._refuse(handler, guest, 403, "this computer has no guest access here")
                    upstream.connect()
                    threading.Thread(target=self._watch, args=(guest.id, done, upstream.sock, handler.connection),
                                     daemon=True, name="guest-watch").start()
                    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
                    upstream.request("POST", path, sent, headers)
                    response = upstream.getresponse()
                    handler.send_response(response.status)
                    handler.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                    handler.send_header("Connection", "close")
                    handler.end_headers()
                    answered = True
                    writer.start()
                    while block := response.read1(CHUNK):   # held in memory until sent: at most one bounded reply
                        if gone.is_set() or not self._allowed(guest.id):
                            break
                        meter.feed(block)
                        out.put(block)
            except (OSError, http.client.HTTPException, gate.QueueTimeout) as why:
                # the kind alone: a message may quote the request. Closing the upstream below stops the generation
                wire.logger.info("a guest request ended early: %s", type(why).__name__)
                if not answered:
                    with contextlib.suppress(OSError):
                        wire.reply(handler, 502, {"error": {"message": "the shared model did not answer", "type": "guest_access"}})
            finally:
                upstream.close()
                if answered:
                    out.put(None)
                    writer.join(ANSWER_S)   # `_watch` still ends it when the guest is removed
                done.set()
        finally:
            meter.done()
            self.engine.release(guest.id)
            if answered:   # a reply cut off before its counts came is charged by size, so hanging up early buys nothing
                self.store.count(guest.id, requests=1, input_tokens=meter.input or len(sent) // 4,
                                 output_tokens=meter.output or meter.events)

    @staticmethod
    def _send_on(handler: BaseHTTPRequestHandler, out: queue.SimpleQueue, gone: threading.Event) -> None:
        try:
            while (block := out.get()) is not None:
                handler.wfile.write(block)
                handler.wfile.flush()
        except OSError:
            gone.set()   # the guest hung up: the upstream is closed, which stops the generation

    def _watch(self, guest: str, done: threading.Event, *sockets: socket.socket) -> None:
        """End a request as soon as its guest is removed, also while the model is still reading the prompt."""
        while not done.wait(WATCH_S):
            if self._closed.is_set() or not self._allowed(guest):
                for one in sockets:
                    with contextlib.suppress(OSError):
                        socket.socket.shutdown(one, socket.SHUT_RDWR)
                return

"""A browser that cannot reach this machine or its network: every connection it makes goes through a proxy on a loopback port of
its own, loopback addresses included. The proxy resolves the name once, refuses it unless every address is public and is neither
one of this machine's nor on a network one of its interfaces is on (`allowed`, the rule of every guarded fetch) and connects to
the address it checked, so a first address, a redirect, a form, either socket API and a
name whose answer changes are all held to that one rule, whatever in the page asked. What a proxy cannot carry, datagrams, is taken
away, and downloads are off. The proxy serves only the browser it was made for, which holds a password made for that one guard:
another program on this machine, one confined to loopback among them, gets a 407 and goes nowhere."""

from __future__ import annotations

import json
import logging
import re
import secrets
import selectors
import socket
import sys
from typing import Any

from poolhouse.httpguard import Limits, Refused, resolve, split
from poolhouse.isolation import wire
from poolhouse.isolation.serving import IDLE_SECONDS, Server

__all__ = ["Guard", "allowed"]

logger = logging.getLogger(__name__)

UNPROXIED = ("RTCPeerConnection", "webkitRTCPeerConnection", "WebTransport", "Worker", "SharedWorker")
"""What could send datagrams, which no proxy carries; workers are among them because `_REMOVE` does not run inside one."""
_REMOVE = ("for (const name of " + json.dumps(UNPROXIED) + ") "
           "Object.defineProperty(globalThis, name, {value: undefined, writable: false, configurable: false});")
FLAGS = ("--webrtc-ip-handling-policy=disable_non_proxied_udp",)
"""WebRTC sends no datagram around the proxy and its connections go through it, from a frame the script above has not reached
either."""
AUTHORITY = re.compile(r"(?:\[[0-9A-Fa-f:.]+\]|[^\[\]/?#@:\\]+):[0-9]{1,5}")
HOP = frozenset({"connection", "keep-alive", "proxy-connection", "proxy-authorization"})
"""Header fields of one connection, which a proxy does not pass on."""
MOST_ANSWER_HEAD = 4 * wire.MAX_HEAD
"""The most bytes an answer's head may have: Chromium's own limit on one."""
USER = "poolhouse"
HEAD_SECONDS = 10.0
"""The most seconds a connection may take to send the head of its request, which holds the password."""


def allowed(url: str, limits: Limits | None = None) -> list[str]:
    """The addresses the browser may connect to for the http(s) ``url``: those its host names when every one is public and
    none is this machine's or on its network (`httpguard.resolve`), and none otherwise."""
    try:
        _parts, host, port = split(url)
        return resolve(host, port, limits or Limits())
    except Refused:
        return []


class Guard:
    """The proxy of one browser, open for a ``with`` block. Launch the browser with `options`, `install` the context before its
    first page, and read ``refused`` for the hosts and ports it was stopped from reaching."""

    def __init__(self, limits: Limits | None = None) -> None:
        self.limits = limits or Limits()
        self.refused: list[str] = []
        self.port = 0
        self.password = secrets.token_urlsafe(32)
        """What the browser presents with every connection; it reaches the browser through Playwright, never on a command line."""
        self._expected = wire.basic(USER, self.password)
        self._server = Server(socket.AF_INET, ("127.0.0.1", 0), self._connection)

    def __enter__(self) -> Guard:
        self.port = int(self._server.start()[1])
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.stop()

    def options(self, args: list[str]) -> dict[str, Any]:
        """The launch options that hold a browser started with ``args`` to this proxy. Chromium sends loopback addresses past a
        proxy unless ``<-loopback>`` says otherwise."""
        proxy = {"server": f"http://127.0.0.1:{self.port}", "bypass": "<-loopback>", "username": USER, "password": self.password}
        return {"proxy": proxy, "args": [*args, *FLAGS], "accept_downloads": False,
                "service_workers": "block"}   # `_REMOVE` does not run inside one either

    def install(self, context: Any) -> None:
        """Take `UNPROXIED` from every frame of ``context``."""
        context.add_init_script(_REMOVE)

    def _connection(self, client: socket.socket) -> None:
        """One request of the browser's: a tunnel (https and both socket APIs), or one plain http exchange."""
        client.settimeout(IDLE_SECONDS)
        buffer = bytearray()
        try:
            head = wire.read_head(client, buffer, tunnel=True, within=HEAD_SECONDS)
            if not wire.presented(head, self._expected):
                wire.answer(client, 407, b"", extra=(wire.CHALLENGE,))
            elif head.method == "CONNECT":
                self._tunnel(client, head.target, bytes(buffer))
            else:
                self._exchange(client, head, bytes(buffer))
        except wire.Refusal as why:
            wire.answer(client, why.status, b"")   # empty, so the browser shows an error and no page of the proxy's

    def _open(self, url: str) -> socket.socket:
        """A connection for ``url`` to an address `allowed` gave: nothing is looked up a second time."""
        addresses = allowed(url, self.limits)
        if not addresses:
            where = url.partition("://")[2].partition("/")[0]
            self.refused.append(where)
            logger.warning("the browser was stopped from reaching %s", where)
            raise wire.Refusal(403, "that address is not one the browser may reach")
        port = split(url)[2]
        for address in addresses:
            try:
                made = socket.create_connection((address, port), timeout=self.limits.timeout)
            except OSError:
                continue
            made.settimeout(IDLE_SECONDS)
            return made
        raise wire.Refusal(502, "the host did not answer")

    def _tunnel(self, client: socket.socket, target: str, first: bytes) -> None:
        if not AUTHORITY.fullmatch(target):
            raise wire.Refusal(400, "a tunnel names a host and a port")
        with self._open(f"http://{target}") as upstream:
            if wire.send(client, b"HTTP/1.1 200 Connection established\r\n\r\n") and (not first or wire.send(upstream, first)):
                _relay({client: upstream, upstream: client})

    def _exchange(self, client: socket.socket, head: wire.Head, first: bytes) -> None:
        """Send one request on and its answer back, then close: a second request on the connection is never sent anywhere, so
        every request is checked on a connection of its own."""
        if not head.target.lower().startswith("http://"):
            raise wire.Refusal(400, "only a plain http address is asked for this way")
        left = wire.length_of(head, sys.maxsize)
        with self._open(head.target) as upstream:
            parts = split(head.target)[0]
            lines = [f"{head.method} {parts.path or '/'}{'?' + parts.query if parts.query else ''} HTTP/1.1",
                     f"Host: {parts.netloc}",
                     *(f"{name}: {value}" for name, value in head.fields if name not in HOP and name != "host"),
                     "Connection: close"]
            data = first[:left]
            if not wire.send(upstream, ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + data):
                raise wire.Refusal(502, "the host did not answer")
            left -= len(data)
            while left:
                data = client.recv(min(left, wire.MAX_HEAD))
                if not data or not wire.send(upstream, data):
                    return
                left -= len(data)
            if _pass_head(client, upstream):
                _relay({upstream: client, client: None})


def _pass_head(client: socket.socket, upstream: socket.socket) -> bool:
    """Pass on the head of the answer, saying the connection closes after it; interim answers go as they are. False when the
    exchange is over."""
    buffer = bytearray()
    while True:
        while (end := buffer.find(b"\r\n\r\n")) < 0:
            data = upstream.recv(wire.MAX_HEAD)
            if not data or len(buffer) > MOST_ANSWER_HEAD:
                wire.answer(client, 502, b"")
                return False
            buffer.extend(data)
        status, *fields = bytes(buffer[:end]).split(b"\r\n")
        del buffer[:end + 4]
        words = status.split(b" ")
        interim = len(words) > 1 and words[1].startswith(b"1") and words[1] != b"101"
        if not interim:
            fields = [line for line in fields if line.partition(b":")[0].strip().lower().decode("latin-1") not in HOP]
            fields.append(b"Connection: close")
        if not wire.send(client, b"\r\n".join([status, *fields]) + b"\r\n\r\n"):
            return False
        if not interim:
            return not buffer or bool(wire.send(client, bytes(buffer)))


def _relay(routes: dict[socket.socket, socket.socket | None]) -> None:
    """Copy what each socket of ``routes`` sends to the socket it names, until one ends, is silent for `IDLE_SECONDS`, or sends
    where it names none."""
    with selectors.DefaultSelector() as watch:
        for source, sink in routes.items():
            watch.register(source, selectors.EVENT_READ, sink)
        while ready := watch.select(IDLE_SECONDS):
            for key, _events in ready:
                data = key.fileobj.recv(wire.MAX_HEAD)  # type: ignore[union-attr]
                if not data or key.data is None or not wire.send(key.data, data):
                    return
